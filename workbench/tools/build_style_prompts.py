"""为每个款式生成一套专属默认提示词（library/prompts/<slug>.json）。

用法：
    python tools/build_style_prompts.py --all            # 按当前逻辑生成/刷新全部款式
    python tools/build_style_prompts.py --all --force    # 覆盖已手改过的文件（会先备份 .bak）
    python tools/build_style_prompts.py --new 24-cxcr    # 只生成一个新款式（用于以后新增款式）
    python tools/build_style_prompts.py --list           # 看哪些款式有/没有专属提示词

生成内容是「款式专属核心」：
  positive = 款式转换段 + 款式锁 + 袖长 + 袖口工艺 +（连帽款）帽绳色系
  negative = 该款式的袖长/袖口/帽兜/帽绳否定词
共享骨架（图案锁、材质锁、效果图/色卡分工、主体规则）仍在代码里，不重复进每个文件。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import prompts  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402
from skillbridge.style_prompts import (  # noqa: E402
    StylePrompt,
    load_all,
    save_style_prompt,
    write_index,
)

NOTES = {
    "23-sorona-tee": "索罗娜短袖：宽松落肩、短袖、平口折边",
    "07-washed-tee": "水洗T恤：oversize 落肩、水洗做旧面料、平口",
    "10-stretch-tee": "弹力短袖：紧身弹力针织、贴身、平口",
    "21-kids-sorona-tee": "童装索罗娜：儿童宽松短袖、平口",
    "24-long-sleeve": "长袖成人：常规直筒、袖到腕、平口折边（无罗纹）",
    "25-kids-long-sleeve": "长袖童装：儿童常规长袖、平口折边（无罗纹）",
    "29-kids-washed-tee": "水洗童装：儿童 oversize 水洗短袖、平口",
    "03-crewneck-sweatshirt": "圆领卫衣：无帽、罗纹领口/袖口/下摆、抓绒",
    "28-kids-sweatshirt": "卫衣童装：儿童圆领卫衣、罗纹袖口下摆、无帽",
    "06-hoodie": "连帽卫衣：帽子+帽绳（绳与衣身同色系）、袋鼠兜、罗纹袖口下摆",
}


def build_one(wb: Workbench, slug: str) -> StylePrompt:
    garment = wb.skill.type_by_alias(slug)
    positive = " ".join(
        part for part in (
            prompts.convert_clause(garment),
            prompts.garment_lock(garment),
            prompts.sleeve_clause(garment),
            prompts.cuff_clause(garment, reference=True),
            prompts.drawstring_clause(garment),
        ) if part
    )
    negative = prompts.style_negative(
        # 用「按属性拼」的版本生成一次，避免读到文件本身（幂等）
        type(garment)(**{**garment.__dict__, "style_negative": ""})
    )
    return StylePrompt(
        slug=slug,
        positive=" ".join(positive.split()),
        negative=" ".join(negative.split()),
        notes=NOTES.get(slug, ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="生成/刷新全部款式")
    parser.add_argument("--new", default="", help="只生成指定 slug（新款式用）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的文件（先备份 .bak）")
    parser.add_argument("--list", action="store_true", help="查看覆盖情况")
    args = parser.parse_args()

    wb = Workbench()
    library_dir = wb.library_dir
    existing = load_all(library_dir)

    if args.list:
        print(f"款式库：{library_dir}")
        for slug in sorted(wb.skill.types):
            garment = wb.skill.types[slug]
            mark = "有" if slug in existing else "缺"
            extra = "（skill 自带）" if not getattr(garment, "ref_dir", None) else ""
            print(f"  [{mark}] {slug:26s} {garment.display_name}{extra}")
        print(f"\n共 {len(wb.skill.types)} 款，已有专属提示词 {len(existing)} 份")
        return 0

    targets = sorted(wb.skill.types) if args.all else ([args.new] if args.new else [])
    if not targets:
        print("没说做什么：用 --all / --new <slug> / --list")
        return 2

    written = []
    for slug in targets:
        try:
            garment = wb.skill.type_by_alias(slug)
        except KeyError:
            print(f"  [skip] 款式不存在：{slug}")
            continue
        # skill 自带的通用款（短袖/圆领卫衣/连帽卫衣）也可以生成，但优先按款式库算
        target_slug = garment.slug
        path = library_dir / "prompts" / f"{target_slug}.json"
        if path.is_file() and not args.force:
            print(f"  [keep] {target_slug}（已存在，--force 才覆盖）")
            continue
        if path.is_file():
            shutil.copy2(path, path.with_suffix(".json.bak"))
        prompt = build_one(wb, target_slug)
        written.append(save_style_prompt(library_dir, prompt))
        print(f"  [ok] {target_slug}: positive {len(prompt.positive)} 字 / negative {len(prompt.negative)} 字")

    all_prompts = load_all(library_dir)
    index = write_index(library_dir, all_prompts)
    print(f"\n写出 {len(written)} 份 → {library_dir / 'prompts'}")
    print(f"索引：{index}（共 {len(all_prompts)} 份）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
