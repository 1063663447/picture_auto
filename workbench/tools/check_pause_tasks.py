"""单张/多张暂停的回归（9.6.31）。

客户：除了整批取消，还要能暂停某张、某些图片的生成 —— 包括基准图。
期望：暂停的不会开跑；基准图暂停后不再占着审核条；继续能把它们放回队列（整批收尾了会重新开跑）。

这个脚本离线验证（不调接口、不花钱）：
  1. 暂停 pending 的派生色 → status=paused，记住暂停前状态；
  2. 暂停已出图的基准图 → 成品留着、从审核条撤下（pending_ids 里没有它，paused_ids 有），
     它派生出来、还没开始的颜色一起暂停；
  3. 剩下的活全暂停 → 任务收尾成 partial，stage 说明「已暂停…可以接着跑」；
  4. 继续 → 状态还原（done 的基准图回 done、没跑的派回 pending），整批回到 running；
  5. runner / 前端接线：runner 的 open_tasks 排除 paused，卡片和审核条都有暂停/继续入口。

用法：python tools/check_pause_tasks.py
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


def build_job(root: Path) -> Job:
    folder = root / "20261112-160000-paus"
    folder.mkdir(parents=True, exist_ok=True)
    job = Job(
        id="20261112-160000-paus",
        params={"review_gate": True, "garment_type": "10-stretch-tee"},
        tasks=[
            make_task("item-01__white", status="done", out="staging/w/WH.png", master=True),
            make_task("item-01__black", status="pending", master_task="item-01__white"),
            make_task("item-01__pink", status="queued", master_task="item-01__white"),
            make_task("item-01__apricot", status="pending", master_task="item-01__white"),
        ],
        status="review",
        stage="基准图已出，等你确认（确认后按这张改其余颜色）",
    )
    job.root = folder
    job.save()
    return job


def bench_with(job: Job) -> Workbench:
    bench = Workbench.__new__(Workbench)
    bench.jobs = {job.id: job}
    bench._tasks = {}
    bench.start = lambda job_id: None  # 暂停/继续的测试不需要真的起 runner
    return bench


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="wb-pause-"))
    try:
        print("1) 暂停没开始的派生色")
        job = build_job(temp)
        bench = bench_with(job)
        bench.pause_tasks(job.id, ["item-01__pink"])
        pink = next(t for t in job.tasks if t.id == "item-01__pink")
        check("queued → paused", pink.status == "paused" and pink.paused, pink.status)
        check("记住了暂停前的状态", pink.paused_from == "queued", pink.paused_from)
        check("没暂停的还在队列里",
              next(t for t in job.tasks if t.id == "item-01__black").status == "pending")

        print("2) 暂停基准图：成品留着，但从审核条撤下（连带未开始的派生色）")
        bench.pause_tasks(job.id, ["item-01__white"])
        job = bench.get_job(job.id)
        master = next(t for t in job.tasks if t.id == "item-01__white")
        review = job.as_dict(with_tasks=False)["review"]
        check("基准图仍然 paused=True", master.paused)
        check("成品还在（out 没动）", bool(master.out), str(master.out))
        check("从待确认列表撤下", "item-01__white" not in (review.get("pending_ids") or []),
              str(review.get("pending_ids")))
        check("单独列在 paused_ids", "item-01__white" in (review.get("paused_ids") or []),
              str(review.get("paused_ids")))
        check("没有可确认的基准图时不显示「全部确定」", review.get("can_approve") is False)
        others = [t for t in job.tasks if t.id != "item-01__white"]
        check("同款派生色一起暂停", all(t.paused for t in others), str([(t.id, t.status) for t in others]))

        print("3) 剩下的活全暂停 → 任务收尾（不打包、不转圈）")
        check("status 收尾成 partial", job.status == "partial", job.status)
        check("stage 说明已暂停且能继续", "已暂停" in job.stage and "继续" in job.stage, job.stage)
        check("日志记了暂停",
              any("已暂停" in str(row.get("msg")) for row in job.log))

        print("4) 继续：状态还原、连带暂停的派生色一起放回、整批重新开跑")
        bench.resume_tasks(job.id, ["item-01__white"])
        job = bench.get_job(job.id)
        master = next(t for t in job.tasks if t.id == "item-01__white")
        check("基准图回到 done（成品本来就在）", master.status == "done" and not master.paused,
              master.status)
        review = job.as_dict(with_tasks=False)["review"]
        check("重新出现在待确认列表", "item-01__white" in (review.get("pending_ids") or []),
              str(review.get("pending_ids")))
        check("有基准待确认时回到 review（不急着开 runner）", job.status == "review", job.status)
        by_id = {t.id: t for t in job.tasks}
        check("被基准连带暂停的派生色一起放回",
              not by_id["item-01__black"].paused and not by_id["item-01__apricot"].paused,
              str([(t.id, t.status, t.paused) for t in job.tasks]))
        check("自己单独暂停的那张不受牵连（粉色仍然暂停）",
              by_id["item-01__pink"].paused and by_id["item-01__pink"].status == "paused",
              str(by_id["item-01__pink"].status))
        try:
            bench.resume_tasks(job.id, ["item-01__black"])
            check("对没暂停的图点继续会被拦住", False)
        except Exception:
            check("对没暂停的图点继续会被拦住", True)

        print("5) 拒绝空选择 / 不存在的任务")
        for bad in ([], ["nope"]):
            try:
                bench.pause_tasks(job.id, bad)
                check(f"非法输入被拦下 {bad}", False)
            except Exception:
                check(f"非法输入被拦下 {bad}", True)

        print("6) runner / 前端接线")
        runner_src = (ROOT / "skillbridge" / "runner.py").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        ui_js = (ROOT / "web" / "blackglass-ui.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        check("runner 不再挑暂停的任务",
              '"skipped", "paused"' in runner_src and 'if paused_left:' in runner_src)
        check("取消整批时暂停的也一起取消",
              '"pending", "queued", "running", "paused"' in runner_src)
        check("卡片上有暂停/继续按钮", 'data-pause="${esc(task.id)}"' in app_js)
        check("审核条上有暂停/继续（基准图）", "data-pause-review" in app_js)
        check("工具栏有暂停选中/继续选中",
              'id="batchPauseBtn"' in html and 'id="batchResumeBtn"' in html)
        check("批量按钮按选中状态判断可用",
              '$("batchPauseBtn")' in app_js and "canResume" in app_js)
        check("卡片文案有「已暂停」", "paused:'已暂停'" in ui_js)
        check("暂停计入「待处理」筛选", "['pending','queued','canceled','paused']" in ui_js)
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    if FAIL:
        print(f"\n失败 {len(FAIL)} 项：" + "、".join(FAIL))
        return 1
    print("\n全部通过：单张/多张/基准图都能暂停与继续，收尾不转圈")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
