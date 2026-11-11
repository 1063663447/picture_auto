# -*- coding: utf-8 -*-
"""对话框状态回归：修「基准图还没出就说基准已确认」这个 bug 不许复发。

现场（2026-11-11 客户截图）：
    基准图还在生成，对话框里就已经出现「基准已确认，正在按它给其余颜色改色：0/11」，
    而且从来没把基准图返给用户确认。

根因：前端把「`review.awaiting` 为假」当成「已确认」，但**任务刚创建**（基准还在排队/生成）时
`review.awaiting` 也是假 —— 这两个状态必须用别的信号区分。

这个检查做两件事：
  ① 把后端的三种状态量出来（刚创建 / 等确认 / 已确认），证明 awaiting 不能当「已确认」用；
  ② 静态检查前端确实按「phase=derived 或 approved_ids 非空」才判已确认，且生成中只说进度。

用法：python tools/check_master_chat.py [--show]
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from skillbridge.providers.base import EditRequest, Provider  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []
SLUG = "23-sorona-tee"


def check(ok: bool, label: str, show: bool = False) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


def canvas(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (512, 512), (242, 242, 244))
    ImageDraw.Draw(image).rectangle([150, 90, 360, 430], fill=(190, 40, 45))
    image.save(path)
    return path


def png_bytes() -> bytes:
    image = Image.new("RGB", (256, 256), (30, 30, 34))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


class Recorder(Provider):
    supports_negative = True
    max_images = 4

    def __init__(self) -> None:
        super().__init__("recorder", {"label": "recorder", "type": "mock", "ready": True})

    def info(self) -> dict:
        return {"name": self.name, "label": "recorded", "ready": True, "max_images": 4,
                "max_concurrency": 0, "unlimited": True, "skip_gate": True}

    async def open(self) -> None:
        return None

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        return png_bytes()


def build_bench(tmp: Path) -> Workbench:
    bench = Workbench()
    bench.data_dir = tmp / "data"
    bench.jobs_dir = bench.data_dir / "jobs"
    bench.jobs_dir.mkdir(parents=True, exist_ok=True)
    library = tmp / "library"
    for name in ("prompts", "refs", "effects"):
        source = bench.library_dir / name
        if source.is_dir():
            shutil.copytree(source, library / name, dirs_exist_ok=True)
    shutil.copy2(bench.library_dir / "styles.json", library / "styles.json")
    bench.config.setdefault("library", {})["dir"] = str(library)
    bench.defaults()["auto_learn"] = False        # 这个检查不关心沉淀
    recorder = Recorder()
    bench.get_provider = lambda name=None: recorder       # type: ignore[assignment]
    bench.gate_for = lambda provider=None: None           # type: ignore[assignment]
    bench.reload_skill()
    return bench


async def wait_job(bench: Workbench, job_id: str, timeout: float = 60.0) -> None:
    task = bench._tasks.get(job_id)
    if task is not None:
        await asyncio.wait_for(task, timeout=timeout)


def static_checks(show: bool) -> None:
    print("② 前端：确认改回画布（可单张）+ 不再自动弹 AI 窗口")
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    # 1) 生成后不再自动打开对话
    check("startMasterChat" not in js, "已删除「生成即弹 AI 窗口」的 startMasterChat", show)
    check("if(data.job?.review?.enabled) startMasterChat" not in js,
          "createJob 里不再自动开对话", show)
    check("只有需要改图时才打开对话框" in js, "注释写明「改图时才开对话」", show)

    # 2) 画布审核条：标清款式 + 颜色
    check('id="reviewBar"' in index, "画布上有基准图审核条 #reviewBar", show)
    check("function styleLabel(task)" in js, "有「款式 · 颜色」标签函数", show)
    check("task.garment_name || task.garment" in js, "卡片上取款式名", show)
    check("task.color_name || task.color" in js, "卡片上取颜色名", show)

    # 3) 每张单独确认 / 改图
    check('data-approve="${esc(task.id)}"' in js, "每张基准图有「这张可以」按钮", show)
    check('data-rework="${esc(task.id)}"' in js, "每张基准图有「改这张」按钮", show)
    check("approveMaster(approve, null, [approve.dataset.approve])" in js,
          "「这张可以」只确认这一张", show)
    check("async function approveMaster(button, adopt, onlyTaskIds)" in js,
          "approveMaster 支持指定任务", show)
    check("openHarness(\"master\", [target.id], state.job.id)" in js,
          "「改这张」只把这一张带进对话", show)
    # 9.6.7 卡片改成差异化状态：已确认那一支只留一行说明，不再渲染任何按钮
    approved_act = ""
    if "if (st.approved) {" in js:
        head = js.index("if (st.approved) {")
        tail = js.index("} else if (st.working) {", head)
        approved_act = js[head:tail]
    check(bool(approved_act) and "<button" not in approved_act,
          "已确认的卡片收起按钮", show)

    # 4) 对话不再自动返基准图（确认在画布）
    check("chatMasterCard(" not in js, "对话不再自动返基准图卡片（画布接管）", show)
    check("function masterWatch(" not in js, "已删除自动轮询返图的 masterWatch", show)
    check("现在只改「" in js, "对话开场白点名「只改这一张」", show)

    # 5) 三态判据仍在：卡片只对「没确认」的图给按钮，确认状态看 task.approved
    check("const pending = masters.filter((t) => !t.approved);" in js,
          "待确认按 task.approved 过滤（不是靠 awaiting）", show)
    check('const head = $("reviewHead");' in js and "已全部确认" in js,
          "审核条只报待确认/改图计数，不自报改色进度", show)
    service = (ROOT / "skillbridge" / "service.py").read_text(encoding="utf-8")
    check("def review_payload(self, job: Job) -> dict:" in service,
          "后端 review_payload 仍区分 awaiting / approved", show)

async def scenario(show: bool) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="master-chat-", dir=os.environ.get("PI_SCRATCH_DIR") or None))
    bench: Workbench | None = None
    try:
        bench = build_bench(tmp)
        upload = canvas(tmp / "upload" / "item-01" / "view-01.png")
        job = bench.create_job(
            [("view-01.png", upload.read_bytes())],
            garment_type=SLUG,
            selections=[{"garment_type": SLUG, "colors": ["white", "black", "red"]}],
            provider="recorder",
            subject="garment",
            review_master=True,
            concurrency=1,
        )
        print("① 后端三种状态（证明 awaiting 不能当「已确认」）")
        fresh = job.as_dict(with_tasks=False, with_log=False)["review"]
        check(fresh["awaiting"] is False, "刚创建：awaiting=False（但基准还没出）", show)
        check(fresh["phase"] == "", f"刚创建：phase 为空（实际 {fresh['phase']!r}）", show)
        check(not fresh["approved_ids"], "刚创建：没有 approved", show)
        check(not [t for t in job.tasks if t.master and t.out], "刚创建：基准任务没有图", show)

        bench.start(job.id)
        await wait_job(bench, job.id)
        waiting = job.as_dict(with_tasks=False, with_log=False)["review"]
        check(job.status == "review" and waiting["awaiting"] is True,
              f"出完基准：awaiting=True（{job.status}）", show)
        check(waiting["phase"] == "", "出完基准：还没确认（phase 仍为空）", show)
        check(bool([t for t in job.tasks if t.master and t.out]), "此时才有基准图可返给用户", show)

        await bench.approve_master(job.id, note="")
        await wait_job(bench, job.id)
        approved = job.as_dict(with_tasks=False, with_log=False)["review"]
        check(approved["phase"] == "derived", "确认后：phase=derived（前端据此进入改色进度）", show)
        check(bool(approved["approved_ids"]), "确认后：approved_ids 非空", show)
        check(approved["awaiting"] is False, "确认后：awaiting 变回 False", show)

        print()
        if FAILURES:
            print(f"失败 {len(FAILURES)} 条：")
            for item in FAILURES:
                print("  -", item)
            return 1
        print("全部通过：只有「出完基准 + 已确认」才说已确认，生成中只报进度")
        return 0
    finally:
        if bench is not None:
            for task in list(bench._tasks.values()):
                task.cancel()
            await asyncio.sleep(0.8)
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    show = "--show" in sys.argv
    static_checks(show)
    return asyncio.run(scenario(show))


if __name__ == "__main__":
    raise SystemExit(main())
