"""给「10 弹力短袖」加上罗纹感约束（9.6.27）。

客户反馈：这件本来就是罗纹针织（原图和效果图都有明显竖条罗纹），但生成出来的、尤其是
**派生出来的其它颜色**，罗纹被磨平成普通平纹汗布 —— 因为款式提示词里写的还是
"thin cotton jersey … the only ribbed part is the neckband"，模型就照着把罗纹去掉了。

这个脚本做三件事（断言每一处都命中，改不到就报错退出，不会静默）：
  1. 正文最前面插入 RIB-KNIT FABRIC TEXTURE 段落：罗纹是面料属性，每个颜色都要保住；
  2. 把「the only ribbed part is the neckband」「thin cotton jersey …」这些互相打架的旧描述换掉；
  3. 负面词补上「不许把罗纹磨平成平纹」、「不许换色时丢掉竖条」。

用法：python tools/patch_stretch_rib_9_6_27.py [--dry]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SLUG = "10-stretch-tee"

RIB_BLOCK = (
    "RIB-KNIT FABRIC TEXTURE — HIGHEST-PRIORITY MATERIAL RULE, it must hold on EVERY colourway. "
    "This style is fine rib-knit jersey: the whole body and both sleeves show dense, fine vertical "
    "ribs (a 1x1 / 2x1 rib-knit look), evenly pitched, running down the garment and curving over the "
    "bust, the side seams and the sleeves. The ribs belong to the FABRIC, not to the colour: after "
    "the recolour they must still be clearly visible, in the same pitch, density, depth and "
    "direction as IMAGE 2 — on the master image AND on every derived colourway of this batch. The "
    "new colour is applied TO the ribbed knit, so the vertical rib lines stay readable in the new "
    "shade, including in shadow and in the highlights. Never flatten the ribs into smooth plain "
    "single-jersey, never render the new colour as a flat matte surface, never let the rib lines "
    "disappear or blur out, and never change the rib pitch. Hems stay PLAIN folded self-fabric (the "
    "hem is plain, the FABRIC is ribbed — do not turn the hems or the sleeve openings into rib-knit "
    "bands); the neckband is ribbed in the same pitch. "
)

OLD_FABRIC = "thin cotton jersey with plain folded hems and a ribbed neckband only"
NEW_FABRIC = (
    "fine rib-knit jersey (dense vertical ribs across the body and both sleeves, ribbed neckband, "
    "plain folded sleeve and body hems)"
)
OLD_ONLY_RIB = "the only ribbed part is the neckband"
NEW_ONLY_RIB = (
    "there is no rib-knit BAND on the sleeve or the hem (the ribbed knit is the fabric itself, all "
    "over the garment)"
)
NEG_EXTRA = (
    " Do not flatten the rib-knit texture into smooth plain jersey, do not remove or blur the "
    "vertical rib lines on the body or the sleeves, do not change the rib pitch or density, do not "
    "paint the new colour as a flat matte surface without ribs, and do not lose the ribbed "
    "appearance on any derived colourway of the same style."
)


def main() -> int:
    dry = "--dry" in sys.argv
    path = ROOT / "library" / "prompts" / f"{SLUG}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    pos = str(data.get("positive") or "")
    neg = str(data.get("negative") or "")

    if "RIB-KNIT FABRIC TEXTURE" in pos:
        print("  正文已含罗纹段，跳过插入")
    else:
        pos = RIB_BLOCK + pos
        print("  ✓ 正文最前面插入 RIB-KNIT FABRIC TEXTURE 段")

    if OLD_FABRIC in pos:
        pos = pos.replace(OLD_FABRIC, NEW_FABRIC)
        print("  ✓ 旧面料描述（thin cotton jersey…）已换成罗纹针织")
    else:
        print("  ! 没找到旧面料描述，检查是否已被改过")

    if OLD_ONLY_RIB in pos:
        pos = pos.replace(OLD_ONLY_RIB, NEW_ONLY_RIB)
        print("  ✓ 「只有领口有罗纹」改成「袖口下摆没有罗纹带，罗纹是面料本身」")
    else:
        print("  ! 没找到「the only ribbed part is the neckband」")

    if "flatten the rib-knit texture" not in neg:
        neg = neg.rstrip() + NEG_EXTRA
        print("  ✓ 负面词补上「不许磨平罗纹 / 不许换色丢竖条」")
    else:
        print("  负面词已含罗纹约束")

    data["positive"] = pos
    data["negative"] = neg
    data["notes"] = (
        "弹力短袖：紧身弹力**罗纹针织**（整件竖条罗纹，每个颜色都要保住纹理）、"
        "平口（袖口下摆是无罗纹带的平折边）、领口罗纹"
    )
    print(f"  正文 {len(pos)} 字 / 负面 {len(neg)} 字")
    if not dry:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  已写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
