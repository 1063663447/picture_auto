"""派生色改发「面料色卡」的回归：形状只认基准，颜色只认色卡。

背景（2026-11-10 飞书 1526~1542）：21 童装索罗娜（短袖）9 色里，黑色那张跑成了带帽卫衣。
原因是派生色还在发整张效果图，模型偶尔会把效果图的**版型**一起搬过来。
现在派生色发的是从效果图裁出的 256×256 面料色卡（没有任何衣服轮廓），提示词也改成
「IMAGE 2 是一小块面料色卡，只取颜色，不许读版型」。

用法：python tools/check_swatch.py [--show]
"""
from __future__ import annotations

import asyncio
import io
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from skillbridge import prompts, swatch  # noqa: E402
from skillbridge.job import Job as JobModel, new_job_id  # noqa: E402
from skillbridge.providers.base import EditRequest, Provider  # noqa: E402
from skillbridge.runner import JobRunner  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

WORK = Path(__file__).resolve().parent.parent / "data" / "test" / "swatch"
FAILURES: list[str] = []


def check(ok: bool, label: str, show: bool) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


class Recorder(Provider):
    def __init__(self) -> None:
        super().__init__("recorder", {"label": "recorder", "type": "mock", "ready": True})
        self.max_images = 4
        self.requests: list[EditRequest] = []

    @property
    def ready(self) -> bool:
        return True

    def info(self) -> dict:
        return {"name": self.name, "label": "recorder", "ready": True, "max_images": 4,
                "max_concurrency": 0, "unlimited": True, "skip_gate": True}

    def setting(self, key, default=None):
        return default

    async def open(self) -> None:
        return None

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        self.requests.append(request)
        buf = io.BytesIO()
        # 用 256×256 的渐变图：保证 >512 字节，能过 runner 的最小体积校验
        image = Image.open(request.images[0]).convert("RGB").resize((256, 256))
        image.save(buf, format="PNG")
        return buf.getvalue()


def main() -> int:
    show = "--show" in sys.argv
    wb = Workbench()

    print("① 色卡本身")
    garment = wb.skill.type_by_alias("21-kids-sorona-tee")
    color = garment.color("black") or garment.colors[0]
    path = swatch.swatch_path(color.effect, WORK / "cache")
    check(path is not None and path.is_file(), "能生成面料色卡", show)
    if path is not None:
        with Image.open(path) as image:
            check(image.size == (swatch.SWATCH_SIZE, swatch.SWATCH_SIZE),
                  f"色卡尺寸 {image.size}（期望 {swatch.SWATCH_SIZE}²）", show)
        again = swatch.swatch_path(color.effect, WORK / "cache")
        check(again == path, "同一张效果图复用同一张色卡（有缓存）", show)

    print("② 提示词：派生色用色卡说法，基准色仍用效果图说法")
    master_prompt = prompts.build_single_pass(wb.skill, garment, color, subject="person")
    derived_prompt = prompts.build_master_recolor(color, garment=garment, subject="person",
                                                 effect=True, swatch=True)
    check("small SWATCH" in derived_prompt, "派生提示词写明 IMAGE 2 是面料色卡", show)
    check("no garment shape" in derived_prompt.lower(), "派生提示词写明色卡没有版型", show)
    check("small SWATCH" not in master_prompt, "基准提示词不提色卡", show)
    check("official product photo" in master_prompt.lower(), "基准提示词仍用官方效果图", show)
    check("no hood" in derived_prompt.lower(), "派生提示词仍带款式锁（no hood）", show)

    print("③ 真实请求：基准发效果图、派生发色卡")
    root = WORK / "job"
    shutil.rmtree(root, ignore_errors=True)
    for name in ("inputs/item-01", "work", "staging", "outputs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (240, 240), (200, 180, 160)).save(root / "inputs" / "item-01" / "item-01.png")
    job = JobModel(id=new_job_id(),
                   params={"mode": "single_pass", "provider": "recorder", "concurrency": 2,
                           "subject": "person", "send_reference": True})
    job.root = root
    provider = Recorder()
    runner = JobRunner(job, wb.skill, garment, [color, garment.colors[1]], provider, data_dir=WORK)
    asyncio.run(runner.run())
    check(len(provider.requests) == 2, f"发出 2 次请求（实际 {len(provider.requests)}）", show)
    master_task = job.tasks[0]
    master_out = master_task.out or ""
    if len(provider.requests) == 2:
        first, second = provider.requests
        check(len(first.images) == 2, "基准请求带 2 张图", show)
        check(Path(first.images[1]).name == Path(color.effect).name, "基准第 2 张是效果图", show)
        check(len(second.images) >= 2, f"派生请求至少带 2 张图（实际 {len(second.images)}）", show)
        second_ref = Path(second.images[1])
        check("swatches" in str(second_ref.parent), f"派生第 2 张来自色卡目录（{second_ref.parent.name}）", show)
        check(Path(second.images[0]).name == Path(master_out).name, "派生第 1 张是基准成品", show)
        with Image.open(second_ref) as image:
            check(image.size == (swatch.SWATCH_SIZE, swatch.SWATCH_SIZE),
                  f"派生第 2 张是 {image.size} 的色卡而非整张效果图", show)

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("全部通过：派生色只拿颜色，不再抄效果图的版型")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
