"""生成对比图：主体约束前后 / 平铺禁人 / 真人保留（4 格）。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "test" / "no-person" / "person-guard.png"

PANELS = [
    ("source: flat-lay tee (no person)", ROOT / "library" / "refs" / "23-sorona-tee" / "white.jpg"),
    ("afTER fix: garment mode -> still empty", ROOT / "data" / "test" / "no-person" / "live-a6api.png"),
    ("source: worn by a person", ROOT / "data" / "test" / "cuff" / "real-24-long-sleeve-red.png"),
    ("after fix: person mode -> same person kept", ROOT / "data" / "test" / "no-person" / "live-person-mode.png"),
]

CELL = 520
LABEL_H = 34
GAP = 14


def fit(path: Path, box: int) -> Image.Image:
    image = Image.open(path).convert("RGB")
    scale = min(box / image.width, box / image.height)
    size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    return image.resize(size, Image.LANCZOS)


def main() -> int:
    cells = []
    for label, path in PANELS:
        if not path.is_file():
            raise SystemExit(f"缺图：{path}")
        cells.append((label, fit(path, CELL)))
    width = CELL * 2 + GAP * 3
    height = (CELL + LABEL_H) * 2 + GAP * 3
    sheet = Image.new("RGB", (width, height), (22, 26, 30))
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(cells):
        col, row = index % 2, index // 2
        x = GAP + col * (CELL + GAP)
        y = GAP + row * (CELL + LABEL_H + GAP)
        draw.text((x + 4, y + 8), label, fill=(240, 244, 248))
        px = x + (CELL - image.width) // 2
        py = y + LABEL_H + (CELL - image.height) // 2
        sheet.paste(image, (px, py))
    sheet.save(OUT)
    print(f"已生成：{OUT}  {sheet.size[0]}x{sheet.size[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
