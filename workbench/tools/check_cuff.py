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
EXPECTED_PLAIN = {
    "23-sorona-tee",
    "07-washed-tee",
    "10-stretch-tee",
    "21-kids-sorona-tee",
    "24-long-sleeve",
    "25-kids-long-sleeve",
    "29-kids-washed-tee",
    "short-sleeve",
}

PLAIN_MARK = "PLAIN, FLAT"
PLAIN_NEG = "Do not add ribbed cuffs"
RIBBED_MARK = "rib-knit (1x1 rib)"
RIBBED_NEG = "Do not replace the ribbed cuffs"

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
        check(cuff in CUFF_VALUES, f"{slug}：cuff 取值合法（{cuff}）", show)
        if slug in EXPECTED_RIBBED:
            check(cuff == CUFF_RIBBED, f"{slug}：卫衣系按螺纹处理", show)
        if slug in EXPECTED_PLAIN:
            check(cuff == CUFF_PLAIN, f"{slug}：T 恤系按平口处理", show)

        color = garment.colors[0] if garment.colors else None
        if color is None:
            continue
        p = prompts.preview(skill, garment, color)
        lower = garment.phrase.lower()

        if cuff == CUFF_RIBBED:
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
