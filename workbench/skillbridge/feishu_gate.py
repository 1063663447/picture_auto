"""飞书多维表格当「全局并发闸门 + 队列」（槽位抢占版）。

为什么不是「一行一个任务、数行数」：

    真机实测（2 台机器、12 张图、额度 6）——数行数的做法**会超发到 12**。
    原因是飞书 `records/search` 是最终一致的：刚写的行 1~2 秒内可能查不到，
    多个客户端同时读 → 都以为「还有空位」→ 一起写 → 一起放行。

所以改成**固定槽位 + 原子抢占**：

    表里预置 N 行（N = 并发额度，默认 15），每行就是一个「坑」，字段：
        占位（槽位名 01..15） / 租约（谁占的，空=空闲） / 主机 / 开始 / 说明

    抢坑 = 「按槽位顺序找一个空闲行 → 写我的租约 → 回读这一行确认还是我的」
    同一行上只能留一个人的租约（Feishu 单行更新是原子的），
    所以**同时在跑的数量在结构上不可能超过 N**，跟搜索索引延迟无关。

    - 抢不到（没有空行）→ 进队列轮询，别人还了就能抢到；
    - 写完 `settle_ms`（默认 1.2s）再回读确认（缓冲，防索引延迟误判），
      `confirmations`（默认 2）次都一致才算拿到；
    - 行里的 `开始` 超过 `stale_sec`（默认 10 分钟）视为崩溃遗留，可以直接抢；
    - `release` = 把行里的租约清空（**不删行**），表永远是 N 行。

这样每张图的闸门开销是「读 + 改 + 回读」，没有增删行带来的索引风暴。
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .gate import GateBusy, GateConfig, GateStatus, Lease

DEFAULT_LIMIT = 15
STALE_SEC = 600.0
SETTLE_MS = 1200
POLL_MS = 2500

# 闸门表的字段（槽位表：一行一个坑）
GATE_FIELDS = [
    ("占位", 1),        # 主字段：槽位名 01 / 02 / ...
    ("租约", 1),        # 空 = 空闲；非空 = 谁占着（也用来做抢占确认）
    ("主机", 1),
    ("开始", 2),        # 毫秒时间戳（判断僵尸占位）
    ("说明", 1),
]


def _text(item: dict, name: str) -> str:
    value = (item.get("fields") or {}).get(name)
    if isinstance(value, list):
        parts = []
        for piece in value:
            if isinstance(piece, dict):
                parts.append(str(piece.get("text") or ""))
            else:
                parts.append(str(piece))
        return "".join(parts)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("value") or "")
    return "" if value is None else str(value)


def _num(item: dict, name: str) -> float:
    value = (item.get("fields") or {}).get(name)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class Slot:
    record_id: str
    name: str = ""
    lease: str = ""
    holder: str = ""
    started: float = 0.0
    note: str = ""
    created: float = 0.0        # 服务端创建的毫秒时间戳（排序用，取不到就退回 开始）

    @classmethod
    def from_item(cls, item: dict) -> "Slot":
        started = _num(item, "开始")
        created = _num(item, "created_time")
        return cls(
            record_id=str(item.get("record_id") or ""),
            name=_text(item, "占位").strip(),
            lease=_text(item, "租约").strip(),
            holder=_text(item, "主机").strip(),
            started=started / 1000.0 if started > 1e11 else started,
            note=_text(item, "说明"),
            created=created / 1000.0 if created > 1e11 else created,
        )

    def order_key(self) -> float:
        """排队先后：优先用服务端 created_time，取不到用 开始。"""
        return self.created or self.started or 0.0

    def index(self) -> int:
        digits = "".join(ch for ch in self.name if ch.isdigit())
        return int(digits) if digits else 0

    def busy(self, now: float, stale_sec: float) -> bool:
        if not self.lease:
            return False
        return now - (self.started or now) <= stale_sec


class FeishuGate:
    """一张飞书表当并发闸门（槽位抢占）。client 需实现 search/update/create。"""

    def __init__(self, cfg: GateConfig, client, label: str = "") -> None:
        self.cfg = cfg
        self.client = client
        self.label = label
        self.limit = max(1, int(cfg.limit or DEFAULT_LIMIT))
        self.table_id = (cfg.table_id or "").strip()
        self.settle_ms = max(0, int(cfg.settle_ms or SETTLE_MS))
        self.stale_sec = max(60.0, float(cfg.stale_sec or STALE_SEC))
        self.poll_ms = max(300, int(cfg.poll_ms or POLL_MS))
        self.confirmations = max(1, int(getattr(cfg, "confirmations", 2) or 2))
        self.last_error = ""
        # 同一台机器内串行「读→抢」，减少自己人内耗
        self._lock = asyncio.Lock()
        self._slots_ready = False
        # 本进程取过的号（record_id）：算「我前面还有几张」要用
        self._own_tickets: set[str] = set()
        # 本进程当前持有的租约：飞书 search 是最终一致的，刚写的租约可能读不到，
        # 光信表就会「全以为有空位」→ 一起放行。本机这几个自己最清楚，用它兜底，
        # 超过额度就老老实实取号排队（否则界面上永远看不到排队）。
        self._local_leases: set[str] = set()
        # 正在「读→抢」过程中的本机名额（抢到就转成 _local_leases）
        self._local_reserved = 0
        self.stats = {
            "acquired": 0, "released": 0, "queue_rounds": 0,
            "waited_total": 0.0, "waited_max": 0.0,
            "lost_race": 0, "reclaimed_stale": 0,
            "tickets": 0, "ticket_waits": 0,
            "reads": 0, "writes": 0,
        }


    # ------------------------------------------------------------- 基础读写 #
    def _rows(self) -> list[Slot]:
        if not self.table_id:
            raise RuntimeError(
                "没配闸门表 table_id（跑 python -m skillbridge.feishu_gate --init 建表）"
            )
        self.stats["reads"] += 1
        try:
            items = self.client.search_table(
                self.table_id, page_size=500, max_pages=4, automatic=True
            )
        except TypeError:  # 老客户端不支持 automatic 参数
            items = self.client.search_table(self.table_id, page_size=500, max_pages=4)
        slots = [Slot.from_item(item) for item in items]
        return [slot for slot in slots if slot.record_id]

    def _ensure_slots(self, rows: list[Slot]) -> list[Slot]:
        """表里必须有 01..limit 这些槽位行；缺了就补（每个进程只补一次）。"""
        if self._slots_ready:
            return rows
        have = {slot.index() for slot in rows}
        missing = [i for i in range(1, self.limit + 1) if i not in have]
        if missing:
            self.client.create_records(
                [{"占位": f"{i:02d}", "租约": "", "主机": "", "开始": 0,
                  "说明": "槽位（占位行，别手动删）"} for i in missing],
                self.table_id,
            )
            self.stats["writes"] += len(missing)
            rows = self._rows()
        self._slots_ready = True
        return rows

    def _claim(self, slot: Slot, lease: str, job: str, task: str, note: str) -> None:
        self.client.update_record(
            self.table_id,
            slot.record_id,
            {
                "租约": lease,
                "主机": self.cfg.holder[:60],
                "开始": int(time.time() * 1000),
                "说明": (note or f"{job}/{task}")[:200],
            },
        )
        self.stats["writes"] += 1

    # ------------------------------------------------------------ 排队号 --
    TICKET_PREFIX = "Q"

    def _tickets(self, rows: list[Slot]) -> list[Slot]:
        return [row for row in rows if row.name.startswith(self.TICKET_PREFIX)]

    def _make_ticket(self, job: str, task: str) -> str:
        """没空位时先取个号（一行），谁先取号谁先进。"""
        try:
            ids = self.client.create_records(
                [{
                    "占位": f"{self.TICKET_PREFIX}{int(time.time() * 1000)}-{random.randrange(100, 999)}",
                    "租约": f"wait|{self.cfg.holder}|{random.randrange(100000, 999999)}",
                    "主机": self.cfg.holder[:60],
                    "开始": int(time.time() * 1000),
                    "说明": f"排队 {job}/{task}"[:200],
                }],
                self.table_id,
            )
            self.stats["writes"] += 1
            self.stats["tickets"] += 1
            if ids:
                self._own_tickets.add(str(ids[0]))
            return str(ids[0]) if ids else ""
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"取号失败：{exc}"
            return ""

    def _drop_ticket(self, record_id: str) -> None:
        if not record_id:
            return
        self._own_tickets.discard(record_id)
        try:
            self.client.delete_record(self.table_id, record_id)
            self.stats["writes"] += 1
        except Exception:  # noqa: BLE001
            pass

    def _queue_info(
        self, rows: list[Slot], ticket_id: str, in_use: int, waited: float = 0.0
    ) -> dict:
        """排队位置：**前方还有几张**（正在跑 + 排在我前面的）。

        这是界面上「前方还有 xx 个任务」的唯一来源：数字全部来自飞书表里的实时行，
        所以别的电脑插进来排队也能算进去。
        """
        tickets = sorted(self._tickets(rows), key=lambda slot: (slot.order_key(), slot.record_id))
        queued = len(tickets)
        ahead_in_queue = queued
        if ticket_id:
            ids = [slot.record_id for slot in tickets]
            if ticket_id in ids:
                ahead_in_queue = ids.index(ticket_id)
            # 刚取号还没出现在表里（索引延迟）：至少算我自己这张
            queued = max(queued, 1)
        elif not queued:
            ahead_in_queue = 0
        return {
            "in_use": int(in_use),
            "limit": self.limit,
            "queued": queued,
            "ahead_in_queue": ahead_in_queue,
            "ahead": int(in_use) + ahead_in_queue,
            "waited": round(waited, 1),
        }

    def _my_turn(self, rows: list[Slot], ticket_id: str) -> bool:
        """我是不是排队最前面的那个（先到先得）。"""
        if not ticket_id:
            return True
        tickets = sorted(self._tickets(rows), key=lambda slot: (slot.order_key(), slot.record_id))
        if not tickets:
            return True
        return tickets[0].record_id == ticket_id

    def _read_one(self, record_id: str) -> Slot | None:
        """读一条槽位。优先单条 GET（便宜），客户端不支持时退回整表搜索。"""
        if not record_id:
            return None
        getter = getattr(self.client, "get_record", None)
        if callable(getter):
            try:
                return Slot.from_item(getter(self.table_id, record_id))
            except Exception:  # noqa: BLE001 - 退回整表
                pass
        for slot in self._rows():
            if slot.record_id == record_id:
                return slot
        return None

    async def _verify(self, record_id: str, lease: str, tries: int = 3) -> Slot | None:
        """回读确认这一行还是我的。

        飞书搜索是最终一致的：刚写完可能还读到旧值。所以这里**重试几次**（每次间隔
        一小段 + 等索引），而不是读一次不一致就判失败（那会变成互相让位的活锁）。
        """
        gap = max(150, self.settle_ms // 2) / 1000.0
        for attempt in range(max(1, tries)):
            try:
                slot = await asyncio.to_thread(self._read_one, record_id)
            except Exception:  # noqa: BLE001
                slot = None
            if slot is not None and slot.lease == lease:
                return slot
            if attempt + 1 < tries:
                await asyncio.sleep(gap)
        return None

    # ------------------------------------------------------------- acquire #
    async def acquire(
        self, *, job: str = "", task: str = "", note: str = "", on_wait=None
    ) -> Lease | None:
        """占一个槽位。没有空位就取号排队（先到先得）。"""
        if not self.cfg.enabled:
            return None
        started = time.monotonic()
        deadline = started + max(5.0, float(self.cfg.wait_timeout_sec or 1800))
        # 排队号是「这一次 acquire」的状态，放局部变量（同一个 gate 会被多个任务共用）
        ticket = ""
        attempt = 0
        last_note = 0.0
        try:
            while True:
                attempt += 1
                try:
                    rows = await asyncio.to_thread(self._rows)
                    rows = await asyncio.to_thread(self._ensure_slots, rows)
                except Exception as exc:  # noqa: BLE001
                    self.last_error = f"读闸门表失败：{exc}"
                    if self.cfg.fail_open:
                        return None
                    raise

                now = time.time()
                usables = sorted(
                    (slot for slot in rows if 1 <= slot.index() <= self.limit),
                    key=lambda slot: slot.index(),
                )
                free = [slot for slot in usables if not slot.busy(now, self.stale_sec)]
                my_turn = self._my_turn(rows, ticket)
                # 本机自己占着 / 正在占的几张（飞书读不到最新租约，这几张只有自己最清楚）
                local_busy = len(self._local_leases) + self._local_reserved
                in_use_now = max(
                    sum(1 for slot in usables if slot.busy(now, self.stale_sec)), local_busy
                )

                if not free or not my_turn or local_busy >= self.limit:
                    # 没空位、或者有空位但前面还有人排队、或者本机额度已满 → 取号/继续等
                    if not ticket:
                        ticket = await asyncio.to_thread(self._make_ticket, job, task)
                    wait_now = time.monotonic()
                    if wait_now >= deadline:
                        self.last_error = "排队超时"
                        raise GateBusy(
                            f"飞书闸门排队超过 {int(self.cfg.wait_timeout_sec)}s"
                            f"（{self.limit} 个槽位全被占满）"
                        )
                    self.stats["queue_rounds"] += 1
                    if free:
                        self.stats["ticket_waits"] += 1
                    if on_wait and (wait_now - last_note > 5 or attempt == 1):
                        last_note = wait_now
                        info = self._queue_info(rows, ticket, in_use_now, wait_now - started)
                        try:
                            on_wait(info)
                        except Exception:  # noqa: BLE001
                            pass
                    await asyncio.sleep((self.poll_ms / 1000.0) * random.uniform(0.75, 1.25))
                    continue

                # 有空位 + 轮到我 → 抢
                lease = (
                    f"{self.cfg.holder}|{int(time.time() * 1000)}|"
                    f"{random.randrange(100000, 999999)}"
                )
                claimed: Slot | None = None
                candidates = list(free)
                random.shuffle(candidates)      # 打散：别都去抢 01 号坑
                wrote_id = ""
                reserved = False
                async with self._lock:
                    # 锁里只做「写占位」，等索引 + 回读放到锁外；
                    # 但「判断还有没有本地额度」必须在锁内做，否则几个任务同时读到
                    # 「3 个空位」→ 一起抢同一批槽位 → 超发（真机踩过：limit=3 跑出 5）
                    if len(self._local_leases) + self._local_reserved >= self.limit:
                        continue
                    self._local_reserved += 1
                    reserved = True
                try:
                    async with self._lock:
                        for slot in candidates:
                            try:
                                await asyncio.to_thread(self._claim, slot, lease, job, task, note)
                                wrote_id = slot.record_id
                                break
                            except Exception as exc:  # noqa: BLE001
                                self.last_error = f"写闸门表失败：{exc}"
                    if wrote_id:
                        if self.settle_ms:
                            await asyncio.sleep(self.settle_ms / 1000.0)
                        claimed = await self._verify(wrote_id, lease)
                        if claimed is None:
                            # 被别的机器抢走了（后写覆盖）→ 下一轮换坑
                            self.stats["lost_race"] += 1
                finally:
                    if claimed is not None:
                        self._local_leases.add(claimed.record_id)
                    if reserved:
                        self._local_reserved -= 1

                if claimed is None:
                    await asyncio.sleep((self.poll_ms / 1000.0) * random.uniform(0.5, 0.9))
                    continue

                # 二次确认（confirmations>1 时）
                for _ in range(max(0, self.confirmations - 1)):
                    again = await self._verify(claimed.record_id, lease, tries=2)
                    if again is None:
                        claimed = None
                        break
                if claimed is None:
                    self._local_leases.discard(wrote_id)
                    self.stats["lost_race"] += 1
                    await asyncio.sleep((self.poll_ms / 1000.0) * random.uniform(0.5, 0.9))
                    continue

                if ticket:
                    mine = ticket
                    ticket = ""
                    await asyncio.to_thread(self._drop_ticket, mine)
                waited = time.monotonic() - started
                self.stats["acquired"] += 1
                self.stats["waited_total"] += waited
                self.stats["waited_max"] = max(self.stats["waited_max"], waited)
                self.last_error = ""
                self._local_leases.add(claimed.record_id)
                busy_now = max(
                    sum(1 for slot in usables if slot.busy(now, self.stale_sec)),
                    len(self._local_leases),
                )
                return Lease(
                    id=claimed.record_id,
                    limit=self.limit,
                    in_use=min(busy_now, self.limit),
                    waited=waited,
                    holder=self.cfg.holder,
                    lease_token=lease,
                )
        finally:
            # 没抢到 / 超时 / 异常 → 把号还回去，别堵住后面的人
            if ticket:
                await asyncio.to_thread(self._drop_ticket, ticket)

    # ------------------------------------------------------------- release #
    async def release(self, lease: Lease | None) -> None:
        if not lease or not lease.id:
            return
        self._local_leases.discard(lease.id)
        try:
            await asyncio.to_thread(
                self.client.update_record,
                self.table_id,
                lease.id,
                {"租约": "", "主机": "", "开始": 0, "说明": ""},
            )
            self.stats["released"] += 1
            self.stats["writes"] += 1
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"释放槽位失败：{exc}"

    # -------------------------------------------------------------- status #
    async def status(self) -> GateStatus:
        if not self.cfg.enabled:
            return GateStatus(ok=False, enabled=False, limit=self.limit,
                              error="未启用", checked_at=time.time())
        try:
            rows = await asyncio.to_thread(self._rows)
        except Exception as exc:  # noqa: BLE001
            return GateStatus(ok=False, enabled=True, limit=self.limit,
                              error=str(exc)[:160], checked_at=time.time(),
                              extra={"provider": "feishu", "table_id": self.table_id})
        now = time.time()
        usables = [slot for slot in rows if 1 <= slot.index() <= self.limit]
        busy = [slot for slot in usables if slot.busy(now, self.stale_sec)]
        in_use = max(len(busy), len(self._local_leases))
        # 队列：还有多少张在排队、我前面还有几张（全是飞书表里的实时行）
        tickets = sorted(self._tickets(rows), key=lambda slot: (slot.order_key(), slot.record_id))
        mine = [slot for slot in tickets if slot.record_id in self._own_tickets]
        ahead = len(busy)
        if mine:
            first = mine[0]
            ahead += sum(1 for slot in tickets if slot.order_key() < first.order_key())
        elif tickets:
            ahead += len(tickets)
        return GateStatus(
            ok=True,
            enabled=True,
            in_use=in_use,
            limit=self.limit,
            checked_at=now,
            extra={
                "provider": "feishu",
                "table_id": self.table_id,
                "slots": len(usables),
                "queued": len(tickets),
                "ahead": max(ahead, in_use),
                "holders": [slot.holder for slot in busy][:20],
                "holder": self.cfg.holder,
                "stats": {k: (round(v, 2) if isinstance(v, float) else v)
                          for k, v in self.stats.items()},
            },
        )

    async def close(self) -> None:
        return None


# ------------------------------------------------------------------ 建表工具 #
def ensure_gate_table(app, table_id: str = "", limit: int = DEFAULT_LIMIT) -> str:
    """建好闸门表并预置 limit 个槽位行；已存在就补字段 / 补槽位。"""
    app_token = app.cfg.app_token
    if not table_id:
        data = app._api(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables",
            {"table": {"name": "并发闸门", "default_view_name": "槽位",
                       "fields": [{"field_name": name, "type": ftype}
                                  for name, ftype in GATE_FIELDS]}},
        )
        table_id = str(data.get("table_id") or "")
        if not table_id:
            raise RuntimeError("建表失败：飞书没返回 table_id")
    else:
        have = {
            str(item.get("field_name")): int(item.get("type") or 0)
            for item in app._api(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields?page_size=100",
            ).get("items") or []
        }
        for name, ftype in GATE_FIELDS:
            if name in have:
                continue
            app._api(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                {"field_name": name, "type": ftype},
            )

    existing = {Slot.from_item(item).index()
                for item in app.search_table(table_id, page_size=500)}
    missing = [i for i in range(1, max(1, limit) + 1) if i not in existing]
    if missing:
        app.create_records(
            [{"占位": f"{i:02d}", "租约": "", "主机": "", "开始": 0,
              "说明": "槽位（占位行，别手动删）"} for i in missing],
            table_id,
        )
    return table_id


def main(argv: list[str] | None = None) -> int:
    """python -m skillbridge.feishu_gate --init / --check"""
    import argparse

    from . import feishu as feishu_mod
    from .config import load_config, load_env

    parser = argparse.ArgumentParser(description="飞书并发闸门表（槽位队列）")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--init", action="store_true", help="建表 / 补字段 / 补槽位")
    parser.add_argument("--limit", type=int, default=0, help="槽位数（默认读 config 或 15）")
    parser.add_argument("--check", action="store_true", help="看当前占位情况")
    args = parser.parse_args(argv)

    env = load_env()
    config = load_config(Path(args.config), env)
    fc = feishu_mod.load_config(config, env)
    ready, reason = fc.ready()
    if not ready:
        print("飞书未配置：", reason)
        return 2
    app = feishu_mod.FeishuBitable(fc)
    gate_cfg = (config.get("defaults") or {}).get("gate") or {}
    table_id = str(gate_cfg.get("table_id") or (config.get("feishu") or {}).get("gate_table_id") or "")
    limit = args.limit or int(gate_cfg.get("limit") or DEFAULT_LIMIT)

    if args.init:
        table_id = ensure_gate_table(app, table_id, limit)
        print("闸门表 table_id =", table_id, f"（{limit} 个槽位）")
        print("把这一行加进 config.json：")
        print(json.dumps({"feishu": {"gate_table_id": table_id}}, ensure_ascii=False))
        return 0

    if not table_id:
        print("还没建表：先跑 python -m skillbridge.feishu_gate --init")
        return 1
    rows = [Slot.from_item(item) for item in app.search_table(table_id, page_size=500)]
    now = time.time()
    usables = sorted((s for s in rows if 1 <= s.index() <= limit), key=lambda s: s.index())
    busy = [s for s in usables if s.busy(now, STALE_SEC)]
    stale = [s for s in usables if s.lease and not s.busy(now, STALE_SEC)]
    print(f"闸门表 {table_id}（{len(usables)} 个槽位，上限 {limit}）")
    print(f"  占用：{len(busy)} · 空闲：{len(usables) - len(busy)} · 僵尸：{len(stale)}")
    for slot in usables:
        if slot.lease:
            age = int(now - (slot.started or now))
            flag = "僵尸" if age > STALE_SEC else ""
            print(f"   [{slot.name}] {slot.holder or '?'}  已占 {age}s  {slot.note[:40]} {flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
