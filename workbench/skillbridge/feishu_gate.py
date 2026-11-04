"""飞书多维表格当「全局并发闸门 + 队列」。

思路（用户提的方案，比 Cloudflare 更省事：用他们已经在用的飞书表）：

    每要发一张图 → 往「闸门表」写一条占位记录
    占位记录数 < limit（默认 15）→ 直接调接口；出图成功/失败后**删掉这条记录**
    占位记录数 ≥ limit      → 轮询等待（别人删了就能进）

为什么这样能挡住并发：所有电脑都读写同一张表，表的行数就是「当前在跑的张数」。
飞书的读写对所有人是同一个真相源，所以不需要额外的服务。

两个必须处理的坑：
1. **飞书索引有延迟**：刚写的记录可能不会立刻出现在搜索结果里。所以写完之后要
   等 `settle_ms`（默认 1.2s）再**数一遍**，超过 limit 就把自己这条删掉重排 —— 这就是「二次检测 + 缓冲」。
2. **进程崩了会留下僵尸占位**：每条记录带 `开始时间`，超过 `stale_sec`（默认 10 分钟）
   的记录视为崩溃遗留，任何客户端发现就顺手删掉。

对比 `gate.py` 里的 HTTP 阀门：接口一致（acquire/release/status），runner 不用改。
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

# 闸门表的字段（都是最简单的类型：文本 + 数字）
GATE_FIELDS = [
    ("占位", 1),        # 主字段：job/task
    ("主机", 1),        # 哪台电脑占的
    ("说明", 1),        # 款式/颜色等，方便人肉看
    ("开始", 2),        # 毫秒时间戳，用来判断僵尸占位
    ("租约", 1),        # 客户端自己的 lease 串，防止误删别人的行
]


def _field_text(item: dict, name: str) -> str:
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


def _field_num(item: dict, name: str) -> float:
    value = (item.get("fields") or {}).get(name)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class GateRow:
    record_id: str
    holder: str = ""
    started: float = 0.0
    lease: str = ""
    label: str = ""

    @classmethod
    def from_item(cls, item: dict) -> "GateRow":
        started_ms = _field_num(item, "开始")
        return cls(
            record_id=str(item.get("record_id") or ""),
            holder=_field_text(item, "主机"),
            started=started_ms / 1000.0 if started_ms > 1e11 else started_ms,
            lease=_field_text(item, "租约"),
            label=_field_text(item, "占位"),
        )


class FeishuGate:
    """用一张飞书表格当并发闸门。client 需实现 search_table/create_records/delete_record。"""

    def __init__(self, cfg: GateConfig, client, label: str = "") -> None:
        self.cfg = cfg
        self.client = client
        self.label = label
        self.limit = max(1, int(cfg.limit or DEFAULT_LIMIT))
        self.table_id = (cfg.table_id or "").strip()
        self.settle_ms = max(0, int(cfg.settle_ms or SETTLE_MS))
        self.stale_sec = max(60.0, float(cfg.stale_sec or STALE_SEC))
        self.poll_ms = max(300, int(cfg.poll_ms or POLL_MS))
        self.last_error = ""
        self._cleaned: set[str] = set()

    # ------------------------------------------------------------- 基础读写 #
    def _rows(self) -> list[GateRow]:
        if not self.table_id:
            raise RuntimeError("没配闸门表 table_id（feishu.gate_table_id 或 defaults.gate.table_id）")
        items = self.client.search_table(self.table_id, page_size=500, max_pages=4)
        rows = [GateRow.from_item(item) for item in items]
        return [row for row in rows if row.record_id]

    def _active(self, rows: list[GateRow], *, now: float | None = None) -> list[GateRow]:
        now = now or time.time()
        return [row for row in rows if now - (row.started or now) <= self.stale_sec]

    def _stale(self, rows: list[GateRow], *, now: float | None = None) -> list[GateRow]:
        now = now or time.time()
        return [row for row in rows if row.started and now - row.started > self.stale_sec]

    def _create(self, lease: str, job: str, task: str, note: str) -> str:
        rows = [
            {
                "占位": f"{job}/{task}"[:200] or "task",
                "主机": self.cfg.holder[:60],
                "说明": (note or self.label or "")[:200],
                "开始": int(time.time() * 1000),
                "租约": lease,
            }
        ]
        ids = self.client.create_records(rows, self.table_id)
        return str(ids[0]) if ids else ""

    def _delete(self, record_ids: list[str]) -> None:
        for record_id in record_ids:
            if not record_id:
                continue
            try:
                self.client.delete_record(self.table_id, record_id)
            except Exception:  # noqa: BLE001 - 删不掉就等它过期
                pass

    def _cleanup_stale(self, rows: list[GateRow]) -> int:
        stale = [row for row in self._stale(rows) if row.record_id not in self._cleaned]
        if not stale:
            return 0
        for row in stale:
            self._cleaned.add(row.record_id)
        asyncio.get_event_loop().run_in_executor(
            None, self._delete, [row.record_id for row in stale]
        )
        return len(stale)

    # ------------------------------------------------------------- acquire #
    async def acquire(self, *, job: str = "", task: str = "", note: str = "", on_wait=None) -> Lease | None:
        """占一个坑。返回 Lease(record_id)；阀门不可用且 fail_open 时返回 None。"""
        if not self.cfg.enabled:
            return None
        started = time.monotonic()
        deadline = started + max(5.0, float(self.cfg.wait_timeout_sec or 1800))
        attempt = 0
        last_note = 0.0
        while True:
            attempt += 1
            try:
                rows = await asyncio.to_thread(self._rows)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"读闸门表失败：{exc}"
                if self.cfg.fail_open:
                    return None
                raise
            try:
                cleaned = self._cleanup_stale(rows)
            except Exception:  # noqa: BLE001
                cleaned = 0
            if cleaned:
                rows = [row for row in rows if row.record_id not in self._cleaned]

            active = self._active(rows)
            in_use = len(active)
            if in_use >= self.limit:
                now = time.monotonic()
                if now >= deadline:
                    self.last_error = "排队超时"
                    raise GateBusy(
                        f"飞书闸门排队超过 {int(self.cfg.wait_timeout_sec)}s"
                        f"（{self.limit} 个并发占位全被占满）"
                    )
                if on_wait and (now - last_note > 8 or attempt == 1):
                    last_note = now
                    try:
                        on_wait(in_use, self.limit, now - started)
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.sleep(
                    (self.poll_ms / 1000.0) * random.uniform(0.75, 1.25)
                )
                continue

            # 有位 → 先占坑
            lease = f"{int(time.time() * 1000)}-{random.randrange(100000, 999999)}"
            try:
                record_id = await asyncio.to_thread(self._create, lease, job, task, note)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"写闸门表失败：{exc}"
                if self.cfg.fail_open:
                    return None
                raise
            if not record_id:
                self.last_error = "写闸门表没有返回 record_id"
                if self.cfg.fail_open:
                    return None
                raise RuntimeError("写闸门表失败")

            # ---- 缓冲 + 二次检测：等飞书索引把这条记录同步出来，再数一遍 ----
            if self.settle_ms:
                await asyncio.sleep(self.settle_ms / 1000.0)
            try:
                rows2 = await asyncio.to_thread(self._rows)
            except Exception:  # noqa: BLE001 - 数不清就按「能进」处理（宁可多等一次）
                rows2 = []
            if rows2:
                active2 = self._active(rows2)
                mine = next((row for row in active2 if row.record_id == record_id), None)
                if len(active2) > self.limit or (mine is None and len(active2) >= self.limit):
                    # 别人也在同一瞬间抢到了 → 让出来重新排队，别把并发顶破
                    await asyncio.to_thread(self._delete, [record_id])
                    await asyncio.sleep((self.poll_ms / 1000.0) * random.uniform(0.6, 1.0))
                    continue
                in_use = len(active2)
            else:
                in_use = in_use + 1

            self.last_error = ""
            return Lease(
                id=record_id,
                limit=self.limit,
                in_use=in_use,
                waited=time.monotonic() - started,
                holder=self.cfg.holder,
                lease_token=lease,
            )

    # ------------------------------------------------------------- release #
    async def release(self, lease: Lease | None) -> None:
        if not lease or not lease.id:
            return
        try:
            await asyncio.to_thread(self._delete, [lease.id])
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"删占位记录失败：{exc}"

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
        active = self._active(rows)
        return GateStatus(
            ok=True,
            enabled=True,
            in_use=len(active),
            limit=self.limit,
            checked_at=time.time(),
            extra={
                "provider": "feishu",
                "table_id": self.table_id,
                "stale": len(self._stale(rows)),
                "holder": self.cfg.holder,
            },
        )

    async def close(self) -> None:
        return None


# ------------------------------------------------------------------ 建表工具 #
def ensure_gate_table(app, table_id: str = "") -> str:
    """建好闸门表（字段：占位/主机/说明/开始/租约）。已存在就直接用。"""
    app_token = app.cfg.app_token
    if not table_id:
        data = app._api(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables",
            {"table": {"name": "并发闸门", "default_view_name": "占位", "fields": [
                {"field_name": name, "type": ftype} for name, ftype in GATE_FIELDS
            ]}},
        )
        table_id = str(data.get("table_id") or "")
        if not table_id:
            raise RuntimeError("建表失败：飞书没返回 table_id")
        return table_id
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
    return table_id


def main(argv: list[str] | None = None) -> int:
    """python -m skillbridge.feishu_gate --init / --check"""
    import argparse

    from .config import load_config, load_env
    from . import feishu as feishu_mod

    parser = argparse.ArgumentParser(description="飞书并发闸门表（并发队列）")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--init", action="store_true", help="建表 / 补字段，并打印 table_id")
    parser.add_argument("--check", action="store_true", help="看当前占位情况")
    args = parser.parse_args(argv)

    root = Path.cwd()
    env = load_env()
    config = load_config(root / args.config, env)
    cfg = feishu_mod.load_config(config, env)
    if not cfg.ready()[0]:
        print("飞书未配置：", cfg.ready()[1])
        return 2
    app = feishu_mod.FeishuBitable(cfg)
    gate_cfg = (config.get("defaults") or {}).get("gate") or {}
    table_id = str(gate_cfg.get("table_id") or (config.get("feishu") or {}).get("gate_table_id") or "")

    if args.init:
        table_id = ensure_gate_table(app, table_id)
        print("闸门表 table_id =", table_id)
        print("把这一行加进 config.json：")
        print(json.dumps({"feishu": {"gate_table_id": table_id}}, ensure_ascii=False))
        return 0

    if not table_id:
        print("还没建表：先跑 python -m skillbridge.feishu_gate --init")
        return 1
    rows = [GateRow.from_item(item) for item in app.search_table(table_id, page_size=500)]
    now = time.time()
    active = [r for r in rows if now - (r.started or now) <= STALE_SEC]
    stale = [r for r in rows if r.started and now - r.started > STALE_SEC]
    print(f"闸门表 {table_id}")
    print(f"  占位：{len(active)} 条（上限 {gate_cfg.get('limit') or DEFAULT_LIMIT}）")
    for row in active[:20]:
        print(f"   - {row.label or row.record_id}  主机={row.holder or '?'}"
              f"  已占 {int(now - row.started)}s")
    if stale:
        print(f"  僵尸占位：{len(stale)} 条（> {int(STALE_SEC)}s，下次抢位时自动清掉）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
