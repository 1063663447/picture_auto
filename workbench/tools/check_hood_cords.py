"""连帽卫衣帽绳回归：绳身跟衣身同色、两个绳头是银色金属头、两根一模一样。

背景：
  * 2026-11-10 客户第一轮反馈「连帽卫衣的带子容易串」= **颜色串色**（黑卫衣画出白帽绳）；
  * 2026-11-12 客户拿对照片补规格：**整根绳子跟衣服同色，绳子最下面两个头是银色的金属头**，
    两根必须一模一样（之前会一根深一根浅、绳头发毛/打结、材质画成石头纹）。

连帽款注入 `HOOD DRAWSTRING`（色 + 银头 + 双胞胎三条一起钉），其它款式反过来必须写
「no drawstrings」。

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
    "HOOD DRAWSTRING",
    "SAME colour as THIS hoodie's fabric",
    "white hoodie gets white cords",
    "SILVER METAL AGLET",
    "one per cord, both tips silver",
    "identical twins",
    "belong to the OLD colourway",
    "never copy",
)
NEGATIVE_MARKS = (
    "do not colour the cord body anything other than this garment's own fabric colour",
    "polished silver metal",
    "do not make the two cords differ",
    "frayed, tasselled, knotted",
    "stone, marble, burlap",
)


def check(ok: bool, label: str, show: bool) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


def main() -> int:
    show = "--show" in sys.argv
    wb = Workbench()

    print("① 连帽款：四条路径都要有帽绳规格（同色 + 银头 + 双胞胎）")
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
            check("HOOD DRAWSTRING" in text, f"{slug} / {name}：含帽绳规格", show)
            for mark in MARKS:
                check(mark in text, f"{slug} / {name}：写明「{mark}」", show)
            check("HOOD CORD COLOUR" not in text, f"{slug} / {name}：旧版「同色系」文案已退休", show)
        negative = prompts.full_negative(wb.skill, "preserve", garment, "person").lower()
        for mark in NEGATIVE_MARKS:
            check(mark in negative, f"{slug}：负面词含「{mark}」", show)
        check("do not make the cords glossy or metallic" not in negative,
              f"{slug}：不再无差别禁金属（绳头本来就是银色）", show)
        check(prompts.is_hooded(garment), f"{slug}：被识别为连帽款", show)
        # 款式专属提示词（library/prompts/06-hoodie.json）也得是新规格
        style_pos = str(getattr(garment, "style_positive", "") or "")
        style_neg = str(getattr(garment, "style_negative", "") or "")
        if style_pos:
            for mark in ("HOOD DRAWSTRING", "SILVER METAL AGLET", "identical twins"):
                check(mark in style_pos, f"{slug}：专属提示词含「{mark}」", show)
            check("HOOD CORD COLOUR" not in style_pos, f"{slug}：专属提示词里旧文案已清掉", show)
        if style_neg:
            for mark in ("polished SILVER metal", "frayed, tasselled, knotted"):
                check(mark in style_neg, f"{slug}：专属负面词含「{mark}」", show)

    print("② 非连帽款：不许出现帽绳规格，且必须禁掉抽绳")
    for slug in ("23-sorona-tee", "03-crewneck-sweatshirt", "24-long-sleeve",
                 "10-stretch-tee", "21-kids-sorona-tee"):
        garment = wb.skill.type_by_alias(slug)
        text = prompts.build_single_pass(wb.skill, garment, garment.colors[0], subject="person")
        negative = prompts.full_negative(wb.skill, "preserve", garment, "person")
        check(not prompts.is_hooded(garment), f"{slug}：不是连帽款", show)
        check("HOOD DRAWSTRING" not in text, f"{slug}：提示词不含帽绳规格", show)
        check("no drawstrings" in text.lower(), f"{slug}：提示词要求没有抽绳", show)
        check("silver metal aglet" not in text.lower(), f"{slug}：不提银头（会误导模型加抽绳）", show)
        check("colour family" not in negative.lower(), f"{slug}：负面词不提帽绳色系", show)

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("全部通过：连帽款钉住「绳身同色 + 绳头银色 + 两根一致」，其它款式禁抽绳")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
