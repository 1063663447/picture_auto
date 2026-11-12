"""白色够不够白 + 基准图「取消这款」的回归（9.6.33）。

两件事：
1. 客户：「很多款式的白色都不够白，有点像灰色」。白条款（WHITE_CLAUSE）要进所有提示词路径，
   深色不能误带；识别函数要能认出白色（按 label / 中文名 / 色号亮度）。
2. 客户：「基准图的暂停逻辑改成取消，取消后本次任务取消对应款式的生成，且该基准图和该款式
   待生成图片不显示」。校验 `cancel_style`：基准图 + 该款没开始的颜色 → canceled + hidden，
   统计里也不再计；前端不渲染 hidden 的卡片、审核条也撤下。

用法：python tools/check_white_cancel_style.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillbridge import prompts  # noqa: E402
from skillbridge.job import Job, Task  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  ✓ " if ok else "  ✗ ") + label + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        FAIL.append(label)


def make_task(tid: str, *, status: str, out: str | None = None, master: bool = False,
              master_task: str = "", garment: str = "10-stretch-tee") -> Task:
    return Task(id=tid, item=tid.split("__")[0], view="view-01", color=tid.split("__")[-1],
                color_hex="#E6E6E8", color_name="白色", garment=garment,
                garment_name="10 弹力短袖", status=status, out=out, master=master,
                master_task=master_task)


def main() -> int:
    wb = Workbench()
    garment = wb.skill.type_by_alias("10-stretch-tee")
    white = next(c for c in garment.colors if c.label == "white")
    black = next(c for c in garment.colors if c.label == "black")

    print("1) 白色条款：进所有路径，深色不误带")
    paths = {
        "一次到位": prompts.build_single_pass(wb.skill, garment, white, subject="person"),
        "派生色（色卡）": prompts.build_master_recolor(white, garment=garment, subject="person",
                                                  effect=True, swatch=True),
        "派生色（效果图）": prompts.build_master_recolor(white, garment=garment, subject="person",
                                                  effect=True),
        "只换色": prompts.build_recolor_only(white, garment=garment, subject="person"),
    }
    for name, text in paths.items():
        check(f"{name}：含 WHITE COLOURWAY", "WHITE COLOURWAY" in text)
        check(f"{name}：写明不许发灰",
              "must not come out light grey" in text and "paper-white" in text)
    black_text = prompts.build_single_pass(wb.skill, garment, black, subject="person")
    check("黑色不误带白条款", "WHITE COLOURWAY" not in black_text)
    check("识别白色（label）", prompts.is_white_colour(white))
    check("识别非白（黑色）", not prompts.is_white_colour(black))

    print("2) 取消这款：基准图 + 该款没开始的颜色 → 取消且不显示")
    temp = Path(tempfile.mkdtemp(prefix="wb-cancelstyle-"))
    try:
        folder = temp / "20261112-180000-cs"
        folder.mkdir(parents=True)
        job = Job(
            id="20261112-180000-cs",
            params={"review_gate": True},
            tasks=[
                # 款式 A：基准已出、待确认 + 2 个没开始的颜色
                make_task("item-01__white", status="done", out="staging/a/WH.png", master=True),
                make_task("item-01__black", status="pending", master_task="item-01__white"),
                make_task("item-01__pink", status="pending", master_task="item-01__white"),
                # 款式 B：另一个款式，不受影响
                make_task("item-02__white", status="done", out="staging/b/WH.png", master=True,
                          garment="06-hoodie"),
                make_task("item-02__black", status="pending", master_task="item-02__white",
                          garment="06-hoodie"),
            ],
            status="review",
            stage="基准图已出，等你确认",
        )
        job.root = folder
        job.save()
        (folder / "staging" / "a").mkdir(parents=True, exist_ok=True)
        (folder / "staging" / "b").mkdir(parents=True, exist_ok=True)
        (folder / "staging" / "a" / "WH.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        (folder / "staging" / "b" / "WH.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

        bench = Workbench.__new__(Workbench)
        bench.jobs = {job.id: job}
        bench._tasks = {}
        bench._runners = {}
        bench._distill = {}
        payload = bench.cancel_style(job.id, ["item-01__white"])
        live = bench.get_job(job.id)
        by_id = {t.id: t for t in live.tasks}
        check("基准图被隐藏", by_id["item-01__white"].hidden)
        check("基准图状态标成 canceled（不再算完成）",
              by_id["item-01__white"].status == "canceled", by_id["item-01__white"].status)
        check("该款没开始的颜色一起取消并隐藏",
              all(by_id[i].hidden and by_id[i].status == "canceled"
                  for i in ("item-01__black", "item-01__pink")),
              str([(i, by_id[i].status, by_id[i].hidden) for i in ("item-01__black", "item-01__pink")]))
        check("另一个款式不受影响",
              not by_id["item-02__white"].hidden and not by_id["item-02__black"].hidden)
        check("返回体带 tasks（前端可直接重画）", len(payload.get("tasks") or []) == 5)
        counts = live.counts()
        check("统计里不再计被取消的款式", counts["total"] == 2, str(counts))
        check("日志记了取消款式",
              any("已取消 1 个款式" in str(r.get("msg")) for r in live.log))
        check("审核条不再把它算成待确认",
              "item-01__white" not in ((live.as_dict(with_tasks=False).get("review") or {})
                                       .get("pending_ids") or []),
              str((live.as_dict(with_tasks=False).get("review") or {}).get("pending_ids")))
        reloaded = Job.load(job.path())
        check("hidden 落盘（刷新后仍然不显示）",
              next(t for t in reloaded.tasks if t.id == "item-01__white").hidden)
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    print("3) 前端接线")
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    api_src = (ROOT / "skillbridge" / "api.py").read_text(encoding="utf-8")
    check("审核卡片上是「取消这款」按钮", 'data-cancel-style="${esc(task.id)}"' in app_js)
    check("两步确认 + 点别处复原", "cancelStyleArmed" in app_js and "disarmCancelStyle" in app_js)
    check("成品网格不渲染 hidden 的卡片",
          "job.tasks.filter((task) => !task.hidden)" in app_js)
    check("审核条也不列 hidden 的基准图",
          "t.master && t.thumb_small && !t.hidden" in app_js)
    check("后端有 /cancel-style 路由", '"/api/jobs/{job_id}/cancel-style"' in api_src)

    if FAIL:
        print(f"\n失败 {len(FAIL)} 项：" + "、".join(FAIL))
        return 1
    print("\n全部通过：白色钉住「干净的白」，基准图取消这一款后卡片不再显示")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
