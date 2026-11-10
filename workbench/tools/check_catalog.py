# -*- coding: utf-8 -*-
"""款式库整合回归：《菲律宾-服装款式代号表》27 款 / 121 色 + 每款专属提示词。

2026-11-11 客户给了菲律宾款式表（27 个款式 sheet，每款每色一张官方效果图），要求
「整合到每个款式的 skill 里去」。这个脚本把那次整合钉成可回归的断言：

  ① 数据：款式数量 / 色号数量 / 每色的参考图与效果图都真实存在
  ② 建档：每款都有 phrase / fit / 袖长 / 袖口 / 品类，且取值自洽
  ③ 提示词：每款一份专属核心，且真的进了最终提示词与否定词
  ④ 品类：背心没有袖子、短裤是两条腿，提示词不许出现上衣的袖口措辞
  ⑤ 款式细节：插肩双色、露脐、弧形下摆、挂脖、水墨、做旧各自写进提示词
  ⑥ 回退：删掉专属文件后仍能按属性拼装（新款式可以先上线再补提示词）

用法：python tools/check_catalog.py [--show]
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import prompts  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402
from skillbridge.style_prompts import load_all, write_index  # noqa: E402

LIB = Path(__file__).resolve().parent.parent / "library"
FAILURES: list[str] = []

EXPECT_KINDS = {"top", "vest"}          # 客户口径 24 款里没有下装
VESTS = {"08-tank-top", "13-mens-tank", "18-halter-tank"}
BOTTOMS: set[str] = set()               # 14/15/16 已按客户口径移出
#: 客户明确说没有的款式，库里不许再出现
FORBIDDEN_SLUGS = {"14-dolman-tee", "15-mid-shorts", "16-sports-shorts"}
TWO_TONE = {"11-kids-dolman-tee"}
CROPPED = {"05-cropped-tee", "17-kids-cropped-tee", "26-cropped-sorona-tee"}

#: 每个款式专属提示词里必须出现的关键词（证明「细节真的写进去了」）
DETAIL_MARKERS = {
    "11-kids-dolman-tee": "RAGLAN TWO-TONE",
    "12-curved-hem-tee": "CURVED HEM",
    "19-u-neck-tee": "U NECKLINE",
    "18-halter-tank": "HALTER NECK",
    "20-distressed-tee": "ACID-WASH SURFACE",
    "31-ink-wash-tee": "INK-WASH FABRIC",
    "30-tie-dye-tee": "TIE-DYE FABRIC",
    "26-cropped-sorona-tee": "CROPPED LENGTH",
    "05-cropped-tee": "CROPPED LENGTH",
    "17-kids-cropped-tee": "CROPPED KIDS LENGTH",
    "04-milk-silk-tee": "FABRIC — milk-silk",
    "01-cotton-tee": "FABRIC — 100% cotton jersey",
    "09-kids-cotton-tee": "KIDS COTTON",
    "08-tank-top": "TANK SHAPE",
    "13-mens-tank": "MUSCLE TANK SHAPE",
}


def check(ok: bool, label: str, show: bool) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


def main() -> int:
    show = "--show" in sys.argv
    wb = Workbench()
    library = wb.library_dir
    types = wb.skill.types

    rows = json.loads((LIB / "styles.json").read_text(encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows.get("styles") or []

    print("① 数据：菲律宾款式表 24 款 + 手工建档 30 扎染T = 25 款 / 115 色")
    check(len(rows) == 25, f"styles.json 有 25 个款式（实际 {len(rows)}）", show)
    total = sum(len(s.get("colors") or []) for s in rows)
    check(total == 115, f"共 115 个色号（实际 {total}）", show)
    check(len(types) == 28, f"工作台可选 28 款（25 + skill 自带 3，实际 {len(types)}）", show)
    bad_files: list[str] = []
    bad_effect: list[str] = []
    for style in rows:
        for color in style.get("colors") or []:
            ref = LIB / str(color.get("image") or "")
            eff = LIB / str(color.get("effect") or "")
            if not ref.is_file():
                bad_files.append(f"{style['slug']}/{color.get('label')}")
            if not eff.is_file():
                bad_effect.append(f"{style['slug']}/{color.get('color_code')}")
    check(not bad_files, f"每色的参考图都存在（缺 {bad_files[:3]}）", show)
    check(not bad_effect, f"每色的官方效果图都存在（缺 {bad_effect[:3]}）", show)
    manifest = LIB / "effects" / "manifest.json"
    meta = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    check(meta.get("source_file", "").startswith("菲律宾"),
          f"效果图 manifest 指向菲律宾表（{meta.get('source_file')}）", show)

    print("② 建档：版型 / 袖长 / 袖口 / 品类自洽")
    catalog_slugs = {str(s.get("slug")) for s in rows}
    for slug in sorted(catalog_slugs):
        garment = types[slug]
        check(bool(garment.phrase.strip()), f"{slug}：phrase 非空", show)
        check(bool(garment.fit.strip()), f"{slug}：fit 非空（照着效果图写版型）", show)
        kind = getattr(garment, "kind", "")
        check(kind in EXPECT_KINDS, f"{slug}：kind 合法（{kind}）", show)
        if kind == "top":
            check(garment.sleeve in ("short", "long"), f"{slug}：上衣的 sleeve 是短/长袖", show)
        else:
            check(garment.sleeve == "none", f"{slug}：无袖/下装的 sleeve 是 none", show)
        if slug in VESTS:
            check(kind == "vest", f"{slug}：按背心建档", show)
        if slug in BOTTOMS:
            check(kind == "bottom", f"{slug}：按下装建档", show)
        check(garment.name_en.strip() != "", f"{slug}：有英文代号（{garment.name_en}）", show)
        check(all(c.code for c in garment.colors), f"{slug}：每色都有色号", show)

    print("③ 提示词：每款一份专属核心，且真的生效")
    on_disk = load_all(library)
    missing = [slug for slug in types if slug not in on_disk]
    check(not missing, f"28 款都有专属提示词文件（缺 {missing}）", show)
    for slug in sorted(catalog_slugs):
        garment = types[slug]
        if not garment.style_positive:
            continue
        built = prompts.build_single_pass(wb.skill, garment, garment.colors[0], subject="garment")
        marker = garment.style_positive.split(".")[0][:40]
        check(marker in built, f"{slug}：专属核心进了最终提示词", show)
        negative = prompts.full_negative(wb.skill, "preserve", garment, "garment")
        neg_marker = (garment.style_negative or "").split(".")[0][:30]
        if neg_marker:
            check(neg_marker in negative, f"{slug}：专属否定词进了负面词", show)
        check("must not gain no " not in built, f"{slug}：没有双重否定病句", show)

    print("④ 品类：背心没有袖子（客户口径 24 款里没有下装）")
    for slug in sorted(VESTS):
        garment = types[slug]
        text = prompts.build_single_pass(wb.skill, garment, garment.colors[0], subject="garment")
        check(prompts.SLEEVE_NONE_CLAUSE in text, f"{slug}：写明「没有袖子」", show)
        check(prompts.CUFF_HEM_VEST_CLAUSE in text, f"{slug}：袖口段换成袖笼/下摆", show)
        check("GENUINE SHORT SLEEVES" not in text, f"{slug}：不出现短袖约束", show)
        check("PLAIN, FLAT" not in text, f"{slug}：不出现「袖口平折边」那段", show)
    for slug in sorted(BOTTOMS):
        garment = types[slug]
        text = prompts.build_single_pass(wb.skill, garment, garment.colors[0], subject="garment")
        check(prompts.LEG_SHORTS_CLAUSE in text, f"{slug}：写明是短裤不是上衣", show)
        check(prompts.CUFF_HEM_BOTTOM_CLAUSE in text, f"{slug}：袖口段换成腰头/裤脚", show)
        check("SLEEVE LENGTH" not in text, f"{slug}：不出现袖长约束", show)
    stale = sorted(FORBIDDEN_SLUGS & set(types))
    check(not stale, f"客户说没有的款式（成人牛角袖/裤子）已从库里移出：{stale}", show)
    check("bottom" not in {g.kind for g in types.values() if g.slug in catalog_slugs},
          "款式库里不再有下装款式", show)

    print("⑤ 款式细节：结构写进提示词（不是只写在 phrase 里）")
    for slug, marker in sorted(DETAIL_MARKERS.items()):
        garment = types[slug]
        text = prompts.build_single_pass(wb.skill, garment, garment.colors[0], subject="garment")
        check(marker in text, f"{slug}：提示词含「{marker}」", show)
    for slug in sorted(TWO_TONE):
        text = prompts.build_single_pass(wb.skill, types[slug], types[slug].colors[0], subject="garment")
        check("WHITE / off-white" in text, f"{slug}：写明身子是白色", show)
        check("raglan seam" in text, f"{slug}：写明插肩缝", show)
    for slug in sorted(CROPPED):
        text = prompts.build_single_pass(wb.skill, types[slug], types[slug].colors[0], subject="garment")
        check("above the waist" in text, f"{slug}：写明衣长在腰线以上", show)

    print("⑥ 回退：删掉专属文件也能按属性拼装")
    sample = types["11-kids-dolman-tee"]
    bare = type(sample)(**{**sample.__dict__, "style_positive": "", "style_negative": ""})
    built = prompts.build_single_pass(wb.skill, bare, bare.colors[0], subject="garment")
    check("CONVERT THE GARMENT TYPE" in built, "无专属文件 → 回落款式转换段", show)
    check(prompts.garment_lock(bare) in built, "无专属文件 → 仍然带款式锁", show)
    with tempfile.TemporaryDirectory() as tmp:
        index = write_index(Path(tmp), {slug: on_disk[slug] for slug in list(on_disk)[:3]})
        check(index.is_file(), "索引能重新生成（新增款式时会用到）", show)

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES[:20]:
            print("  -", item)
        return 1
    print(f"全部通过：{len(rows)} 款 / {total} 色整合完毕，{len(on_disk)} 份专属提示词各自生效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
