# -*- coding: utf-8 -*-
"""「白色优先做基准」+「出一张就能确认」回归。

客户要求（2026-11-11）：
    ① 以后基准图统一先做白色；
    ② 先生成出来的可以先确认和修改，不需要等全部完成后才同步；
    ③ 确认页要说明哪个图是哪个款式（前端由 check_master_chat 静态检查 + 这里校验数据字段）。

这个脚本钉后端部分：
    · 同款多色时，基准任务必须是**白色**（即使白色不在颜色列表第一位）
    · 单张基准一出图就进入 review（其它基准还在跑也能先确认）
    · 确认是按张的：确认 A 之后只跑 A 的派生色，B 的派生色不动，任务回到 review 等 B
    · B 也确认后，全部派生色跑完、任务正常收尾

用法：python tools/check_master_partial.py [--show]
"""
from __future__ import annotations

import asyncio
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from skillbridge.providers.base import EditRequest, Provider  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

FAILURES: list[str] = []


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
        self.requests: list[EditRequest] = []

    def info(self) -> dict:
        return {"name": self.name, "label": "recorded", "ready": True, "max_images": 4,
                "max_concurrency": 0, "unlimited": True, "skip_gate": True}

    async def open(self) -> None:
        return None

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        self.requests.append(request)
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
    bench.defaults()["auto_learn"] = False
    recorder = Recorder()
    bench.get_provider = lambda name=None: recorder       # type: ignore[assignment]
    bench.gate_for = lambda provider=None: None           # type: ignore[assignment]
    bench.reload_skill()
    return bench


async def wait_job(bench: Workbench, job_id: str, timeout: float = 60.0) -> None:
    task = bench._tasks.get(job_id)
    if task is not None:
        await asyncio.wait_for(task, timeout=timeout)


async def scenario(show: bool) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="partial-", dir=os.environ.get("PI_SCRATCH_DIR") or None))
    bench: Workbench | None = None
    try:
        bench = build_bench(tmp)
        upload = canvas(tmp / "upload" / "item-01" / "view-01.png")

        print("① 白色优先做基准（白色故意排在第二个）")
        job = bench.create_job(
            [("view-01.png", upload.read_bytes())],
            garment_type="23-sorona-tee",
            # 黑色在前、白色在后：基准仍必须是白色
            selections=[{"garment_type": "23-sorona-tee", "colors": ["black", "white", "red"]}],
            provider="recorder",
            subject="garment",
            review_master=True,
            concurrency=1,
        )
        masters = [t for t in job.tasks if t.master]
        check(len(masters) == 1, f"1 张基准（实际 {len(masters)}）", show)
        check(masters and masters[0].color == "white",
              f"基准色是白色（实际 {masters[0].color if masters else '无'}）", show)
        derived = {t.color for t in job.tasks if t.master_task}
        check(derived == {"black", "red"}, f"其余颜色是派生（{sorted(derived)}）", show)

        print("② 出一张就能确认：先只跑白色基准")
        runner = bench._runner_for(job)
        master = masters[0]
        await runner._execute_color_task(master)
        review = job.as_dict(with_tasks=False, with_log=False)["review"]
        check(job.status == "review", f"基准一出图就进 review（{job.status}）", show)
        check(master.id in review["pending_ids"], "这张已在「待确认」里", show)
        check(review["can_approve"] is True, "此时就能确认（不用等别的基准）", show)
        check(all(t.status == "pending" for t in job.tasks if t.master_task),
              "派生色一张都还没跑", show)

        print("③ 按张确认：确认白色 → 只跑白色那几张派生")
        await bench.approve_master(job.id, [master.id], note="")
        await wait_job(bench, job.id)
        approved = [t for t in job.tasks if t.master and t.approved]
        check(len(approved) == 1 and approved[0].color == "white", "白色基准已确认", show)
        # 同款三个颜色共用这一张白色基准：确认它，黑/红都该跟着跑
        done_colors = {t.color for t in job.tasks if t.master_task and t.out}
        check(done_colors == {"black", "red"},
              f"确认白色后，它管的派生色都跑了（{sorted(done_colors)}）", show)
        check(job.status in {"done", "partial"} or job.status == "review",
              f"任务状态合理（{job.status}）", show)

        print("④ 第二张基准随后确认 → 全部派生跑完")
        job2 = bench.create_job(
            [("view-01.png", upload.read_bytes())],
            garment_type="23-sorona-tee",
            selections=[
                {"garment_type": "23-sorona-tee", "colors": ["apricot", "white"]},
                {"garment_type": "11-kids-dolman-tee", "colors": ["pink", "black"]},
            ],
            provider="recorder",
            subject="garment",
            review_master=True,
            concurrency=1,
        )
        masters2 = [t for t in job2.tasks if t.master]
        check(len(masters2) == 2, f"两个款式各一张基准（{len(masters2)}）", show)
        check(
            sorted(t.color for t in masters2) == sorted({"white", "pink"}),
            f"每款各一张基准：{[t.color for t in masters2]}",
            show,
        )
        white_masters = [t for t in masters2 if t.color == "white"]
        check(len(white_masters) == 1, "有白色的那一款用白色做基准", show)
        check(all(t.garment_name for t in masters2), "基准任务带款式名（前端用来标注「哪个款式」）", show)

        bench.start(job2.id)
        await wait_job(bench, job2.id)
        first = [t for t in job2.tasks if t.master][0]
        await bench.approve_master(job2.id, [first.id], note="")
        await wait_job(bench, job2.id)
        check(job2.status == "review",
              f"确认一张、另一张没确认 → 仍停在 review（{job2.status}）", show)
        second = [t for t in job2.tasks if t.master and not t.approved][0]
        await bench.approve_master(job2.id, [second.id], note="")
        await wait_job(bench, job2.id)
        check(job2.status in {"done", "partial"}, f"两张都确认后正常收尾（{job2.status}）", show)
        check(all(t.out for t in job2.tasks), "所有派生色都出图了", show)

        print()
        if FAILURES:
            print(f"失败 {len(FAILURES)} 条：")
            for item in FAILURES:
                print("  -", item)
            return 1
        print("全部通过：白色优先 + 出一张即可确认 + 按张放行派生")
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
