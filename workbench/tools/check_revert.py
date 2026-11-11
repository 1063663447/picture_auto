# -*- coding: utf-8 -*-
"""9.6.10 自检：复原到第 1 轮 —— 轮次记录清掉、款式记忆不会被污染、确认后不能再复原。

跑法：python tools/check_revert.py
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from skillbridge import style_memory  # noqa: E402
from skillbridge.providers.base import EditRequest, Provider  # noqa: E402
from skillbridge.service import Workbench, WorkbenchError  # noqa: E402

FAILURES: list[str] = []


def check(ok: bool, label: str, show: bool) -> None:
    if show or not ok:
        print(f"  [{'ok' if ok else 'x'}] {label}")
    if not ok:
        FAILURES.append(label)


class MockProvider(Provider):
    name = "mock"
    label = "离线模拟"
    max_images = 4

    def __init__(self) -> None:
        super().__init__("mock", {"label": "离线模拟", "type": "mock", "ready": True})
        self.count = 0

    def info(self) -> dict:
        return {"name": self.name, "label": self.label, "ready": True,
                "max_images": self.max_images, "type": "mock"}

    async def edit(self, request: EditRequest, timeout: float = 60.0) -> bytes:
        self.count += 1
        # 尺寸/体积要过 runner 的「太小 = 错误信息」校验，所以画一张 512×512 的带噪图
        image = Image.effect_noise((512, 512), 18 + self.count % 7).convert("RGB")
        buf = io.BytesIO()
        image.save(buf, "PNG")
        return buf.getvalue()


def make_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), (240, 240, 242)).save(path)
    return path


async def wait_job(bench: Workbench, job_id: str, timeout: float = 60.0) -> None:
    task = bench._tasks.get(job_id)
    if task is not None:
        await asyncio.wait_for(task, timeout=timeout)


async def scenario(show: bool) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="revert-", dir=os.environ.get("PI_SCRATCH_DIR") or None))
    bench: Workbench | None = None
    try:
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
        bench.defaults()["auto_learn"] = False
        bench.defaults()["auto_learn_online"] = False
        bench.defaults()["review_master"] = True
        mock = MockProvider()
        bench.get_provider = lambda name=None: mock            # type: ignore[assignment]
        bench.gate_for = lambda provider=None: None            # type: ignore[assignment]
        bench.reload_skill()

        print("① 造一条待确认的基准图任务")
        upload = make_png(tmp / "upload" / "item-01" / "view-01.png")
        job = bench.create_job(
            [("view-01.png", upload.read_bytes())],
            garment_type="23-sorona-tee",
            selections=[{"garment_type": "23-sorona-tee", "colors": ["white", "black"]}],
            provider="mock",
            subject="garment",
            review_master=True,
            concurrency=1,
        )
        bench.start(job.id)
        await wait_job(bench, job.id)
        job = bench.get_job(job.id)
        masters = [t for t in job.tasks if t.master]
        check(len(masters) == 1, f"基准图 1 张（实际 {len(masters)}）", show)
        master = masters[0]
        check(bool(master.out), f"基准图有产出（out={master.out}, err={master.error!r}）", show)
        slug = master.garment
        before = style_memory.load_stats(bench.library_dir, slug) or {}
        rounds_before = int(before.get("review_rounds") or 0)

        print("② 连着改两轮（第 2 轮、第 3 轮）")
        first = await bench.reject_master(job.id, "第 2 轮：领口收小一点", [master.id])
        await wait_job(bench, first.id)
        first = bench.get_job(first.id)
        first_task = first.tasks[0]
        second = await bench.reject_master(
            job.id, "第 3 轮：下摆走线再平一点", [master.id],
            source_job_id=first.id, source_task_ids=[first_task.id],
        )
        await wait_job(bench, second.id)
        job = bench.get_job(job.id)
        reviews = job.params.get("master_reviews") or []
        check(len(reviews) == 2, f"记了 2 轮改图（实际 {len(reviews)}）", show)
        check([r.get("round") for r in reviews] == [1, 2],
              f"轮次是 1、2（实际 {[r.get('round') for r in reviews]}）", show)
        after_stats = style_memory.load_stats(bench.library_dir, slug) or {}
        check(int(after_stats.get("review_rounds") or 0) == rounds_before + 2,
              f"返工轮次 +2（{rounds_before} → {after_stats.get('review_rounds')}）", show)

        print("③ 复原到第 1 轮")
        result = await bench.revert_rework(job.id, master.id)
        check(result["dropped"] == 2, f"丢掉 2 轮（实际 {result['dropped']}）", show)
        job = bench.get_job(job.id)
        check(not (job.params.get("master_reviews") or []),
              "master_reviews 清空（确认时不会把糟的一轮写进款式记忆）", show)
        back_stats = style_memory.load_stats(bench.library_dir, slug) or {}
        check(int(back_stats.get("review_rounds") or 0) == rounds_before,
              f"返工轮次退回（{after_stats.get('review_rounds')} → {back_stats.get('review_rounds')}）", show)
        check(job.status == "review", f"任务仍停在 review（实际 {job.status}）", show)

        print("④ 复原之后确认，款式记忆里没有那两轮的要求")
        approved = await bench.approve_master(job.id, [master.id], note="")
        check(bool(approved), "确认成功", show)
        lessons = style_memory.load_lessons(bench.library_dir, slug) or []
        texts = " | ".join(str(item.get("text") or "") for item in lessons)
        check("领口收小" not in texts and "下摆走线" not in texts,
              "被复原的两轮要求没有进 lessons", show)

        print("⑤ 已确认的基准图不能再复原")
        try:
            await bench.revert_rework(job.id, master.id)
            check(False, "已确认后复原应被拒绝", show)
        except WorkbenchError as exc:
            check("已经确认" in str(exc), f"已确认后复原被拒（{exc}）", show)

        print()
        if FAILURES:
            print(f"失败 {len(FAILURES)} 条：")
            for item in FAILURES:
                print("  -", item)
            return 1
        print("全部通过：复原能丢轮次、能退统计、能把那一轮的记忆一起丢掉，确认后不再允许复原")
        return 0
    finally:
        if bench is not None:
            for task in list(bench._tasks.values()):
                task.cancel()
            await asyncio.sleep(0.8)
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    return asyncio.run(scenario("--show" in sys.argv))


if __name__ == "__main__":
    raise SystemExit(main())
