"""连帽卫衣帽绳回归：两条绳不能交叉、打结、穿过图案。

背景（2026-11-10）：客户反馈「连帽卫衣的带子容易串」—— 客户指的是**颜色串色**（黑卫衣画出白帽绳）。现在连帽款会注入
`HOOD CORD COLOUR` 约束（绳色跟衣身同色系）+ 对应的否定词，
其它款式反过来必须写「no drawstrings」。

用法：python tools/check_hood_cords.py [--show]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import prompts  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

FAILURES: list[str] = []
HOODED = {"06-hoodie"}
MARKS = (
    "HOOD CORD COLOUR",
    "SAME colour as THIS hoodie's fabric",
    "black or charcoal hoodie gets black / charcoal cords",
    "belongs to the OLD colourway",
    "must be REPLACED by this colourway's colour",
)
NEGATIVE_MARKS = ("outside the garment's colour family", "white or silver cords on a dark hoodie", "another colourway")


def check(ok: bool, label: str, show: bool) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


def main() -> int:
    show = "--show" in sys.argv
    wb = Workbench()

    print("① 连帽款：四条路径都要有帽绳约束")
    for slug in sorted(HOODED):
        garment = wb.skill.type_by_alias(slug)
        color = garment.colors[0]
        paths = {
            "一次到位": prompts.build_single_pass(wb.skill, garment, color, subject="person"),
            "派生色": prompts.build_master_recolor(color, garment=garment, subject="person", effect=True),
            "只换色": prompts.build_recolor_only(color, garment=garment, subject="person"),
            "两段式第一阶段": prompts.build_style_only(garment, subject="person", effect=True),
        }
        for name, text in paths.items():
            check("HOOD CORD COLOUR" in text, f"{slug} / {name}：含帽绳色系约束", show)
            for mark in MARKS:
                check(mark in text, f"{slug} / {name}：写明「{mark}」", show)
        negative = prompts.full_negative(wb.skill, "preserve", garment, "person")
        for mark in NEGATIVE_MARKS:
            check(mark in negative.lower(), f"{slug}：负面词含「{mark}」", show)
        check(prompts.is_hooded(garment), f"{slug}：被识别为连帽款", show)

    print("② 非连帽款：不许出现帽绳约束，且必须禁掉抽绳")
    for slug in ("23-sorona-tee", "03-crewneck-sweatshirt", "24-long-sleeve",
                 "10-stretch-tee", "21-kids-sorona-tee"):
        garment = wb.skill.type_by_alias(slug)
        text = prompts.build_single_pass(wb.skill, garment, garment.colors[0], subject="person")
        negative = prompts.full_negative(wb.skill, "preserve", garment, "person")
        check(not prompts.is_hooded(garment), f"{slug}：不是连帽款", show)
        check("HOOD CORD COLOUR" not in text, f"{slug}：提示词不含帽绳色系约束", show)
        check("no drawstrings" in text.lower(), f"{slug}：提示词要求没有抽绳", show)
        check("colour family" not in negative.lower(), f"{slug}：负面词不提帽绳色系", show)

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("全部通过：连帽款钉住帽绳，其它款式禁抽绳")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
