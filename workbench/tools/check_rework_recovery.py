"""框选改图「界面一直生成中、其实服务端早就出图了」的回归验证（9.6.24）。

客户的症状（2026-11-12）：框选改图后界面卡在「仍在处理中」，而飞书记录库里那张
「白色·改图」已经出图了。两条根因：

1. 主任务停在 `review`（等确认基准图），状态栏却只看有没有 running/pending，
   于是十几分钟一直打印「N 张待处理 · 仍在处理中」；
2. 「改到第几轮、改好的那张图」只活在前端内存（state.reworks），刷新页面就没了，
   卡片退回「第 1 轮 · 待确认」——改图结果既看不到，也点不了确定。

这个脚本离线验证修法，不调接口、不碰飞书、不花钱：
  * 后端：返工任务收尾时把轮次回填到主任务（`_update_master_rounds`），
    `job.review.rounds` 给出最新一轮（含可直接渲染的 thumb/preview），刷新后还在；
  * 前端：卡片状态按 `review.rounds` 恢复（hydrateReworks），改图任务查不到时会放弃并
    回主任务捞真实状态；审核期状态栏改说「等你确认基准图」，不再谎报「仍在处理中」。

用法：python tools/check_rework_recovery.py
"""
from __future__ import annotations

import json
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


def make_task(task_id: str, *, master: bool = False, out: str | None = None,
              master_task: str = "", status: str = "pending") -> Task:
    return Task(
        id=task_id, item="item-01", view="view-01", color=task_id.split("__")[-1],
        color_hex="#ffffff", color_name="白色", garment="10-stretch-tee",
        garment_name="10 弹力短袖", master=master, master_task=master_task,
        out=out, status=status,
    )


def make_job(root: Path, job_id: str, *, status: str, stage: str, tasks: list[Task],
             params: dict | None = None) -> Job:
    folder = root / job_id
    folder.mkdir(parents=True, exist_ok=True)
    job = Job(id=job_id, params=params or {}, tasks=tasks, status=status, stage=stage)
    job.root = folder
    job.save()
    return job


def bench_with(*jobs: Job) -> Workbench:
    """只给需要的那点属性：回填逻辑只碰 self.jobs。"""
    bench = Workbench.__new__(Workbench)
    bench.jobs = {job.id: job for job in jobs}
    bench._tasks = {}
    return bench


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="wb-rework-recovery-"))
    try:
        parent_dir = temp / "jobs"
        parent_dir.mkdir(parents=True, exist_ok=True)
        master_id = "item-01__white"
        rework_job_id = "20261112-133906-c082"
        parent = make_job(
            parent_dir, "20261112-133240-parent", status="review",
            stage="基准图修改中：改好后在对话框里确认，就会按新图改色",
            tasks=[
                make_task(master_id, master=True, out="staging/10CXDT/10CXDT_WH[0].png", status="done"),
                make_task("item-01__black", master_task=master_id),
                make_task("item-01__blue", master_task=master_id),
            ],
            params={
                "review_gate": True,
                "master_reviews": [{
                    "at": 0, "instruction": "帽檐压平一点",
                    "rework_job": rework_job_id, "master_ids": [master_id],
                    "from_job": "20261112-133240-parent", "from_tasks": [master_id], "round": 1,
                }],
            },
        )
        print("1) 返工任务还在跑的时候")
        review = parent.as_dict(with_tasks=False)["review"]
        rounds = review.get("rounds") or []
        check("rounds 已经有这一轮", len(rounds) == 1, str(rounds))
        check("状态是 running（还没出图）", rounds and rounds[0]["status"] == "running", str(rounds[:1]))
        check("running 时不给图地址", rounds and rounds[0]["thumb"] is None, str(rounds[:1]))

        print("2) 返工任务收尾 → 回填到主任务")
        rework = make_job(
            parent_dir, rework_job_id, status="done", stage="完成",
            tasks=[make_task(master_id, out="staging/10CXDT/10CXDT_WH[0]_v2.png", status="done")],
            params={"kind": "rework", "rework_round": 1},
        )
        bench = bench_with(parent, rework)
        bench._update_master_rounds(rework.id)
        review = parent.as_dict(with_tasks=False)["review"]
        first = (review.get("rounds") or [{}])[0]
        check("轮次变成 done", first.get("status") == "done", str(first))
        check("带上出图任务号", first.get("task") == master_id, str(first))
        check("thumb 指向返工任务（前端可直接渲染）",
              bool(first.get("thumb")) and rework.id in first["thumb"], str(first.get("thumb")))
        check("还有大图地址 preview", bool(first.get("preview")), str(first.get("preview")))
        check("主任务 stage 不再停在「修改中」", "已出图" in parent.stage, parent.stage)
        check("主任务日志记了回填",
              any("第 2 轮结果挂到基准图审核" in str(row.get("msg")) for row in parent.log))

        print("3) 刷新页面（重新从磁盘读）后还在")
        reloaded = Job.load(parent.path())
        reloaded_review = reloaded.as_dict(with_tasks=False)["review"]
        kept = (reloaded_review.get("rounds") or [{}])[0]
        check("轮次落盘了", kept.get("status") == "done" and kept.get("task") == master_id, str(kept))
        check("采纳用的坐标还在（job_id + task）",
              kept.get("job_id") == rework_job_id and bool(kept.get("task")),
              f"{kept.get('job_id')}::{kept.get('task')}")

        print("4) 多轮：只给最新一轮")
        parent.params["master_reviews"].append({
            "at": 1, "instruction": "再压一点", "rework_job": "20261112-134500-aaa1",
            "master_ids": [master_id], "from_job": rework_job_id,
            "from_tasks": [master_id], "round": 2,
        })
        parent.save()
        rounds = parent.as_dict(with_tasks=False)["review"]["rounds"]
        check("同一张基准图只出 1 条", len(rounds) == 1, str(rounds))
        check(
            "给的是最新一轮，且轮次数字和卡片一致（第 3 轮）",
            bool(rounds) and rounds[0]["round"] == 3,
            str(rounds[:1]),
        )

        print("5) 改图失败也要说清楚")
        broken_parent = make_job(
            parent_dir, "20261112-140000-parent", status="review", stage="base",
            tasks=[make_task(master_id, master=True, out="staging/bk.png", status="done"),
                   make_task("item-01__black", master_task=master_id)],
            params={"review_gate": True, "master_reviews": [{
                "at": 0, "instruction": "x", "rework_job": "20261112-140100-ffff",
                "master_ids": [master_id], "round": 1,
            }]},
        )
        broken_rew = make_job(
            parent_dir, "20261112-140100-ffff", status="failed", stage="接口报错",
            tasks=[make_task(master_id, status="failed")],
            params={"kind": "rework", "rework_round": 1},
        )
        broken_rew.error = "接口返回 HTTP 500"
        bench = bench_with(broken_parent, broken_rew)
        bench._update_master_rounds(broken_rew.id)
        row = (broken_parent.as_dict(with_tasks=False)["review"]["rounds"] or [{}])[0]
        check("失败轮次状态为 failed", row.get("status") == "failed", str(row))
        check("带上失败原因", "HTTP 500" in str(row.get("error")), str(row.get("error")))
        check("主任务 stage 说明没出图", "没出图" in broken_parent.stage, broken_parent.stage)

        print("6) 前端接线")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        ui_js = (ROOT / "web" / "blackglass-ui.js").read_text(encoding="utf-8")
        check("renderReview 会用服务端轮次恢复卡片",
              "hydrateReworks(job)" in app_js and "job.review.rounds" in app_js)
        check("盯改图任务有放弃条件（不再无限转圈）",
              "改图任务查不到了" in app_js and "改图超过 12 分钟" in app_js)
        check("放弃前先回主任务捞真实状态", "refreshParentJob" in app_js)
        check("审核期状态栏不再谎报「仍在处理中」",
              "等你点「确定」确认基准图" in ui_js and "仍在处理中" in ui_js)
        check("结构接线：_record_after 会回填轮次",
              "_update_master_rounds(job_id)" in (ROOT / "skillbridge" / "service.py").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    if FAIL:
        print(f"\n失败 {len(FAIL)} 项：" + "、".join(FAIL))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
