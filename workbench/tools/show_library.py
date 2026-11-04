"""打印款式库清单（颜色 / 色值 / 是否有参考图）。"""
from __future__ import annotations

import json
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "library"


def main() -> int:
    data = json.loads((LIB / "styles.json").read_text(encoding="utf-8"))
    total = 0
    for style in data:
        print(f"{style['display_name']:<14} {style['slug']:<26} 颜色 {len(style['colors'])}")
        for color in style["colors"]:
            total += 1
            flag = "" if color["image"] else "   ← 无图"
            print(
                f"    {color['label']:<14} {color['name_zh']:<6} {color['color_code']:<9}"
                f"{color['hex']:<9}{flag}"
            )
    print(f"\n款式 {len(data)} · 颜色 {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
