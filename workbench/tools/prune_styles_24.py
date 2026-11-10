# -*- coding: utf-8 -*-
"""按客户口径修正款式数量：菲律宾这张表就是 **24 个款式**，没有「成人牛角袖」和「裤子」。

客户原话：「这里面总共应该是24个款式，并没有什么成人牛角袖和裤子」。

核对表结构后确认：工作簿里的「目录」页只列 24 格（23 个不同代号 + 06 重了一格），
01 / 09 / 14 / 15 / 16 这 5 个 sheet **不在目录里**；客户只点名排除「成人牛角袖」和「裤子」，
即 14 成人牛角袖、15 中短裤、16 运动短裤 —— 27-3 = 24，与客户口径一致。

因此：
  1. 导入器加 EXCLUDE_CODES（14/15/16）：跳过这几个 sheet，重跑也不会再把它们导进来
  2. 已入库的这三款连同效果图/缩略图/款式提示词一起清掉（留一份到 scratch 备份）
  3. 「目录里有、但工作簿没有 sheet」的 30 扎染T 单独记一笔（没有颜色和效果图，无法建档）

用法：python tools/prune_styles_24.py [--dry-run]
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"
STYLES = LIB / "styles.json"
BACKUP = ROOT.parent / "scratch" / "pruned-styles-9.6.1"

DROP_CODES = {"14", "15", "16"}
DROP_SLUGS = {"14-dolman-tee", "15-mid-shorts", "16-sports-shorts"}


def main() -> int:
    dry = "--dry-run" in sys.argv
    styles = json.loads(STYLES.read_text(encoding="utf-8"))
    keep: list[dict] = []
    drop: list[dict] = []
    for style in styles:
        code = str(style.get("style_code") or "").strip()
        slug = str(style.get("slug") or "")
        if code in DROP_CODES or slug in DROP_SLUGS:
            drop.append(style)
        else:
            keep.append(style)
    colours_before = sum(len(s.get("colors") or []) for s in styles)
    colours_after = sum(len(s.get("colors") or []) for s in keep)
    print(f"现有 {len(styles)} 款 / {colours_before} 色")
    print(f"要移除 {len(drop)} 款 / {colours_before - colours_after} 色：")
    for style in drop:
        print(f"  - {style.get('style_code')} {style.get('slug')} "
              f"({len(style.get('colors') or [])} 色)")
    print(f"保留 {len(keep)} 款 / {colours_after} 色")
    if dry:
        return 0

    BACKUP.mkdir(parents=True, exist_ok=True)
    (BACKUP / "prompts").mkdir(parents=True, exist_ok=True)
    (BACKUP / "styles.json").write_text(
        json.dumps(styles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for style in drop:
        slug = str(style.get("slug") or "")
        for folder in ("effects", "refs"):
            source = LIB / folder / slug
            if source.is_dir():
                shutil.copytree(source, BACKUP / folder / slug, dirs_exist_ok=True)
                shutil.rmtree(source)
        prompt = LIB / "prompts" / f"{slug}.json"
        if prompt.is_file():
            shutil.copy2(prompt, BACKUP / "prompts" / prompt.name)
            prompt.unlink()

    STYLES.write_text(
        json.dumps(keep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nstyles.json 已写回：{len(keep)} 款 / {colours_after} 色")
    print(f"备份（含图与提示词）：{BACKUP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
