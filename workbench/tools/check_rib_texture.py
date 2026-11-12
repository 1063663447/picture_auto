"""「10 弹力短袖」罗纹感回归（9.6.27）。

客户反馈：这件是罗纹针织（原图/效果图都有竖条罗纹），但生成出来、**尤其是派生出来的其它颜色**
罗纹被磨平成普通平纹汗布。原因是款式提示词里写着 "thin cotton jersey … the only ribbed part is
the neckband"，模型照做就把罗纹去掉了。

这个脚本校验：
  * 四条提示词路径（一次到位 / 派生色 / 只换色 / 两段式第一阶段）都带 RIB-KNIT 段落；
  * 派生色那条（客户真正出问题的路径）明确写了「每个派生的颜色都要保住罗纹」；
  * 负面词写了「不许磨平成平纹 / 不许换色丢竖条」；
  * 互相打架的旧描述（thin cotton jersey、只有领口有罗纹）已经清掉；
  * 别的款式不许误带这段；款式库描述也改成了 rib-knit。

用法：python tools/check_rib_texture.py [--show]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillbridge import prompts  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

FAIL: list[str] = []
MARKERS = (
    "RIB-KNIT FABRIC TEXTURE",
    "dense, fine vertical ribs",
    "EVERY colourway",
    "every derived colourway of this batch",
    "never flatten the ribs into smooth plain single-jersey",
    "the ribbed knit is the fabric itself",
)
NEG_MARKERS = (
    "flatten the rib-knit texture into smooth plain jersey",
    "do not remove or blur the vertical rib lines",
    "do not paint the new colour as a flat matte surface without ribs",
)
OLD = ("thin cotton jersey", "the only ribbed part is the neckband")


def check(ok: bool, label: str, show: bool) -> None:
    if not ok:
        FAIL.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


def main() -> int:
    show = "--show" in sys.argv
    wb = Workbench()
    slug = "10-stretch-tee"
    garment = wb.skill.type_by_alias(slug)
    color = garment.colors[0]

    print("① 四条路径都带罗纹段落")
    paths = {
        "一次到位": prompts.build_single_pass(wb.skill, garment, color, subject="garment"),
        "派生色": prompts.build_master_recolor(color, garment=garment, subject="garment", effect=True),
        "只换色": prompts.build_recolor_only(color, garment=garment, subject="garment"),
        "两段式第一阶段": prompts.build_style_only(garment, subject="garment", effect=True),
    }
    for name, text in paths.items():
        low = text.lower()
        for mark in MARKERS:
            check(mark.lower() in low, f"{name}：含「{mark}」", show)
        for old in OLD:
            check(old.lower() not in low, f"{name}：旧的「{old}」已清掉", show)

    print("② 负面词")
    negative = prompts.full_negative(wb.skill, "preserve", garment, "garment").lower()
    for mark in NEG_MARKERS:
        check(mark.lower() in negative, f"负面词含「{mark}」", show)

    print("③ 其它款式不许误带罗纹段")
    for other in ("23-sorona-tee", "24-long-sleeve", "06-hoodie", "21-kids-sorona-tee"):
        g = wb.skill.type_by_alias(other)
        text = prompts.build_single_pass(wb.skill, g, g.colors[0], subject="garment")
        check("RIB-KNIT FABRIC TEXTURE" not in text, f"{other}：不含罗纹针织段", show)

    print("④ 款式库描述 / 文案文件")
    cat = json.loads((ROOT / "library" / "styles.json").read_text(encoding="utf-8"))
    rows = cat if isinstance(cat, list) else cat.get("styles") or []
    row = next((r for r in rows if str(r.get("slug")) == slug), {})
    check("rib-knit" in str(row.get("phrase") or ""), "styles.json 的 phrase 写的是 rib-knit", show)
    prompt_json = json.loads(
        (ROOT / "library" / "prompts" / f"{slug}.json").read_text(encoding="utf-8")
    )
    check("RIB-KNIT" in str(prompt_json.get("positive") or ""), "款式专属提示词含罗纹段", show)
    check(
        "flatten" in str(prompt_json.get("negative") or ""),
        "款式专属负面词含「不许磨平」",
        show,
    )

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 条：")
        for item in FAIL:
            print("  -", item)
        return 1
    print("全部通过：罗纹感在四条路径 + 每个派生色都钉住，别的款式不受影响")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
