# -*- coding: utf-8 -*-
"""打印某个款式某个颜色的最终提示词（人工复核用）。

用法：python tools/show_prompt.py 16-sports-shorts black
      python tools/show_prompt.py 08-tank-top white --raw
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import prompts  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python tools/show_prompt.py <slug> [颜色] [--raw]")
        return 2
    slug = sys.argv[1]
    color = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else ""
    raw = "--raw" in sys.argv
    wb = Workbench()
    garment = wb.skill.type_by_alias(slug)
    ref = garment.color(color) if color else garment.colors[0]
    if ref is None:
        print(f"{slug} 没有颜色 {color}；可选：{[c.label for c in garment.colors]}")
        return 2
    out = prompts.build_single_pass(wb.skill, garment, ref, subject="garment")
    negative = prompts.full_negative(wb.skill, "preserve", garment, "garment")
    print(f"=== {garment.slug} / {ref.label} ({ref.code}) "
          f"{garment.sleeve_zh}/{garment.cuff_zh}/{garment.kind_zh} ===")
    print(f"正文 {len(out)} 字 · 负面 {len(negative)} 字")
    print()
    if raw:
        print(out)
    else:
        for index, block in enumerate(out.split(". "), start=1):
            print(textwrap.fill(block + ".", 108))
            print()
    print("--- 负面 ---")
    print(textwrap.fill(negative, 108))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
