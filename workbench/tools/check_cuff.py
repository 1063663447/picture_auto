"""验证「袖口工艺」这一层：T 恤平口、卫衣螺纹，提示词和否定词都跟着款式走。

背景：生图模型会把长袖 T 恤画成卫衣的罗纹袖口（客户一眼就看出不是这件衣服）。
这里把规则钉成可回归的断言，改款式/改提示词后跑一遍就知道有没有退化。

用法：
    python tools/check_cuff.py            # 纯离线断言
    python tools/check_cuff.py --show     # 额外打印每个款式的袖口约束摘要
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import prompts  # noqa: E402
from skillbridge.library import phrase_for_cuff  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402
from skillbridge.skill import CUFF_PLAIN, CUFF_RIBBED, CUFF_VALUES  # noqa: E402

EXPECTED_RIBBED = {
    "03-crewneck-sweatshirt",
    "06-hoodie",
    "28-kids-sweatshirt",
    "crewneck-sweatshirt",
    "hoodie",
}
EXPECTED_LONG_SLEEVE = {
    "24-long-sleeve", "25-kids-long-sleeve",
    "03-crewneck-sweatshirt", "06-hoodie", "28-kids-sweatshirt",
    "crewneck-sweatshirt", "hoodie",
}

EXPECTED_PLAIN = {
    "01-cotton-tee",
    "04-milk-silk-tee",
    "05-cropped-tee",
    "07-washed-tee",
    "09-kids-cotton-tee",
    "10-stretch-tee",
    "11-kids-dolman-tee",
    "12-curved-hem-tee",
    "17-kids-cropped-tee",
    "19-u-neck-tee",
    "20-distressed-tee",
    "21-kids-sorona-tee",
    "23-sorona-tee",
    "24-long-sleeve",
    "25-kids-long-sleeve",
    "26-cropped-sorona-tee",
    "29-kids-washed-tee",
    "31-ink-wash-tee",
    "short-sleeve",
}
#: 无袖（背心/挂脖）：没有袖口，只有袖笼 + 领口，提示词换成袖笼那一段
EXPECTED_VEST = {"08-tank-top", "13-mens-tank", "18-halter-tank"}
#: 下装（短裤）：整件衣服换成了裤子，腰头 + 两个裤腿
EXPECTED_BOTTOM: set[str] = set()        # 客户口径：库里没有裤子（15/16 已移出）

LONG_MARK = "FULL-LENGTH LONG SLEEVES"
SHORT_MARK = "GENUINE SHORT SLEEVES"
NONE_MARK = "NO SLEEVES AT ALL"
LEG_MARK = "SHORTS, NOT A TOP"
LONG_NEG = "the sleeves must reach the wrists"
SHORT_NEG = "the sleeves must end above the elbow"
NONE_NEG = "Do not add sleeves"
LEG_NEG = "Do not turn the shorts into a T-shirt"

PLAIN_MARK = "PLAIN, FLAT"
PLAIN_NEG = "Do not add ribbed cuffs"
RIBBED_MARK = "rib-knit (1x1 rib)"
RIBBED_NEG = "Do not replace the ribbed cuffs"
VEST_HEM_MARK = "ARMHOLE & HEM CONSTRUCTION"
VEST_HEM_NEG = "Do not add a rib-knit or elastic band to the armholes"
BOTTOM_HEM_MARK = "HEM & WAIST CONSTRUCTION"
BOTTOM_HEM_NEG = "Do not add ribbed leg cuffs"

#: 2026-11-10：卫衣 promo 图改成长袖童装时，袖口还是罗纹 —— 现在提示词第一段是「款式转换」，
#: 平口款必须写明去掉罗纹收口，罗纹款反过来要求补上。
#: 2026-11-11（菲律宾款式表）：背心没有袖子、短裤没有袖口，转换段也换成对应措辞。
CONVERT_MARK = "CONVERT THE GARMENT TYPE"


def check_convert(garment, prompt: str, style_only: str, show: bool) -> None:
    check(prompt.find(CONVERT_MARK) >= 0,
          f"{garment.slug}：提示词含款式转换段", show)
    check(style_only.find(CONVERT_MARK) >= 0,
          f"{garment.slug}：两段式第一阶段也有转换段", show)
    low = prompt.lower()
    kind = getattr(garment, "kind", "")
    if kind == "bottom":
        check("remove that top completely" in low, f"{garment.slug}：下装要求把上衣整件换掉", show)
    elif kind == "vest" or str(getattr(garment, "sleeve", "")) == "none":
        check("cut the sleeves off completely" in low, f"{garment.slug}：背心要求把袖子去掉", show)
    elif garment.cuff == CUFF_RIBBED:
        check("rib-knit cuffs" in low and "remove all of that" not in low,
              f"{garment.slug}：罗纹款要求补罗纹带", show)
    else:
        check("remove all of that" in low, f"{garment.slug}：平口款要求去掉罗纹收口", show)
        check("plain self-fabric folded hems" in low, f"{garment.slug}：写明改成平折边", show)


FAILURES: list[str] = []


def check(ok: bool, label: str, show: bool) -> None:
    """label 用肯定句描述断言（「…有平口约束」），失败时才报错。"""
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


def main() -> int:
    show = "--show" in sys.argv
    wb = Workbench()
    skill = wb.skill
    types = skill.types
    print(f"款式 {len(types)} 个")

    for slug, garment in sorted(types.items()):
        cuff = garment.cuff
        kind = getattr(garment, "kind", "top")
        sleeve = getattr(garment, "sleeve", "")
        check(cuff in CUFF_VALUES, f"{slug}：cuff 取值合法（{cuff}）", show)
        prompt_single = prompts.build_single_pass(skill, garment, garment.colors[0], subject="person")
        style_only = prompts.build_style_only(garment, subject="person", effect=True)
        check_convert(garment, prompt_single, style_only, show)
        if slug in EXPECTED_RIBBED:
            check(cuff == CUFF_RIBBED, f"{slug}：卫衣系按螺纹处理", show)
        if slug in EXPECTED_PLAIN:
            check(cuff == CUFF_PLAIN, f"{slug}：T 恤系按平口处理", show)
        if slug in EXPECTED_VEST:
            check(kind == "vest", f"{slug}：背心按无袖（vest）处理", show)
        if slug in EXPECTED_BOTTOM:
            check(kind == "bottom", f"{slug}：短裤按下装（bottom）处理", show)

        color = garment.colors[0] if garment.colors else None
        if color is None:
            continue
        p = prompts.preview(skill, garment, color)
        lower = garment.phrase.lower()

        if kind == "bottom":
            check("ribbed cuff" not in lower, f"{slug}：版型描述不提袖口", show)
            check(BOTTOM_HEM_MARK in p["single_pass"], f"{slug}：single_pass 有腰头/裤脚约束", show)
            check(BOTTOM_HEM_MARK in p["style_only"], f"{slug}：style_only 有腰头/裤脚约束", show)
            check(BOTTOM_HEM_NEG in p["negative"], f"{slug}：否定词禁止罗纹裤脚", show)
        elif kind == "vest" or sleeve == "none":
            check("ribbed cuff" not in lower, f"{slug}：版型描述不提袖口", show)
            check(VEST_HEM_MARK in p["single_pass"], f"{slug}：single_pass 有袖笼/下摆约束", show)
            check(VEST_HEM_MARK in p["style_only"], f"{slug}：style_only 有袖笼/下摆约束", show)
            check(VEST_HEM_NEG in p["negative"], f"{slug}：否定词禁止给袖笼加罗纹", show)
        elif cuff == CUFF_RIBBED:
            check("ribbed" in lower, f"{slug}：版型描述写明 ribbed", show)
            check(RIBBED_MARK in p["single_pass"], f"{slug}：single_pass 有螺纹约束", show)
            check(RIBBED_MARK in p["style_only"], f"{slug}：style_only 有螺纹约束", show)
            check(RIBBED_NEG in p["negative"], f"{slug}：否定词保住螺纹", show)
        else:
            # 平口款式：版型描述不许自己说 ribbed cuff（自相矛盾模型必挑一个照做）
            check("ribbed cuff" not in lower, f"{slug}：版型描述不提 ribbed cuffs", show)
            check(PLAIN_MARK in p["single_pass"], f"{slug}：single_pass 有平口约束", show)
            check(PLAIN_MARK in p["style_only"], f"{slug}：style_only 有平口约束", show)
            check(PLAIN_MARK in p["recolor_only"], f"{slug}：recolor_only 有平口约束", show)
            check(PLAIN_NEG in p["negative"], f"{slug}：否定词禁止加罗纹", show)
            # 换色提示词不该再劝模型保留 ribbing
            check(
                "seams, ribbing, cuffs" not in p["recolor_only"],
                f"{slug}：recolor_only 不再提 ribbing",
                show,
            )

        # 袖长：长袖款必须写长袖、短袖款必须写短袖，背心/短裤必须写「没有袖子」
        if kind == "top":
            check(sleeve in ("long", "short"), f"{slug}：sleeve 取值合法（{sleeve}）", show)
        else:
            check(sleeve == "none", f"{slug}：无袖/下装 sleeve 必须是 none（{sleeve}）", show)
        if slug in EXPECTED_LONG_SLEEVE:
            check(sleeve == "long", f"{slug}：按长袖处理", show)
        elif slug in EXPECTED_PLAIN:
            check(sleeve == "short", f"{slug}：按短袖处理", show)
        if kind == "bottom":
            check(LEG_MARK in p["single_pass"], f"{slug}：single_pass 写明是短裤不是上衣", show)
            check(LEG_MARK in p["style_only"], f"{slug}：style_only 写明是短裤不是上衣", show)
            check(LEG_NEG in p["negative"], f"{slug}：否定词禁止变回上衣", show)
        elif kind == "vest" or sleeve == "none":
            check(NONE_MARK in p["single_pass"], f"{slug}：single_pass 写明没有袖子", show)
            check(NONE_MARK in p["style_only"], f"{slug}：style_only 写明没有袖子", show)
            check(NONE_NEG in p["negative"], f"{slug}：否定词禁止加袖子", show)
        elif sleeve == "long":
            check(LONG_MARK in p["single_pass"], f"{slug}：single_pass 有长袖子句", show)
            check(LONG_MARK in p["style_only"], f"{slug}：style_only 有长袖子句", show)
            check(LONG_NEG in p["negative"], f"{slug}：否定词禁止短袖", show)
        else:
            check(SHORT_MARK in p["single_pass"], f"{slug}：single_pass 有短袖子句", show)
            check(SHORT_MARK in p["style_only"], f"{slug}：style_only 有短袖子句", show)
            check(SHORT_NEG in p["negative"], f"{slug}：否定词禁止长袖", show)

        # 版型描述自带冠词，模板又写了 a/an → 会出现 "a a long-sleeve"
        for key in ("single_pass", "style_only", "recolor_only"):
            check(
                not any(bad in p[key] for bad in (" a a ", " a an ", " an a ", " an an ")),
                f"{slug}：{key} 没有双冠词",
                show,
            )

        if show:
            print(f"    → {slug:<24} {garment.cuff_zh}")

    # 描述校正器：平口款式里残留的螺纹措辞必须被改掉
    fixed = phrase_for_cuff(
        "a long-sleeve t-shirt (cotton jersey, ribbed cuffs and neckband)", CUFF_PLAIN
    )
    check("ribbed cuff" not in fixed.lower(), f"描述校正去掉 ribbed：{fixed}", show)
    check("plain flat cuffs" in fixed.lower(), f"描述校正补上平口：{fixed}", show)
    kept = phrase_for_cuff("a crewneck sweatshirt (ribbed cuffs and ribbed hem)", CUFF_RIBBED)
    check("ribbed cuffs" in kept.lower(), "螺纹款式的描述不被改动", show)

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项")
        return 1
    print("全部通过：平口/螺纹按款式正确注入提示词与否定词")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
