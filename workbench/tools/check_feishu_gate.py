"""飞书「槽位闸门」的离线回归测试（槽位抢占 + 先到先得排队）。

用假飞书表模拟 bitable 的四个动作（search / update / batch_create / delete），
并且**故意让 search 落后 N 次写入**（模拟飞书索引延迟），验证：

  [1] 硬额度：两台机器 30 个任务抢 6 个槽位 → 同时占用的槽位永远 ≤ 6；
  [2] 先到先得：额度 1，5 个人按顺序排队 → 放行顺序 = 取号顺序（FIFO）；
  [3] 抢坑冲突：两台机器同时抢同一个槽位 → 都最终拿到，但槽位峰值不变；
  [4] 僵尸槽位：占位超过 stale_sec 的可以被直接抢；
  [5] 限流识别 + 一键重试只捞 rate_limit。

运行：python tools/check_feishu_gate.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillbridge.feishu_gate import FeishuGate, Slot  # noqa: E402
from skillbridge.gate import GateConfig  # noqa: E402
from skillbridge.job import Job, Task  # noqa: E402
from skillbridge.runner import is_rate_limited  # noqa: E402
from skillbridge.service import Workbench, _retry_targets  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = Path(tempfile.mkdtemp(prefix="wb-slotgate-"))
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILED.append(label)


class FakeTable:
    """假飞书表：写立即生效，读有延迟（模拟索引）；created_time 用服务端序号。"""

    def __init__(self, *, lag: int = 0) -> None:
        self.rows: dict[str, dict] = {}
        self.seq = 0
        self.lag = lag
        self.history: list[list[dict]] = []
        self._stale_view: list[dict] = []
        self.busy_peak = 0
        self.updates = 0
        self.creates = 0
        self.deletes = 0
        self._snap()

    # --- 假客户端接口 ---------------------------------------------------- #
    def search_table(self, table_id: str, page_size: int = 200, max_pages: int = 10,
                     automatic: bool = False) -> list[dict]:
        # 真实飞书的索引是「随时间追平」的：这里用「读几次之后能看到最新」来模拟
        if self.lag > 0:
            self.lag -= 1
            return [dict(item) for item in self._stale_view]
        snapshot = [self._item(rid) for rid in self.rows]
        self._stale_view = snapshot
        return [dict(item) for item in snapshot]

    def create_records(self, rows: list[dict], table_id: str = "") -> list[str]:
        ids = []
        for row in rows:
            self.seq += 1
            record_id = f"rec{self.seq:05d}"
            self.rows[record_id] = {
                "fields": dict(row),
                "created": 1_700_000_000_000 + self.seq * 1000,
            }
            ids.append(record_id)
            self.creates += 1
        self._snap()
        return ids

    def update_record(self, table_id: str, record_id: str, fields: dict) -> dict:
        if record_id not in self.rows:
            raise KeyError(record_id)
        self.rows[record_id]["fields"].update(fields)
        self.updates += 1
        self._refresh_peak()
        self._snap()
        return {}

    def delete_record(self, table_id: str, record_id: str) -> dict:
        self.rows.pop(record_id, None)
        self.deletes += 1
        self._snap()
        return {}

    # --- 内部 ------------------------------------------------------------- #
    def _item(self, record_id: str) -> dict:
        row = self.rows[record_id]
        return {
            "record_id": record_id,
            "fields": dict(row["fields"]),
            "created_time": row["created"],
        }

    def _snap(self) -> None:
        self.history.append([self._item(rid) for rid in self.rows])
        if len(self.history) > 300:
            self.history = self.history[-150:]

    def _refresh_peak(self) -> None:
        # 只数**槽位**行（占位 01..N）的占用；排队号行的「租约」写的是 wait|…，不算并发
        busy = sum(
            1
            for rid, row in self.rows.items()
            if not self._is_ticket(rid) and str(row["fields"].get("租约") or "").strip()
        )
        self.busy_peak = max(self.busy_peak, busy)

    # --- 断言辅助 --------------------------------------------------------- #
    def _is_ticket(self, rid: str) -> bool:
        return str(self.rows[rid]["fields"].get("占位") or "").startswith("Q")

    def slots(self) -> list[Slot]:
        return [Slot.from_item(self._item(rid)) for rid in self.rows if not self._is_ticket(rid)]

    def tickets(self) -> list[dict]:
        return [{"record_id": rid, **self.rows[rid]["fields"]}
                for rid in self.rows if self._is_ticket(rid)]

    def busy(self) -> list[Slot]:
        now = time.time()
        return [slot for slot in self.slots() if slot.busy(now, 600)]

    def seed_slots(self, limit: int, stale: bool = False) -> None:
        rows = []
        for i in range(1, limit + 1):
            ghost = stale and i == 1
            rows.append({
                "占位": f"{i:02d}",
                "租约": "dead|old" if ghost else "",
                "主机": "ghost" if ghost else "",
                "开始": int((time.time() - 3600) * 1000) if ghost else 0,
                "说明": "",
            })
        self.create_records(rows, "tblFAKE")


def gate_config(limit: int, *, settle_ms: int = 20, poll_ms: int = 60,
                confirmations: int = 1, wait_timeout_sec: float = 20.0,
                stale_sec: float = 600) -> GateConfig:
    return GateConfig(
        enabled=True,
        provider="feishu",
        table_id="tblFAKE",
        limit=limit,
        poll_ms=poll_ms,
        settle_ms=settle_ms,
        confirmations=confirmations,
        stale_sec=stale_sec,
        wait_timeout_sec=wait_timeout_sec,
        fail_open=True,
        holder="pc-test",
    )


async def _burst(pairs, hold: float = 0.25):
    """多个「客户端」同时抢，抢到后占用 hold 秒再还。"""
    granted: list[tuple[str, int]] = []
    errors = 0

    async def worker(gate: FeishuGate, prefix: str, count: int):
        nonlocal errors
        for index in range(count):
            try:
                lease = await gate.acquire(job="job", task=f"{prefix}-{index:02d}")
            except Exception:  # noqa: BLE001 - GateBusy
                errors += 1
                continue
            if lease is None:
                errors += 1
                continue
            granted.append((prefix, index))
            await asyncio.sleep(hold)
            await gate.release(lease)

    await asyncio.gather(*(worker(gate, prefix, count) for gate, prefix, count in pairs))
    return granted, errors


def main() -> int:
    print("[1] 30 个任务抢 6 个槽位（两台机器，索引落后 2 次写入）")
    table = FakeTable(lag=2)
    table.seed_slots(6)
    gate_a = FeishuGate(gate_config(6), table, label="A")
    gate_b = FeishuGate(gate_config(6), table, label="B")
    granted, errors = asyncio.run(_burst([(gate_a, "A", 15), (gate_b, "B", 15)]))
    check("30 个任务都跑过（拿到或明确失败）", len(granted) + errors == 30,
          f"granted={len(granted)} errors={errors}")
    check("同时占用槽位峰值 ≤ 6", table.busy_peak <= 6, f"peak={table.busy_peak}")
    check("结束时没有占用残留", not table.busy(), f"{len(table.busy())} 个还占着")
    check("排队号都清掉了", not table.tickets(), f"{len(table.tickets())} 个残留")
    stats_a, stats_b = gate_a.stats, gate_b.stats
    print(f"      A 埋点: {json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in stats_a.items()}, ensure_ascii=False)}")
    print(f"      B 埋点: {json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in stats_b.items()}, ensure_ascii=False)}")
    check("拿牌/还牌配平",
          stats_a["acquired"] - stats_a["released"] <= 1
          and stats_b["acquired"] - stats_b["released"] <= 1,
          f"A={stats_a['acquired']}/{stats_a['released']} B={stats_b['acquired']}/{stats_b['released']}")

    print("[1.5] 同一台机器 10 张同时抢 3 个槽位（飞书索引落后也不许超发）")
    table15 = FakeTable(lag=2)
    table15.seed_slots(3)
    gate15 = FeishuGate(
        gate_config(3, settle_ms=10, poll_ms=30, wait_timeout_sec=60), table15, label="S"
    )
    notes15: list[dict] = []

    async def ten_at_once():
        async def one(index: int):
            lease = await gate15.acquire(
                job="job", task=f"s{index:02d}", on_wait=notes15.append
            )
            if lease is None:
                return None
            await asyncio.sleep(0.15)
            await gate15.release(lease)
            return lease

        return await asyncio.gather(*(one(i) for i in range(10)))

    leases15 = asyncio.run(ten_at_once())
    check("10 张都拿到槽位", all(lease is not None for lease in leases15),
          str([bool(lease) for lease in leases15]))
    check("单机峰值 ≤ 3（不靠表也要守住）", table15.busy_peak <= 3,
          f"peak={table15.busy_peak}")
    check("确实取过排队号", gate15.stats["tickets"] > 0,
          f"tickets={gate15.stats['tickets']}")
    check("排队时回调报了「前方还有几个」",
          bool(notes15) and all({"ahead", "queued", "limit"} <= set(note) for note in notes15),
          str(notes15[:1]))
    check("前方数字不会是 0（自己前面确实有人）",
          bool(notes15) and all(int(note.get("ahead") or 0) >= 1 for note in notes15),
          str([note.get("ahead") for note in notes15[:5]]))

    print("[2] 先到先得：额度 1，5 个人按顺序排队")
    table2 = FakeTable(lag=0)
    table2.seed_slots(1)
    gate = FeishuGate(gate_config(1, settle_ms=10, poll_ms=30, wait_timeout_sec=30),
                      table2, label="A")
    order: list[str] = []

    async def scenario():
        holder = await gate.acquire(job="job", task="holder")
        await asyncio.sleep(0.2)
        pending = []
        for i in range(5):
            async def one(name=f"t{i + 1}"):
                lease = await gate.acquire(job="job", task=name)
                if lease is not None:
                    order.append(name)
                    await gate.release(lease)      # 拿到就还，让后面的人继续
                return name
            pending.append(asyncio.create_task(one()))
            await asyncio.sleep(0.2)
        await asyncio.sleep(1.0)
        await gate.release(holder)
        await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(scenario())
    check("放行顺序 = 取号顺序（FIFO）", order == ["t1", "t2", "t3", "t4", "t5"], str(order))
    check("排队期间确实取过号", gate.stats["tickets"] >= 5, f"tickets={gate.stats['tickets']}")

    print("[2.5] 排队位置：界面「前方还有 xx 个任务」的数字")
    table2b = FakeTable(lag=0)
    table2b.seed_slots(1)
    gate_q = FeishuGate(gate_config(1, settle_ms=10, poll_ms=30, wait_timeout_sec=30),
                        table2b, label="Q")
    notes: list[dict] = []

    async def position_scenario():
        holder = await gate_q.acquire(job="job", task="holder")
        await asyncio.sleep(0.15)
        waiter = asyncio.create_task(
            gate_q.acquire(job="job", task="waiter", on_wait=notes.append)
        )
        await asyncio.sleep(0.5)
        status = await gate_q.status()
        await gate_q.release(holder)
        lease = await waiter
        if lease is not None:
            await gate_q.release(lease)
        return status

    status = asyncio.run(position_scenario())
    first = notes[0] if notes else {}
    check("排队时回调带了 ahead/queued/limit",
          {"ahead", "queued", "limit", "in_use"} <= set(first),
          str(first))
    check("额度 1 被占着时 ahead ≥ 1", int(first.get("ahead") or 0) >= 1, str(first))
    check("ahead = 正在跑 + 排我前面", 
          int(first.get("ahead") or 0) == int(first.get("in_use") or 0) + int(first.get("ahead_in_queue") or 0),
          str(first))
    extra = status.extra or {}
    check("status 里也有队列信息", {"queued", "ahead"} <= set(extra), str(sorted(extra)))

    print("[3] 抢坑冲突：两台机器同时抢唯一的槽位")
    table3 = FakeTable(lag=0)
    table3.seed_slots(1)
    gate_x = FeishuGate(gate_config(1, settle_ms=10, poll_ms=30, wait_timeout_sec=15), table3, label="X")
    gate_y = FeishuGate(gate_config(1, settle_ms=10, poll_ms=30, wait_timeout_sec=15), table3, label="Y")
    granted3, errors3 = asyncio.run(_burst([(gate_x, "X", 1), (gate_y, "Y", 1)], hold=0.3))
    check("两人都最终拿到槽位", len(granted3) == 2, f"{granted3} errors={errors3}")
    check("槽位峰值 ≤ 1", table3.busy_peak <= 1, f"peak={table3.busy_peak}")

    print("[4] 僵尸槽位：1 小时前的占位可以直接抢")
    table4 = FakeTable(lag=0)
    table4.seed_slots(1, stale=True)
    gate_z = FeishuGate(gate_config(1, stale_sec=120, settle_ms=10, poll_ms=30), table4, label="Z")
    lease = asyncio.run(gate_z.acquire(job="job", task="t"))
    check("僵尸槽位被接管", lease is not None, str(lease))
    check("接管后记录里是我", any(slot.lease.startswith("pc-test") for slot in table4.slots()),
          str([slot.lease[:18] for slot in table4.slots()]))
    asyncio.run(gate_z.release(lease))

    print("[5] 限流识别 + 一键重试分流")
    for text, expect in [
        ("HTTP 429 Too Many Requests", True),
        ("接口返回：并发数超限", True),
        ("飞书闸门排队超过 1800s（6 个槽位全被占满）", True),
        ("内容审核不通过", False),
    ]:
        check(f"「{text[:16]}…」→ {'限流' if expect else '非限流'}", is_rate_limited(text) == expect)

    bench = _bench_with_rate_limit_job()
    job = bench.get_job("job-rate-limit")
    check("stats.rate_limited = 2", job.counts().get("rate_limited") == 2,
          str(job.counts().get("rate_limited")))
    only_limited, _wanted = _retry_targets(job, None, "rate_limit")
    check("一键重试只挑限流失败",
          [t.id for t in only_limited] == ["item-01__white", "item-01__black"],
          str([t.id for t in only_limited]))
    all_failed, _ = _retry_targets(job, None, "")
    check("普通重试覆盖全部失败", len(all_failed) == 3, str(len(all_failed)))

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项失败：{FAILED}")
        return 1
    print("[OK] 全部通过")
    return 0


def _bench_with_rate_limit_job() -> Workbench:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    config["data_dir"] = str(SANDBOX / "data")
    config["skill_root"] = str(ROOT.parent / "apparel-style-recolor")
    config["library"] = {"enabled": True, "dir": str(ROOT / "library")}
    config["feishu"] = {"enabled": False}
    config["defaults"]["gate"] = {"enabled": False}
    config["providers"] = {"mock": {"type": "mock", "enabled": True, "mock_delay_sec": 0.01}}
    path = SANDBOX / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
    bench = Workbench(path)
    job = Job(
        id="job-rate-limit",
        params={"garment_type": "06-hoodie", "colors": ["white", "black"], "provider": "mock"},
        batch_name="x",
    )
    root = bench.data_dir / "jobs" / job.id
    (root / "inputs" / "item-01").mkdir(parents=True)
    root.joinpath("job.json").write_text("{}", encoding="utf-8")
    job.root = root
    job.tasks = [
        Task(id="item-01__white", item="item-01", view="", color="white", color_hex="#fff",
             color_name="白", status="failed", error="HTTP 429", error_kind="rate_limit"),
        Task(id="item-01__black", item="item-01", view="", color="black", color_hex="#000",
             color_name="黑", status="failed", error="接口返回：并发数超限", error_kind="rate_limit"),
        Task(id="item-02__white", item="item-02", view="", color="white", color_hex="#fff",
             color_name="白", status="failed", error="内容审核不通过", error_kind="error"),
        Task(id="item-03__white", item="item-03", view="", color="white", color_hex="#fff",
             color_name="白", status="done", out="staging/x.png"),
    ]
    bench.jobs[job.id] = job
    return bench


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(SANDBOX, ignore_errors=True)
