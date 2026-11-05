"""生成「效果图 vs 旧输出 vs 新输出」的版型对照图（同一个颜色：深酒红）。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "test" / "effect-ref" / "shape-fit-compare.png"
PANELS = [
    ("official effect image (10TLDX burgundy)",
     ROOT / "library" / "effects" / "10-stretch-tee" / "DRD[0].jpg"),
    ("BEFORE: source silhouette kept (boxy)",
     ROOT / "data" / "jobs" / "20261105-153355-6c6d" / "outputs" / "5styles-recolor-batch"
     / "10TLDX" / "10TLDX_DRD[0]_202611056c6d.png"),
    ("AFTER: reshaped to the effect image",
     ROOT / "data" / "test" / "effect-ref" / "live-burgundy.png"),
]
CELL, GAP, LABEL = 460, 14, 26


def main() -> int:
    images = []
    for label, path in PANELS:
        if not path.is_file():
            print(f"缺图：{path}")
            return 2
        with Image.open(path) as handle:
            image = handle.convert("RGB")
        scale = min(CELL / image.width, CELL / image.height)
        images.append((label, image.resize(
            (int(image.width * scale), int(image.height * scale)), Image.LANCZOS)))

    width = CELL * len(images) + GAP * (len(images) + 1)
    height = CELL + LABEL + GAP * 2
    sheet = Image.new("RGB", (width, height), (20, 24, 28))
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(images):
        x = GAP + index * (CELL + GAP)
        draw.text((x + 4, GAP // 2), label, fill=(235, 240, 246))
        sheet.paste(image, (x + (CELL - image.width) // 2, GAP + LABEL))
    sheet.save(OUT)
    print(f"已生成：{OUT}  {sheet.size[0]}x{sheet.size[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
