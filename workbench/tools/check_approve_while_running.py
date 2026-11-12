"""多张基准图「边跑边确认」的回归（9.6.32）。

客户现场：一批里有 2 张基准图（06 连帽卫衣、10 弹力短袖）。先出图的那张想立刻点「确定」，
另一张还在生成 —— 以前后端直接拦「任务正在运行中，等这一轮结束再确认」，只能干等。

现在：确认立刻生效，刚确认这张的派生色**排进正在跑的那一轮**（并发仍由 runner 的 sem 和
飞书闸门控制，不绕限流）。

这个脚本离线验证：
  1. 有 runner 在跑时 approve → 不报错、标记已确认、调用了 runner.kick()、没有另起任务；
  2. 没有 runner 在跑时 approve → 照旧另起一轮派生；
  3. runner._kick_approved 只挑「已确认基准 + 没开始 + 没暂停」的派生色，且不会重复排；
  4. runner 有 kick / _drain_extra，且 _gather 的并发登记是并集（多批同时跑不会互相清空）。

用法：python tools/check_approve_while_running.py
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillbridge.job import Job, Task  # noqa: E402
from skillbridge.runner import JobRunner  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  ✓ " if ok else "  ✗ ") + label + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        FAIL.append(label)


def make_task(tid: str, *, status: str, out: str | None = None, master: bool = False,
              master_task: str = "", paused: bool = False) -> Task:
    return Task(id=tid, item="item-01", view="view-01", color=tid.split("__")[-1],
                color_hex="#ffffff", color_name="白色", garment="10-stretch-tee",
                garment_name="10 弹力短袖", status=status, out=out, master=master,
                master_task=master_task, paused=paused)


def build_job(root: Path) -> Job:
    folder = root / "20261112-170000-two"
    folder.mkdir(parents=True, exist_ok=True)
    job = Job(
        id="20261112-170000-two",
        params={"review_gate": True, "garment_type": "10-stretch-tee"},
        tasks=[
            # 第一张基准已经出图、待确认
            make_task("item-01__white", status="done", out="staging/a/WH.png", master=True),
            make_task("item-01__black", status="pending", master_task="item-01__white"),
            make_task("item-01__pink", status="pending", master_task="item-01__white"),
            # 第二张基准还在生成（另一款）
            make_task("item-02__white", status="running", master=True),
            make_task("item-02__black", status="pending", master_task="item-02__white"),
        ],
        status="running",
        stage="生成中",
    )
    job.root = folder
    job.save()
    (folder / "staging" / "a").mkdir(parents=True, exist_ok=True)
    (folder / "staging" / "a" / "WH.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return job


class FakeRunner:
    def __init__(self) -> None:
        self.kicked = 0

    def kick(self) -> None:
        self.kicked += 1


class FakeBusyTask:
    def done(self) -> bool:
        return False


def main() -> int:
    async def run_cases() -> None:
        temp = Path(tempfile.mkdtemp(prefix="wb-approve-"))
        try:
            print("1) 这一轮还在跑：确认不拦、派生色插进本轮")
            job = build_job(temp)
            bench = Workbench.__new__(Workbench)
            bench.jobs = {job.id: job}
            bench._tasks = {job.id: FakeBusyTask()}
            bench._runners = {job.id: FakeRunner()}
            bench._distill = {}
            bench.config = {"defaults": {"auto_learn": False}}
            bench.env = {}
            bench.data_dir = temp
            bench.skill = None
            before = bench._tasks[job.id]
            payload = await bench.approve_master(job.id, ["item-01__white"])
            master = next(t for t in job.tasks if t.id == "item-01__white")
            check("没有抛「任务正在运行中」", payload is not None)
            check("基准被标记为已确认", master.approved, str(master.approved))
            check("调用了 runner.kick()（排进本轮）",
                  bench._runners[job.id].kicked == 1, str(bench._runners[job.id].kicked))
            check("没有另起一轮任务（还是原来那条）", bench._tasks[job.id] is before)
            check("stage 说明不用等其它基准",
                  "不用等" in str(payload.get("stage")), str(payload.get("stage")))

            print("2) 没有 runner 在跑：照旧另起一轮派生")
            job2 = build_job(temp)
            bench2 = Workbench.__new__(Workbench)
            bench2.jobs = {job2.id: job2}
            bench2._tasks = {}
            bench2._runners = {}
            bench2._distill = {}
            bench2.config = {"defaults": {"auto_learn": False}}
            bench2.env = {}
            bench2.data_dir = temp
            bench2.skill = None

            class InstantRunner:
                def __init__(self) -> None:
                    self.ran = 0

                async def run(self, **_) -> None:
                    self.ran += 1

                def kick(self) -> None:
                    pass

            made: dict[str, InstantRunner] = {}

            def runner_for(_job):
                made["r"] = InstantRunner()
                return made["r"]

            bench2._runner_for = runner_for
            bench2._record_after = lambda *a, **k: None
            await bench2.approve_master(job2.id, ["item-01__white"])
            await asyncio.sleep(0)
            check("有新的派生任务被起起来", bool(made) and made["r"].ran == 1, str(len(made)))
            check("登记了 runner 句柄（后续边跑边确认要用）", job2.id in bench2._runners)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    asyncio.run(run_cases())

    print("3) runner._kick_approved：只挑该跑的，且不重复排")
    temp = Path(tempfile.mkdtemp(prefix="wb-kick-"))
    try:
        job = build_job(temp)
        # 再加一张已确认但派生暂停的，一张已出图的
        job.tasks.append(make_task("item-01__apricot", status="paused", master_task="item-01__white",
                                  paused=True))
        job.tasks.append(make_task("item-01__grey", status="done", out="staging/a/GY.png",
                                  master_task="item-01__white"))
        runner = JobRunner.__new__(JobRunner)
        runner.job = job
        runner.review_gate = True
        runner.review_mode = False
        runner._kicked = set()
        runner._extra = []
        captured: list[list[str]] = []

        async def fake_gather(items, handler):
            captured.append([t.id for t in items])

        runner._gather = fake_gather
        next(t for t in job.tasks if t.id == "item-01__white").approved = True
        asyncio.run(runner._kick_approved())
        first = captured[0] if captured else []
        check("只排了「已确认基准 + 没开始」的两张",
              sorted(first) == ["item-01__black", "item-01__pink"], str(first))
        check("暂停的/已出图的没被排进来",
              "item-01__apricot" not in first and "item-01__grey" not in first, str(first))
        asyncio.run(runner._kick_approved())
        check("同一批不会被重复排", len(captured) == 1, str(len(captured)))
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    print("4) 接线检查")
    runner_src = (ROOT / "skillbridge" / "runner.py").read_text(encoding="utf-8")
    service_src = (ROOT / "skillbridge" / "service.py").read_text(encoding="utf-8")
    check("runner 有 kick / _kick_approved / _drain_extra",
          all(k in runner_src for k in ("def kick(", "async def _kick_approved(", "async def _drain_extra(")))
    check("run() 收尾前会等临时排进来的派生色", "await self._drain_extra()" in runner_src)
    check("跑完基准也会等这批", "await self._drain_extra()\n        if self.review_gate:" in runner_src)
    check("_gather 的并发登记改成并集（多批同跑不互相清空）",
          "previous | set(tasks)" in runner_src)
    check("service 不再硬拦「正在运行中」",
          'raise WorkbenchError("任务正在运行中，等这一轮结束再确认")' not in service_src)
    check("service 侧会调 runner.kick()", "runner.kick()" in service_src)

    if FAIL:
        print(f"\n失败 {len(FAIL)} 项：" + "、".join(FAIL))
        return 1
    print("\n全部通过：多张基准可以边跑边确认，派生色立刻排进本轮（并发仍受闸门约束）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
