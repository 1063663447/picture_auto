# -*- coding: utf-8 -*-
"""基准图审核闸门回归：先出基准 → 停下待确认 → 确认后只按基准改色。

客户需求（2026-11-11）：
「先出基准图，提供确定和修改功能通过 harness 与用户对齐，最终排版再以基准图进行改色减少错误率，
并且根据对齐结果来沉淀迭代对应款式的 skill。」

这里用「记录型接口」（不发真实请求、不花钱）把整条链路钉死：

  ① 建任务：多色 → review_gate 打开；单色 → 自动关闭
  ② 第一趟：只发基准色那一次请求，任务停在 review，派生色一张都没发
  ③ 确认：基准 approved，params.phase=derived，其余颜色才开跑
  ④ 派生色的第 1 张图必须是**确认过的那张基准成品**（+ 面料色卡），不是原图
  ⑤ 沉淀：确认备注进该款式的 lessons，下一次生成的提示词里自动带上
  ⑥ 打回：基准不满意 → 生成改图任务，原任务继续停在待确认
  ⑦ 不重复花钱：已出图的任务不会被再跑一次

用法：python tools/check_master_gate.py [--show]
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from skillbridge import prompts, style_memory  # noqa: E402
from skillbridge.providers.base import EditRequest, Provider  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

FAILURES: list[str] = []
SLUG = "23-sorona-tee"
COLORS = ["white", "black", "red"]


def check(ok: bool, label: str, show: bool = False) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


def canvas(path: Path, color=(190, 40, 45)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (512, 512), (242, 242, 244))
    draw = ImageDraw.Draw(image)
    draw.rectangle([150, 90, 360, 430], fill=color)
    draw.text((200, 250), "FRONT", fill=(250, 250, 250))
    image.save(path)
    return path


def png_bytes(color=(30, 30, 34)) -> bytes:
    image = Image.new("RGB", (256, 256), color)
    ImageDraw.Draw(image).text((90, 120), "OUT", fill=(250, 250, 250))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class Recorder(Provider):
    """记录型接口：不发真实请求，返回固定 PNG。"""

    supports_negative = True
    max_images = 4
    size_presets = None

    def __init__(self) -> None:
        super().__init__("recorder", {"label": "recorder", "type": "mock", "ready": True})
        self.requests: list[EditRequest] = []

    def info(self) -> dict:
        return {"name": self.name, "label": "recorded", "ready": True,
                "max_images": self.max_images, "max_concurrency": 0,
                "unlimited": True, "skip_gate": True}

    async def open(self) -> None:
        return None

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        self.requests.append(request)
        return png_bytes()


def build_bench(tmp: Path) -> tuple[Workbench, Recorder]:
    """Workbench 指到临时目录 + 临时款式库副本，避免污染真实数据。"""
    bench = Workbench()
    bench.data_dir = tmp / "data"
    bench.jobs_dir = bench.data_dir / "jobs"
    bench.jobs_dir.mkdir(parents=True, exist_ok=True)
    library = tmp / "library"
    (library / "prompts").mkdir(parents=True, exist_ok=True)
    for name in ("prompts", "refs", "effects"):
        source = bench.library_dir / name
        if source.is_dir():
            shutil.copytree(source, library / name, dirs_exist_ok=True)
    shutil.copy2(bench.library_dir / "styles.json", library / "styles.json")
    bench.config.setdefault("library", {})["dir"] = str(library)
    recorder = Recorder()
    bench.get_provider = lambda name=None: recorder       # type: ignore[assignment]
    bench.gate_for = lambda provider=None: None           # type: ignore[assignment]
    bench.reload_skill()
    return bench, recorder


async def wait_job(bench: Workbench, job_id: str, timeout: float = 60.0) -> None:
    task = bench._tasks.get(job_id)
    if task is not None:
        await asyncio.wait_for(task, timeout=timeout)


async def scenario(show: bool) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="master-gate-", dir=os.environ.get("PI_SCRATCH_DIR") or None))
    try:
        bench, recorder = build_bench(tmp)
        garment = bench.skill.type_by_alias(SLUG)
        upload = canvas(tmp / "upload" / "item-01" / "view-01.png")

        print("① 建任务：多色开闸门，单色自动关")
        multi = bench.create_job(
            [("view-01.png", upload.read_bytes())],
            garment_type=SLUG,
            selections=[{"garment_type": SLUG, "colors": COLORS}],
            provider="recorder",
            subject="garment",
            review_master=True,
            concurrency=1,
        )
        check(multi.params.get("review_gate") is True, "多色任务打开了基准图闸门", show)
        masters = [t for t in multi.tasks if t.master]
        derived = [t for t in multi.tasks if t.master_task]
        check(len(masters) == 1 and len(derived) == len(COLORS) - 1,
              f"任务结构：1 基准 + {len(COLORS) - 1} 派生（实际 {len(masters)}/{len(derived)}）", show)
        single = bench.create_job(
            [("view-01.png", upload.read_bytes())],
            garment_type=SLUG,
            selections=[{"garment_type": SLUG, "colors": ["white"]}],
            provider="recorder",
            subject="garment",
            review_master=True,
            concurrency=1,
        )
        check(single.params.get("review_gate") is False, "单色任务没有闸门（没有派生色）", show)
        single.params["phase"] = "derived"
        bench.start(single.id)
        await wait_job(bench, single.id)
        check(single.status in {"done", "partial"}, f"单色任务直接跑完（{single.status}）", show)

        print("② 第一趟：只出基准，停在待确认")
        base_calls = len(recorder.requests)
        bench.start(multi.id)
        await wait_job(bench, multi.id)
        check(multi.status == "review", f"任务停在 review（实际 {multi.status}）", show)
        check(len(recorder.requests) - base_calls == len(masters),
              f"只发了基准色 {len(masters)} 次请求（实际 {len(recorder.requests) - base_calls}）", show)
        check(all(t.status == "pending" and not t.out for t in derived),
              "派生色一张都没跑（没白花钱）", show)
        review = multi.as_dict(with_tasks=False, with_log=False)["review"]
        check(review["awaiting"] is True and review["can_approve"] is True,
              f"接口回报可确认：{review['pending_ids']}", show)
        check("基准图已出" in multi.stage, f"阶段文案：{multi.stage}", show)

        print("③ 确认：按基准图改色")
        before = len(recorder.requests)
        bench.start(multi.id)            # 重复点开始不能重跑基准
        await wait_job(bench, multi.id)
        check(len(recorder.requests) == before, "重复点开始不会把基准图再买一次", show)

        await bench.approve_master(multi.id, note="袖口再窄一点，下摆别太长")
        await wait_job(bench, multi.id)
        check(len(recorder.requests) == before + len(derived),
              f"确认后只跑派生色 {len(derived)} 张（实际 {len(recorder.requests) - before}）", show)
        check(all(t.approved for t in masters), "基准图标记为已确认", show)
        check(multi.params.get("phase") == "derived", "阶段切到 derived", show)
        master_out = next((t.out for t in masters if t.out), "")
        master_file = (multi.root / str(master_out)).resolve() if master_out else None
        derived_images = [
            req.images[0].resolve() for req in recorder.requests[before:]
        ]
        check(
            all(path == master_file for path in derived_images),
            f"派生色都拿确认过的基准成品当参考图（{master_file.name if master_file else '无'}）",
            show,
        )
        check(
            all(len(req.images) >= 2 for req in recorder.requests[before:]),
            "派生色同时带面料色卡（第 2 张）",
            show,
        )
        check(multi.status in {"done", "partial"},
              f"确认后整批跑完（{multi.status} / {multi.stage[:24]}）", show)
        check(all(t.out for t in derived), "派生色都出图了", show)

        print("④ 沉淀：确认备注进该款式的 lessons")
        added = bench.learn_from_review(multi.id)
        check(bool(added), f"learn_from_review 写入 {len(added)} 条", show)
        lessons = style_memory.load_lessons(bench.library_dir, SLUG)
        check(any("袖口再窄" in str(item.get("zh")) for item in lessons),
              "该款式的 lessons 里能看到这句确认备注", show)
        bench.reload_skill()
        again = bench.skill.type_by_alias(SLUG)
        built = prompts.build_single_pass(bench.skill, again, again.colors[0], subject="garment")
        check("STYLE RULES LEARNED" in built, "下次生成的提示词自动带上款式规则", show)
        before_lessons = len(lessons)
        bench.learn_from_review(multi.id)
        check(len(style_memory.load_lessons(bench.library_dir, SLUG)) == before_lessons,
              "同一句再说一次不会重复追加", show)

        print("⑤ 打回：基准不满意 → 改图任务，原任务继续等确认")
        reject_job = await bench.reject_master(multi.id, "领口再小一点")
        check(reject_job.params.get("kind") == "rework", "打回生成的是改图任务", show)
        await wait_job(bench, reject_job.id)
        check(reject_job.status in {"done", "partial"}, f"改图任务跑完（{reject_job.status}）", show)
        check(bool(multi.params.get("master_reviews")), "原任务记下了这次打回记录", show)

        print("⑥ 采纳：把改图结果当新基准，再确认")
        new_task = next((t for t in reject_job.tasks if t.out), None)
        other = bench.create_job(
            [("view-01.png", upload.read_bytes())],
            garment_type=SLUG,
            selections=[{"garment_type": SLUG, "colors": ["white", "black"]}],
            provider="recorder",
            subject="garment",
            review_master=True,
            concurrency=1,
        )
        bench.start(other.id)
        await wait_job(bench, other.id)
        master_id = next(t.id for t in other.tasks if t.master)
        await bench.approve_master(
            other.id,
            adopt={master_id: f"{reject_job.id}::{new_task.id}"},
            note="改用打回后的版本",
        )
        await wait_job(bench, other.id)
        adopted = next(t for t in other.tasks if t.id == master_id)
        check(bool(adopted.adopted_from), f"采纳来源记录：{adopted.adopted_from}", show)
        check(other.status in {"done", "partial"}, f"采纳后按新基准改色跑完（{other.status}）", show)

        print()
        if FAILURES:
            print(f"失败 {len(FAILURES)} 条：")
            for item in FAILURES:
                print("  -", item)
            return 1
        print("全部通过：先出基准 → 确认 → 按基准改色 → 沉淀款式规则，全链路无重复计费")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    return asyncio.run(scenario("--show" in sys.argv))


if __name__ == "__main__":
    raise SystemExit(main())
