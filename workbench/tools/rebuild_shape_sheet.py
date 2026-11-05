"""从已保存的 a-/b- 成品重建 A/B 对照图（不重新调用接口）。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

WORK = Path(__file__).resolve().parent.parent / "data" / "test" / "shape-lock"
ORDER = ["DRD[0]", "PK[0]", "FGY[0]"]          # 深酒红 / 粉色 / 花灰（color.code 带色号位）
LABELS = {"DRD[0]": "burgundy", "PK[0]": "pink", "FGY[0]": "heather-grey"}


def main() -> int:
    rows = {
        "A  old: each colour generated on its own": [WORK / f"a-{i + 1}-{code}.png" for i, code in enumerate(ORDER)],
        "B  new: shape locked to the master colour": [WORK / f"b-{i + 1}-{code}.png" for i, code in enumerate(ORDER)],
    }
    cell, gap, label_h = 380, 12, 26
    width = cell * len(ORDER) + gap * (len(ORDER) + 1)
    height = (cell + label_h) * len(rows) + gap * (len(rows) + 1)
    sheet = Image.new("RGB", (width, height), (20, 24, 28))
    draw = ImageDraw.Draw(sheet)
    for row_index, (title, files) in enumerate(rows.items()):
        y = gap + row_index * (cell + label_h + gap)
        draw.text((gap + 4, y + 6), title, fill=(235, 240, 246))
        for col_index, path in enumerate(files):
            x = gap + col_index * (cell + gap)
            if not path.is_file():
                continue
            with Image.open(path) as handle:
                image = handle.convert("RGB")
            scale = min(cell / image.width, cell / image.height)
            image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
            sheet.paste(image, (x + (cell - image.width) // 2, y + label_h))
            draw.text((x + 4, y + label_h - 12), LABELS[ORDER[col_index]], fill=(150, 200, 180))
    out = WORK / "shape-lock-ab.png"
    sheet.save(out)
    print(f"已重建：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
