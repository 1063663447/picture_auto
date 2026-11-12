"""基准图「重新生成」的回归（9.6.34）。

客户：基准图的「取消这款」改成「重新生成」，并把「框选改 / 直接说 / 重新生成」收到一个「改图 ⌘」菜单里。
这个脚本离线验证：

  1. service.regenerate：把成品清空、放回 pending、approved 复位，任务回到 running；
  2. 这一轮还在跑时 → 走 runner.reschedule()（排进当前这一轮），不另起任务；
  3. 没在跑时 → 另起一轮；
  4. 前端接线：审核卡片「改图 ⌄」菜单 + 三个选项 + regenerateTask 打 /regenerate。

用法：python tools/check_regenerate.py
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
from skillbridge.service import Workbench  # noqa: E402

FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  ✓ " if ok else "  ✗ ") + label + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        FAIL.append(label)


def make_task(tid: str, *, status: str, out: str | None = None, master: bool = False,
              master_task: str = "") -> Task:
    return Task(id=tid, item="item-01", view="view-01", color=tid.split("__")[-1],
                color_hex="#E6E6E8", color_name="白色", garment="10-stretch-tee",
                garment_name="10 弹力短袖", status=status, out=out, master=master,
                master_task=master_task)


def build_job(root: Path, job_id: str) -> Job:
    folder = root / job_id
    folder.mkdir(parents=True, exist_ok=True)
    job = Job(
        id=job_id,
        params={"review_gate": True},
        tasks=[
            make_task("item-01__white", status="done", out="staging/a/WH.png", master=True),
            make_task("item-01__black", status="pending", master_task="item-01__white"),
        ],
        status="review",
        stage="基准图已出，等你确认",
    )
    job.root = folder
    job.save()
    (folder / "staging" / "a").mkdir(parents=True, exist_ok=True)
    (folder / "staging" / "a" / "WH.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    return job


class FakeRunner:
    def __init__(self) -> None:
        self.rescheduled: list[list[str]] | None = None

    def reschedule(self, ids=None) -> None:
        self.rescheduled = [list(ids or [])]

    async def run(self, **_):  # pragma: no cover - 只在“没在跑”的分支里用
        return None

    def kick(self) -> None:  # pragma: no cover
        return None


class BusyTask:
    def done(self) -> bool:
        return False


def main() -> int:
    async def cases() -> None:
        temp = Path(tempfile.mkdtemp(prefix="wb-regen-"))
        try:
            print("1) 没有 runner 在跑：清空 + 放回队列 + 另起一轮")
            job = build_job(temp, "20261112-190000-rg1")
            master = next(t for t in job.tasks if t.master)
            master.approved = True
            master.adopted_from = "x::y"
            bench = Workbench.__new__(Workbench)
            bench.jobs = {job.id: job}
            bench._tasks = {}
            bench._runners = {}
            bench._distill = {}
            bench.config = {"defaults": {}}
            bench.env = {}
            bench.data_dir = temp

            class InstantRunner(FakeRunner):
                def __init__(self) -> None:
                    super().__init__()
                    self.ran = 0

                async def run(self, **_):
                    self.ran += 1

            made: dict[str, InstantRunner] = {}

            def runner_for(_job):
                made["r"] = InstantRunner()
                return made["r"]

            bench._runner_for = runner_for
            bench._record_after = lambda *a, **k: None
            payload = bench.regenerate(job.id, ["item-01__white"])
            live = bench.get_job(job.id)
            master = next(t for t in live.tasks if t.id == "item-01__white")
            check("成品被清空", master.out is None and master.bytes == 0, str(master.out))
            check("状态回到 pending", master.status == "pending", master.status)
            check("approved / adopted_from 复位",
                  not master.approved and not master.adopted_from,
                  f"{master.approved} / {master.adopted_from}")
            check("任务回到 running", live.status == "running", live.status)
            check("起了新一轮", bool(made), str(len(made)))
            check("日志记了重新生成",
                  any("重新生成" in str(r.get("msg")) for r in live.log))
            check("返回体带 tasks", len(payload.get("tasks") or []) == 2)

            print("2) 这一轮还在跑：排进当前这一轮（不另起任务）")
            job2 = build_job(temp, "20261112-190100-rg2")
            bench2 = Workbench.__new__(Workbench)
            bench2.jobs = {job2.id: job2}
            bench2._tasks = {job2.id: BusyTask()}
            bench2._runners = {job2.id: FakeRunner()}
            bench2._distill = {}
            bench2.config = {"defaults": {}}
            bench2.env = {}
            bench2.data_dir = temp
            before = bench2._tasks[job2.id]
            bench2.regenerate(job2.id, ["item-01__white"])
            check("调用了 reschedule", bench2._runners[job2.id].rescheduled == [["item-01__white"]],
                  str(bench2._runners[job2.id].rescheduled))
            check("没有另起任务", bench2._tasks[job2.id] is before)
            check("stage 说明排进当前轮",
                  "当前这一轮" in bench2.get_job(job2.id).stage, bench2.get_job(job2.id).stage)

            print("3) 非法输入")
            for bad in ([], ["nope"]):
                try:
                    bench.regenerate(job.id, bad)
                    check(f"非法输入被拦下 {bad}", False)
                except Exception:
                    check(f"非法输入被拦下 {bad}", True)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    asyncio.run(cases())

    print("4) 接线检查")
    api_src = (ROOT / "skillbridge" / "api.py").read_text(encoding="utf-8")
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    runner_src = (ROOT / "skillbridge" / "runner.py").read_text(encoding="utf-8")
    check("后端有 /regenerate 路由", '"/api/jobs/{job_id}/regenerate"' in api_src)
    check("runner 有 reschedule / _run_rescheduled",
          "def reschedule(" in runner_src and "async def _run_rescheduled(" in runner_src)
    check("审核卡片是「改图 ⌄」菜单", 'data-review-menu="${esc(task.id)}"' in app_js)
    for label in ("框选改", "直接说", "重新生成"):
        check(f"菜单里有「{label}」", label in app_js)
    check("前端有 regenerateTask 并打 /regenerate",
          "function regenerateTask(" in app_js and "`/api/jobs/${jobId}/regenerate`" in app_js)
    check("「取消这款」按钮已从卡片撤掉",
          'data-cancel-style="${esc(task.id)}"' not in app_js)

    if FAIL:
        print(f"\n失败 {len(FAIL)} 项：" + "、".join(FAIL))
        return 1
    print("\n全部通过：基准图能重新生成，操作收在「改图 ⌄」菜单里")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
