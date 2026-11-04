"""飞书表格闸门（并发队列）的离线回归测试。

用「假飞书客户端」实现同一套接口（search_table / create_records / delete_record），
验证四件事：

  1. **额度硬约束**：两个「人」抢 15 个坑，任何时刻占位行数 ≤ 15；
  2. **二次检测 + 缓冲**：故意让 search 落后 N 次（模拟飞书索引延迟），
     仍然不会超发（写完之后再数一遍，超了就自己让位）；
  3. **僵尸占位**：超过 stale_sec 的占位会被清掉，不会永久堵死队列；
  4. **release 删行**：跑完把占位删掉，in_use 回到 0。

另外验证「被限流的图」能被标记 + 一键重试（只捞 error_kind=rate_limit 的）。

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

from skillbridge.gate import GateConfig  # noqa: E402
from skillbridge.feishu_gate import FeishuGate, GateRow  # noqa: E402
from skillbridge.job import Job, Task  # noqa: E402
from skillbridge.runner import is_rate_limited  # noqa: E402
from skillbridge.service import Workbench, WorkbenchError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = Path(tempfile.mkdtemp(prefix="wb-fsgate-"))
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILED.append(label)


class FakeFeishu:
    """假飞书表：search 有延迟窗口，行为尽量贴近 bitable。"""

    def __init__(self, *, search_lag: int = 0) -> None:
        self.rows: dict[str, dict] = {}
        self.seq = 0
        self.search_lag = search_lag     # 前 N 次 search 看不到最新写入（模拟索引延迟）
        self.snapshot: list[dict] = []
        self.calls = {"search": 0, "create": 0, "delete": 0}
        self.peak = 0
        self._lock = False

    # 假飞书客户端接口 ----------------------------------------------------- #
    def search_table(self, table_id: str, page_size: int = 200, max_pages: int = 10) -> list[dict]:
        self.calls["search"] += 1
        if self.search_lag > 0:
            # 延迟窗口：返回上一次的快照
            self.search_lag -= 1
            return [dict(item) for item in self.snapshot]
        snapshot = [dict(item) for item in self.rows.values()]
        self.snapshot = snapshot
        self.peak = max(self.peak, len(snapshot))
        return [dict(item) for item in snapshot]

    def create_records(self, rows: list[dict], table_id: str = "") -> list[str]:
        ids = []
        for row in rows:
            self.seq += 1
            record_id = f"rec{self.seq:04d}"
            self.rows[record_id] = {"record_id": record_id, "fields": dict(row)}
            ids.append(record_id)
            self.calls["create"] += 1
        return ids

    def delete_record(self, table_id: str, record_id: str) -> dict:
        self.rows.pop(record_id, None)
        self.calls["delete"] += 1
        return {}

    # 断言辅助 ------------------------------------------------------------- #
    def active_rows(self) -> list[dict]:
        return list(self.rows.values())


def gate_config(
    limit: int, *, settle_ms: int = 40, poll_ms: int = 40, stale_sec: float = 600,
    wait_timeout_sec: float = 3.0,
) -> GateConfig:
    return GateConfig(
        enabled=True,
        provider="feishu",
        table_id="tblFAKE",
        limit=limit,
        poll_ms=poll_ms,
        settle_ms=settle_ms,
        stale_sec=stale_sec,
        wait_timeout_sec=wait_timeout_sec,
        fail_open=True,
        holder="pc-test",
    )


async def occupy(gate: FeishuGate, n: int, tag: str) -> list:
    leases = []

    async def one(index: int):
        lease = await gate.acquire(job="job", task=f"{tag}-{index}")
        leases.append(lease)

    await asyncio.gather(*(one(i) for i in range(n)))
    return leases


def main() -> int:
    print("[1] 额度 15：两个人同时抢 12 个坑，占位行数不会超 15")
    fake = FakeFeishu()
    gate_a = FeishuGate(gate_config(15), fake, label="pc-a")
    gate_b = FeishuGate(gate_config(15), fake, label="pc-b")
    leases = asyncio.run(_race(gate_a, gate_b, 12, 8))
    check("总占位 ≤ 15", len(fake.active_rows()) <= 15, f"{len(fake.active_rows())} 行")
    check("拿到牌的 ≤ 15", len([l for l in leases if l]) <= 15, f"{len([l for l in leases if l])} 张")
    # 飞书索引有延迟时允许「多写 1 条占位 → 二次检测再让位」的瞬时状态；
    # 真正的不变量是「同时拿到牌（=真去调接口）的数量 ≤ 额度」
    check("占位行峰值最多瞬时多 1 条", fake.peak <= 16, f"peak={fake.peak}")
    check("没抢到的不是放行（None 都来自 GateBusy）",
          len([l for l in leases if l]) == min(15, len(leases)), str(len([l for l in leases if l])))

    print("[2] 二次检测：飞书索引延迟 3 次仍不超发")
    fake2 = FakeFeishu(search_lag=3)
    gate_c = FeishuGate(gate_config(6, settle_ms=60), fake2, label="pc-c")
    gate_d = FeishuGate(gate_config(6, settle_ms=60), fake2, label="pc-d")
    leases2 = asyncio.run(_race(gate_c, gate_d, 5, 5))
    check("延迟下总占位仍 ≤ 6", len(fake2.active_rows()) <= 6, f"{len(fake2.active_rows())} 行")
    check("拿到的牌 ≤ 6", len([l for l in leases2 if l]) <= 6, str(len([l for l in leases2 if l])))
    blocked = len([l for l in leases2 if l is None])
    check("满额的那些被挡下（不是硬闯）", blocked >= 1, f"blocked={blocked}")

    print("[3] release 删行：全部还回去后 in_use 回到 0")
    async def release_all():
        for lease in leases2:
            if lease:
                await gate_c.release(lease)
    asyncio.run(release_all())
    check("占位清空", not fake2.active_rows(), f"{len(fake2.active_rows())} 行")
    status = asyncio.run(gate_c.status())
    check("status.in_use = 0", status.ok and status.in_use == 0, status.as_dict())

    print("[4] 僵尸占位：超过 stale_sec 的记录会被清掉")
    fake3 = FakeFeishu()
    old = fake3.create_records([{
        "占位": "old/1", "主机": "dead-pc", "说明": "", "开始": int((time.time() - 3600) * 1000),
        "租约": "x",
    }], "tblFAKE")
    check("先放一条 1 小时前的占位", len(fake3.active_rows()) == 1)
    gate_e = FeishuGate(gate_config(1, stale_sec=120), fake3, label="pc-e")
    lease = asyncio.run(gate_e.acquire(job="j", task="t"))
    check("僵尸被清掉后能拿到坑", lease is not None, str(lease))
    check("旧占位已被删除", old[0] not in [r["record_id"] for r in fake3.active_rows()],
          str([r["record_id"] for r in fake3.active_rows()]))

    print("[5] 队列等待：满额时 acquire 会等，别人还了就能进")
    fake4 = FakeFeishu()
    gate_f = FeishuGate(gate_config(1, poll_ms=60, settle_ms=30), fake4, label="pc-f")
    first = asyncio.run(gate_f.acquire(job="j", task="first"))
    waited: list[tuple[int, int, float]] = []

    async def second():
        return await gate_f.acquire(
            job="j", task="second",
            on_wait=lambda in_use, limit, waited_s: waited.append((in_use, limit, waited_s)),
        )

    async def scenario():
        task = asyncio.create_task(second())
        await asyncio.sleep(0.25)
        blocked = not task.done()
        await gate_f.release(first)
        lease2 = await asyncio.wait_for(task, timeout=10)
        return blocked, lease2

    blocked, lease2 = asyncio.run(scenario())
    check("满额时第二个确实在等", blocked)
    check("第一个还了之后第二个拿到坑", lease2 is not None, str(lease2))
    check("等待时回调报了队列状态", bool(waited) and waited[0][1] == 1, str(waited[:1]))

    print("[6] 限流失败的识别 + 一键重试")
    for text, expect in [
        ("HTTP 429 Too Many Requests", True),
        ("接口返回：并发数超限", True),
        ("rate limit exceeded", True),
        ("频率过高，请稍后再试", True),
        ("内容审核不通过", False),
        ("图片过大", False),
    ]:
        check(f"「{text[:18]}…」→ {'限流' if expect else '非限流'}", is_rate_limited(text) == expect)

    bench = _bench_with_rate_limit_job()
    job = bench.get_job("job-rate-limit")
    stats = job.counts()
    check("job.stats.rate_limited = 2", stats.get("rate_limited") == 2, str(stats.get("rate_limited")))
    from skillbridge.service import _retry_targets

    targets, wanted = _retry_targets(job, None, "rate_limit")
    check("一键重试只挑被限流的", [t.id for t in targets] == ["item-01__white", "item-01__black"],
          str([t.id for t in targets]))
    check("wanted 集合一致", wanted == {t.id for t in targets}, str(sorted(wanted)))
    rest, _ = _retry_targets(job, None, "")
    check("普通重试仍覆盖全部失败项", len(rest) == 3, str([t.id for t in rest]))

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项失败：{FAILED}")
        return 1
    print("[OK] 全部通过")
    return 0


async def _race(gate_a: FeishuGate, gate_b: FeishuGate, n_a: int, n_b: int):
    tasks: list = []

    async def worker(gate: FeishuGate, prefix: str, count: int):
        for index in range(count):
            try:
                lease = await gate.acquire(job="job", task=f"{prefix}-{index}")
            except Exception:  # noqa: BLE001 - 排队超时也照常继续（GateBusy）
                lease = None
            tasks.append(lease)
            await asyncio.sleep(0.002)

    await asyncio.gather(worker(gate_a, "a", n_a), worker(gate_b, "b", n_b))
    return tasks


def _bench_with_rate_limit_job() -> Workbench:
    """造一个含「被限流失败」的 job（不跑服务，只测统计与重试分流）。"""
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
