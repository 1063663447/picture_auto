# -*- coding: utf-8 -*-
"""对比「已存在的款式提示词」和「按当前逻辑重新生成」的差异（只看，不写）。"""
from __future__ import annotations

import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_style_prompts import build_one  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402
from skillbridge.style_prompts import load_all  # noqa: E402


def main() -> int:
    wb = Workbench()
    on_disk = load_all(wb.library_dir)
    changed = []
    for slug in sorted(on_disk):
        try:
            fresh = build_one(wb, slug)
        except KeyError:
            print(f"  [skip] {slug}：款式不存在")
            continue
        old = on_disk[slug]
        diffs = []
        for field in ("positive", "negative", "notes"):
            if getattr(old, field) != getattr(fresh, field):
                diffs.append(field)
        if diffs:
            changed.append((slug, diffs))
    print(f"共 {len(on_disk)} 份，内容会被改变的有 {len(changed)} 份")
    for slug, diffs in changed:
        print(f"  {slug:26s} {diffs}")
    if "--show" in sys.argv and changed:
        slug = changed[0][0]
        fresh = build_one(wb, slug)
        old = on_disk[slug]
        for line in difflib.unified_diff(
            old.positive.split(". "), fresh.positive.split(". "),
            lineterm="", n=0, fromfile=f"{slug}.old", tofile=f"{slug}.new",
        ):
            print(line[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
