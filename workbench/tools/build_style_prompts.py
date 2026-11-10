"""为每个款式生成一套专属默认提示词（library/prompts/<slug>.json）。

用法：
    python tools/build_style_prompts.py --all            # 按当前逻辑生成/刷新全部款式
    python tools/build_style_prompts.py --all --force    # 覆盖已手改过的文件（会先备份 .bak）
    python tools/build_style_prompts.py --new 24-long-sleeve   # 只生成一个新款式
    python tools/build_style_prompts.py --list           # 看哪些款式有/没有专属提示词

生成内容是「款式专属核心」：
  positive = 款式转换段 +（款式专属细节段）+ 款式锁 + 袖长 + 袖口工艺 +（连帽款）帽绳色系
  negative = 该款式的袖长/袖口/帽兜/帽绳否定词 +（款式专属否定词）
共享骨架（图案锁、材质锁、效果图/色卡分工、主体规则）仍在代码里，不重复进每个文件。

「款式专属细节段」(`DETAIL`) 是这一版新增的重点：光靠 phrase + 袖长 + 袖口描述不了
「插肩双色」「露脐」「弧形下摆」「挂脖」「两条裤腿」这类结构，模型必须被明确告知。
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
    "01-cotton-tee": "纯棉宽松短袖：哑光纯棉、落肩宽袖（已被 23 索罗娜短袖取代）",
    "03-crewneck-sweatshirt": "圆领卫衣：无帽、罗纹领口/袖口/下摆、抓绒",
    "04-milk-silk-tee": "牛奶丝短袖：丝光垂坠、宽松落肩（面料观感要像牛奶丝，不是纯棉）",
    "05-cropped-tee": "露脐短袖：衣长只到腰线以上（已被 26 露脐索罗娜取代）",
    "06-hoodie": "连帽卫衣：帽子+帽绳（绳与衣身同色系）、袋鼠兜、罗纹袖口下摆",
    "07-washed-tee": "水洗T恤：oversize 落肩、水洗做旧面料、平口",
    "08-tank-top": "背心：宽肩带高袖笼、深圆领、无袖、衣长偏短",
    "09-kids-cotton-tee": "童装纯棉短袖：儿童哑光纯棉、落肩（已被 21 童装索罗娜取代）",
    "10-stretch-tee": "弹力短袖：紧身弹力针织、贴身、平口",
    "11-kids-dolman-tee": "童装牛角袖：插肩双色（身子白、袖子+领口才是这个色）",
    "12-curved-hem-tee": "弧形短袖：下摆呈弧形（中间低两侧高）、宽领口",
    "13-mens-tank": "男士背心：宽肩带、罗纹圆领、无袖宽松",
    "14-dolman-tee": "成人牛角袖：插肩双色（身子白、袖子+领口才是这个色）",
    "15-mid-shorts": "中短裤：松紧腰+抽绳、两个裤腿、及膝以上",
    "16-sports-shorts": "运动短裤：短裤腿、圆抽绳、侧缝与裤脚白色撞色滚边",
    "17-kids-cropped-tee": "童装短款：衣长到腰线以上、儿童版型",
    "18-halter-tank": "挂脖背心：绕颈挂脖、露肩背、无袖修身",
    "19-u-neck-tee": "U领T：深宽 U 领、修身罗纹、短袖",
    "20-distressed-tee": "做旧T恤：酸洗做旧、颜色刻意不均、oversize",
    "21-kids-sorona-tee": "童装索罗娜：儿童宽松短袖、平口",
    "23-sorona-tee": "索罗娜短袖：宽松落肩、短袖、平口折边",
    "24-long-sleeve": "长袖成人：常规直筒、袖到腕、平口折边（无罗纹）",
    "25-kids-long-sleeve": "长袖童装：儿童常规长袖、平口折边（无罗纹）",
    "26-cropped-sorona-tee": "露脐索罗娜：方版露脐短款、宽短袖、罗纹圆领",
    "28-kids-sweatshirt": "卫衣童装：儿童圆领卫衣、罗纹袖口下摆、无帽",
    "29-kids-washed-tee": "水洗童装：儿童 oversize 水洗短袖、平口",
    "30-tie-dye-tee": "扎染T：面料本身是扎染/晕染纹理（糊状斑块，不是硬边印花）",
    "31-ink-wash-tee": "水墨T：面料本身是水墨/大理石晕染纹理",
}

#: 款式专属细节段（放在款式转换段之后，位置靠前模型才听）。
#: 只给「光靠 phrase 说不清」的款式写；普通 T 恤/卫衣不需要。
DETAIL = {
    "01-cotton-tee": (
        "FABRIC — 100% cotton jersey: matte, dry-handing cotton with no sheen and no silky drape, "
        "reading slightly heavier and crisper than a Sorona or milk-silk tee. The cut stays boxy "
        "with dropped shoulders and wide roomy short sleeves."
    ),
    "04-milk-silk-tee": (
        "FABRIC — milk-silk (fine polyester-spandex jersey): a soft silky sheen across the folds and "
        "a fluid liquid drape, so the loose boxy body falls in soft vertical folds. Do not render it "
        "as matte cotton and do not add rib-knit cuffs."
    ),
    "05-cropped-tee": (
        "CROPPED LENGTH — the body is cut short: the bottom hem sits above the natural waist and the "
        "midriff is left bare. Keep that hem straight and level, keep the sleeves short and plain, "
        "and do not lengthen the body back to a normal tee."
    ),
    "08-tank-top": (
        "TANK SHAPE — wide shoulder straps with high-cut armholes and a deep scoop neckline; the body "
        "follows the torso and ends around the waist in a short cropped length."
    ),
    "09-kids-cotton-tee": (
        "KIDS COTTON — a small-child t-shirt in matte 100% cotton jersey (no sheen, no silky drape): "
        "dropped shoulders, roomy wide short sleeves, ribbed neckband, plain flat hems, and child "
        "proportions (shorter body, shorter sleeves)."
    ),
    "11-kids-dolman-tee": (
        "RAGLAN TWO-TONE — this product is deliberately TWO-TONE and the split is part of it: the "
        "front and back body panels stay WHITE / off-white, while the raglan sleeves and the crew "
        "neckband take the colourway colour. The sleeve is joined by a diagonal raglan seam running "
        "from the neckline down to the underarm on both sides — no straight shoulder seam, no set-in "
        "armhole. Do not flood the whole garment with the colourway colour and do not make it all "
        "white: keep the white body with coloured sleeves and neckband, with child proportions."
    ),
    "12-curved-hem-tee": (
        "CURVED HEM — the bottom hem is a crescent, not a level line: it dips lowest at the centre "
        "front and the centre back and rises toward both side seams. The neckline is a wide scoop "
        "finished with a wide band, the body is close to the torso, and the sleeves are short. Keep "
        "that curved hem clean and continuous around the body."
    ),
    "13-mens-tank": (
        "MUSCLE TANK SHAPE — wide shoulder straps, a ribbed round neckline and a loose boxy body with "
        "a straight hem (men's fit)."
    ),
    "14-dolman-tee": (
        "RAGLAN TWO-TONE — this product is deliberately TWO-TONE and the split is part of it: the "
        "front and back body panels stay WHITE / off-white, while the raglan sleeves and the crew "
        "neckband take the colourway colour. The sleeve is joined by a diagonal raglan seam running "
        "from the neckline down to the underarm on both sides — no straight shoulder seam, no set-in "
        "armhole. Do not flood the whole garment with the colourway colour and do not make it all "
        "white: keep the white body with coloured sleeves and neckband, with adult proportions."
    ),
    "15-mid-shorts": (
        "SHORTS DETAILS — an elasticated waistband with a flat drawstring at the centre front, side "
        "seam pockets, and two legs ending above the knee with plain level hems; the crotch and both "
        "legs must read clearly."
    ),
    "16-sports-shorts": (
        "RUNNING SHORTS DETAILS — an elasticated waistband with a round drawstring tied at the centre "
        "front, plus a WHITE contrast piping running down the side seam and around the curved leg hem. "
        "Keep that white piping crisp and continuous: it is a design detail and it does not take the "
        "garment's colour."
    ),
    "17-kids-cropped-tee": (
        "CROPPED KIDS LENGTH — the body is cut short above the natural waist with child proportions: "
        "roomy short sleeves, a straight cropped hem, and no long body."
    ),
    "18-halter-tank": (
        "HALTER NECK — the front bodice rises into straps that continue up and fasten behind the neck, "
        "leaving the shoulders and the upper back bare, with a deep scoop opening at the front chest. "
        "The body is fitted and follows the torso and the hem is straight and level. The reference "
        "photo shows this top on a display form: reproduce the GARMENT only — not the form, not a "
        "torso, not the background objects."
    ),
    "19-u-neck-tee": (
        "U NECKLINE — a deep, wide U-shaped scoop neckline finished with a wide band. The body is "
        "ribbed and close to the torso, the short sleeves hug the upper arm and end above the elbow, "
        "and the hem is straight and level. The neckline must stay a U scoop, not a crew neck and not "
        "a V neck."
    ),
    "20-distressed-tee": (
        "ACID-WASH SURFACE — the fabric is deliberately unevenly faded: blotchy darker and lighter "
        "patches, cloudy marbling and softly worn edges instead of one flat uniform colour. Keep that "
        "mottled washed texture and do not smooth the garment into a clean uniform fill; the "
        "silhouette stays oversized and boxy with dropped shoulders and wide short sleeves."
    ),
    "26-cropped-sorona-tee": (
        "CROPPED LENGTH — the body is cut short: the bottom hem sits above the natural waist and the "
        "midriff is left bare. The cut is boxy with slightly dropped shoulders, wide short sleeves and "
        "a ribbed crew neckband, and the cropped hem is straight and level."
    ),
    "30-tie-dye-tee": (
        "TIE-DYE FABRIC — the cloth itself carries a soft mottled tie-dye / marble surface: "
        "irregular cloudy blotches and cloudy flecks on a light base, with blurred edges where one "
        "blotch bleeds into the next, spread across the whole body and both sleeves and thinning out "
        "towards the hems. It is a fabric dye effect, not a crisp printed graphic, not a decal and not "
        "a separate printed patch, and it must keep running across seams and folds. Keep the neckband, "
        "sleeve ends and hem in the same fabric. Any artwork the source already carries sits on top of "
        "that fabric and is preserved as is."
    ),
    "31-ink-wash-tee": (
        "INK-WASH FABRIC — the cloth itself carries a soft grey ink-wash / marble pattern: blurred "
        "organic veins spreading across the whole body and the sleeves and fading out at the edges "
        "like watercolour bleeding into wet cloth. It is not a crisp vector graphic, not a decal and "
        "not a separate printed patch, and the veining must run continuously across seams and folds. "
        "Any artwork the source already carries sits on top of that fabric and is preserved as is."
    ),
}

#: 款式专属否定词（补在按属性拼出来的否定词后面）。
DETAIL_NEGATIVE = {
    "01-cotton-tee": "Do not give the cotton body a silky sheen or a liquid Sorona drape.",
    "04-milk-silk-tee": "Do not render the fabric as matte cotton and do not remove the silky sheen.",
    "05-cropped-tee": "Do not lengthen the body to a normal tee and do not lower the cropped hem.",
    "08-tank-top": "Do not add sleeves, cap sleeves or a shoulder sleeve, and do not lengthen the body.",
    "09-kids-cotton-tee": "Do not give the cotton body a silky sheen, and do not use adult proportions.",
    "11-kids-dolman-tee": (
        "Do not flood the entire garment with the colourway colour, do not make the body panels "
        "coloured, do not add a straight set-in shoulder seam, and do not use adult proportions."
    ),
    "12-curved-hem-tee": "Do not straighten the curved hem into a level hem line.",
    "13-mens-tank": "Do not add sleeves, cap sleeves or a shoulder sleeve.",
    "14-dolman-tee": (
        "Do not flood the entire garment with the colourway colour, do not make the body panels "
        "coloured, and do not add a straight set-in shoulder seam."
    ),
    "15-mid-shorts": (
        "Do not merge the two legs into one tube, do not produce long trousers, a skirt or a top, and "
        "do not lengthen the legs below the knee."
    ),
    "16-sports-shorts": (
        "Do not recolour or drop the white contrast piping, do not merge the two legs into one tube, "
        "and do not produce long trousers, a skirt or a top."
    ),
    "17-kids-cropped-tee": "Do not lengthen the cropped body and do not use adult proportions.",
    "18-halter-tank": (
        "Do not add sleeves or a T-shirt neckline, do not give the top a closed round neckline, do not "
        "add shoulder straps that stop at the shoulder, and do not reproduce the display form, a "
        "torso, a mannequin or the background objects from the reference photo."
    ),
    "19-u-neck-tee": "Do not turn the U neckline into a crew neck or a V neck.",
    "20-distressed-tee": "Do not flatten the acid-wash mottling into one uniform solid colour.",
    "26-cropped-sorona-tee": "Do not lengthen the cropped body and do not lower the cropped hem.",
    "30-tie-dye-tee": (
        "Do not turn the tie-dye mottling into a hard-edged graphic, a decal, an all-over print or a "
        "single flat colour, do not blow the blotches up into symmetrical shapes, and do not stop the "
        "tie-dye at a seam."
    ),
    "31-ink-wash-tee": (
        "Do not turn the ink-wash marbling into a hard-edged graphic, a decal or a single flat colour, "
        "and do not stop the veining at a seam."
    ),
}


def build_one(wb: Workbench, slug: str) -> StylePrompt:
    garment = wb.skill.type_by_alias(slug)
    detail = DETAIL.get(slug, "")
    positive = " ".join(
        part for part in (
            prompts.convert_clause(garment),
            detail,
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
    extra = DETAIL_NEGATIVE.get(slug, "")
    if extra:
        negative = (negative + " " + extra).strip()
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
            print(
                f"  [{mark}] {slug:26s} {garment.display_name}"
                f"  {garment.sleeve_zh}/{garment.cuff_zh}/{garment.kind_zh}{extra}"
            )
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
