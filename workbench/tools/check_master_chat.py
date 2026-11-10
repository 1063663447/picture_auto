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
    print("② 前端状态机：只有 phase=derived / approved_ids 才算已确认")
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    check(
        'const approved = review.phase === "derived" || (review.approved_ids || []).length > 0;' in js,
        "用 phase/approved_ids 判「已确认」",
        show,
    )
    check("const awaitingReview = review.awaiting || job.status === \"review\";" in js,
          "「等确认」单独取 awaiting/status=review", show)
    # 分支顺序：approved → (awaiting && 出图) → 生成中
    order = []
    for marker in (
        "if (approved) {",
        "} else if (awaitingReview && masterDone) {",
        "} else if (masterTotal && masterDone < masterTotal) {",
    ):
        order.append(js.find(marker))
    check(all(index > 0 for index in order), "三个分支都在", show)
    check(order == sorted(order), f"分支顺序正确（approved 在前）：{order}", show)
    check("基准图生成中 " in js, "生成中有独立文案（不再说已确认）", show)
    check("state.masterShown = false;" in js, "没出图时会重置「已返图」标记", show)
    check("const hasShot = (state.job?.tasks || []).some((t) => t.master" in js,
          "开场白按「有没有基准图」分别说", show)


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
