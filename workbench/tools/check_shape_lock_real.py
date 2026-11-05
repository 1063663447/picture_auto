"""真接口 A/B：同一张原图、同一个款式 3 个颜色，「每色独立生成」vs「同款版型锁定」。

用途：客户反馈「同款各色版型不一样」时，跑一遍这个脚本就能看出差异，以及锁定后有多少改善。
花费：a6api 单张 ≈ ¥0.006，本脚本固定 6 张（3 色 × 2 组）≈ ¥0.04，约 40~90 秒。

用法：
    python tools/check_shape_lock_real.py --yes
    python tools/check_shape_lock_real.py --yes --source=data/jobs/<id>/inputs/item-01/xxx.png
    python tools/check_shape_lock_real.py --yes --colors=burgundy,pink,heather-grey
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from skillbridge import prompts  # noqa: E402
from skillbridge.providers import build_providers  # noqa: E402
from skillbridge.providers.base import EditRequest  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "data" / "test" / "shape-lock"
DEFAULT_SOURCE = ROOT / "data" / "jobs" / "20261105-145851-c74f" / "inputs" / "item-01" / "屏幕截图-2026-11-05-143652.png"
SHEET = WORK / "shape-lock-ab.png"


def arg(name: str, fallback: str) -> str:
    for item in sys.argv:
        if item.startswith(f"--{name}="):
            return item.split("=", 1)[1]
    return fallback


async def generate(provider, requests: list[tuple[str, EditRequest]]) -> dict[str, bytes]:
    await provider.open()
    try:
        results: dict[str, bytes] = {}
        for label, request in requests:
            results[label] = await provider.edit(request, 180.0)
        return results
    finally:
        await provider.close()


def contact_sheet(rows: dict[str, dict[str, bytes]], colors: list[str]) -> Path:
    cell, gap, label_h = 380, 12, 26
    width = cell * len(colors) + gap * (len(colors) + 1)
    height = (cell + label_h) * len(rows) + gap * (len(rows) + 1)
    sheet = Image.new("RGB", (width, height), (20, 24, 28))
    draw = ImageDraw.Draw(sheet)
    for row_index, (title, images) in enumerate(rows.items()):
        y = gap + row_index * (cell + label_h + gap)
        draw.text((gap + 4, y + 6), title, fill=(235, 240, 246))
        for col_index, color in enumerate(colors):
            x = gap + col_index * (cell + gap)
            data = images.get(color)
            if not data:
                continue
            import io

            with Image.open(io.BytesIO(data)) as handle:
                image = handle.convert("RGB")
            scale = min(cell / image.width, cell / image.height)
            image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
            sheet.paste(image, (x + (cell - image.width) // 2, y + label_h))
            draw.text((x + 4, y + label_h - 12), color, fill=(150, 200, 180))
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(SHEET)
    return SHEET


async def main() -> int:
    if "--yes" not in sys.argv:
        print("会真实调用接口（6 张 ≈ ¥0.04）。确认后再跑：python tools/check_shape_lock_real.py --yes")
        return 2
    source = Path(arg("source", str(DEFAULT_SOURCE)))
    if not source.is_file():
        print(f"原图不存在：{source}")
        return 2
    colors = [c.strip() for c in arg("colors", "burgundy,pink,heather-grey").split(",") if c.strip()]

    wb = Workbench()
    garment = wb.skill.type_by_alias("10-stretch-tee")
    picked = []
    for label in colors:
        color = garment.color(label)
        if color is None:
            print(f"款式 {garment.display_name} 没有颜色 {label}，可选：{garment.color_labels()}")
            return 2
        picked.append(color)

    providers = build_providers(wb.config, wb.env)
    provider = providers.get("a6api")
    if provider is None:
        print("未配置 a6api 接口")
        return 2
    model = str(provider.cfg.get("model") or "")
    negative = prompts.full_negative(wb.skill, "preserve", garment, "garment")

    def request(images, prompt) -> EditRequest:
        return EditRequest(
            images=list(images), prompt=prompt, negative=negative,
            size="auto", quality="high", model=model,
        )

    # A 组：每色独立生成（旧行为）
    group_a = {
        color.label: request(
            [source, color.path],
            prompts.build_single_pass(wb.skill, garment, color, subject="garment"),
        )
        for color in picked
    }

    # B 组：先出基准色，其余以基准成品为参考图只改色（新行为）
    master = picked[0]
    master_request = request(
        [source, master.path],
        prompts.build_single_pass(wb.skill, garment, master, subject="garment"),
    )
    await provider.open()
    try:
        print(f"B 组：先出基准色 {master.label} …")
        master_data = await provider.edit(master_request, 180.0)
        master_path = WORK / f"master-{master.code}.png"
        master_path.parent.mkdir(parents=True, exist_ok=True)
        master_path.write_bytes(master_data)
        group_b = {master.label: master_data}
        for color in picked[1:]:
            prompt = (
                prompts.build_recolor_only(
                    color, pattern_mode="preserve", garment=garment, subject="garment"
                )
                + " "
                + prompts.SILHOUETTE_LOCK_CLAUSE
            )
            print(f"B 组：{color.label} 按基准改色 …")
            group_b[color.label] = await provider.edit(
                request([master_path, color.path], prompt), 180.0
            )
    finally:
        await provider.close()

    # A 组也补跑（放后面，避免占着并发）
    group_a_data = await generate(provider, [(c.label, group_a[c.label]) for c in picked])

    for index, color in enumerate(picked):
        (WORK / f"a-{index + 1}-{color.code}.png").write_bytes(group_a_data[color.label])
        (WORK / f"b-{index + 1}-{color.code}.png").write_bytes(group_b[color.label])
    labels = [color.label for color in picked]
    sheet = contact_sheet(
        {
            "A  old: each colour generated on its own": group_a_data,
            "B  new: shape locked to the master colour": group_b,
        },
        labels,
    )
    print(f"完成：{sheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
