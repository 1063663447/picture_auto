"""「取消这批」的回归（9.6.28/9.6.30）。

客户现场：两段式任务停在「基准图 1 张待确认」，4 个颜色一直待处理，整批把后面的活堵住。
旧 `cancel()` 只置 `cancel_requested` + cancel 掉 runner 的 asyncio 任务；可 review 期根本
没有 runner 在跑，点了取消什么都不变（界面还一直「等你点确定」）。

这个脚本离线验证修法：
  * 停在 review 的任务：cancel 后 status=canceled，没开始的颜色标 canceled，**已出的成品保留**；
  * 返回体带 tasks（前端要直接整页重画，不带任务会把画廊清空）；
  * 还在跑的任务：仍然只置取消标记，交给 runner 收尾（不在这里强改状态）；
  * 前端接线：审核条上有「取消这批」按钮、两步确认、取消后立刻重画、terminal 后不再显示审核条。

用法：python tools/check_cancel_button.py
"""
from __future__ import annotations

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
                color_hex="#ffffff", color_name="白色", garment="10-stretch-tee",
                garment_name="10 弹力短袖", status=status, out=out, master=master,
                master_task=master_task)


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="wb-cancel-"))
    try:
        folder = temp / "20261112-151705-58f7"
        folder.mkdir(parents=True)
        job = Job(
            id="20261112-151705-58f7",
            params={"review_gate": True, "garment_type": "10-stretch-tee"},
            tasks=[
                make_task("item-01__white", status="done", out="staging/x/WH.png", master=True),
                make_task("item-01__black", status="pending", master_task="item-01__white"),
                make_task("item-01__pink", status="queued", master_task="item-01__white"),
                make_task("item-01__apricot", status="pending", master_task="item-01__white"),
            ],
            status="review",
            stage="基准图已出，等你确认（确认后按这张图改其余颜色）",
        )
        job.root = folder
        job.save()

        bench = Workbench.__new__(Workbench)
        bench.jobs = {job.id: job}
        bench._tasks = {}

        print("1) 停在 review：点取消要真的收尾")
        payload = bench.cancel(job.id)
        live = bench.get_job(job.id)
        counts = live.counts()
        check("status = canceled", payload["status"] == "canceled", str(payload["status"]))
        check("3 个没开始的颜色变成 canceled", counts["canceled"] == 3, str(counts))
        check("已出的基准图还在", counts["done"] == 1 and bool(live.tasks[0].out), str(counts))
        check("stage 写明已取消", "已取消" in live.stage, live.stage)
        check("返回体带 tasks（前端可直接重画）",
              len(payload.get("tasks") or []) == 4, str(len(payload.get("tasks") or [])))
        check("日志记了取消", any("任务已取消" in str(row.get("msg")) for row in live.log))
        reloaded = Job.load(job.path())
        check("落盘了（刷新后还是取消状态）", reloaded.status == "canceled", reloaded.status)

        print("2) 正在跑的任务：只置取消标记，状态交给 runner")
        running_dir = temp / "20261112-160000-run1"
        running_dir.mkdir(parents=True)
        running = Job(
            id="20261112-160000-run1",
            params={"garment_type": "10-stretch-tee"},
            tasks=[make_task("item-01__white", status="running")],
            status="running",
            stage="生成中",
        )
        running.root = running_dir
        running.save()

        class FakeTask:
            def done(self) -> bool:
                return False

            def cancel(self) -> None:
                self.canceled = True

        fake = FakeTask()
        bench.jobs[running.id] = running
        bench._tasks[running.id] = fake
        bench.cancel(running.id)
        check("跑着的单子仍然是 running（交给 runner 收尾）",
              bench.get_job(running.id).status == "running", bench.get_job(running.id).status)
        check("调用了 runner 的 cancel", getattr(fake, "canceled", False))
        check("置了 cancel_requested", bench.get_job(running.id).cancel_requested)

        print("3) 前端接线")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        check("审核条上有「取消这批」按钮", 'id="reviewCancelBtn"' in html)
        check("两步确认 + 点别处复原", "cancelArmed" in app_js and "disarmCancel" in app_js)
        check("取消后直接重画任务", "renderJob(res.job)" in app_js)
        check("terminal 后不再显示审核条",
              "!TERMINAL.includes(job.status)" in app_js)
        check("工具栏取消也会立刻重画", app_js.count("renderJob(res.job)") >= 2)
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    if FAIL:
        print(f"\n失败 {len(FAIL)} 项：" + "、".join(FAIL))
        return 1
    print("\n全部通过：审核期可以取消、成品保留、前端入口与两步确认都在")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
