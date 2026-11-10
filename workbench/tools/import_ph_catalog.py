# -*- coding: utf-8 -*-
"""把《菲律宾-服装款式代号表》整合进款式库（27 个款式 / 121 个色号 + 每色的官方效果图）。

和 `import_style_catalog.py` 的分工：
  * 那个只给**已有款式**补效果图（款式清单还是《墨西哥》那 10 个）
  * 这个把**整张表**吃进来：新款式建档（phrase / fit / 袖长 / 袖口 / 品类 / 别名）、
    新色号补进已有款式、每色效果图落到 `library/effects/`、缩略图落到 `library/refs/`，
    并把 `library/styles.json` 排序重写。

表格结构（实测 2026-11 版，27 个款式 sheet + 目录）：
    A1            `26#LQSLN 女款T恤露脐短款，索罗娜材质`
    表头行        颜色名称 | 颜色编码 | 尺码编码 | 衣服重量g | 面料重量g | 效果图 | …
    数据行        颜色名 | WH[0] | … | ← 效果图列锚定该颜色的平铺产品图

用法：
    python tools/import_ph_catalog.py                # 用 library/source/菲律宾-服装款式代号表.xlsx
    python tools/import_ph_catalog.py --dry-run      # 只报告，不写文件
    python tools/import_ph_catalog.py --xlsx=<路径>  # 指定表格
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from import_style_catalog import _anchors, _cells, _shared, CODE_RE  # noqa: E402
from build_library_from_xlsx import COLOR_MAP, NOTE_HINTS, fabric_hex  # noqa: E402

import xml.etree.ElementTree as ET  # noqa: E402

LIB = ROOT / "library"
STYLES = LIB / "styles.json"
REFS = LIB / "refs"
EFFECTS = LIB / "effects"
SOURCE = LIB / "source" / "菲律宾-服装款式代号表.xlsx"

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
COLOR_CODE_RE = __import__("re").compile(r"^[A-Z]{1,5}\[\d+\]$")

#: 新款式的建档元数据（老 10 款沿用 styles.json 里已经调好的 phrase / fit，不在这里覆盖）。
#:   phrase  —— 写进「款式锁 / 款式转换段」的名词短语，**必须是这个款本身的准确描述**
#:   fit     —— 照着效果图写的版型细节（带效果图时会写进提示词）
#:   sleeve  —— short / long / none（none = 背心类，没有袖子）
#:   kind    —— top / vest / bottom（下装单独一套构造约束）
STYLE_META: dict[str, dict] = {
    "01": {
        "slug": "01-cotton-tee",
        "name": "01 纯棉短袖",
        "phrase": "a relaxed-fit short-sleeve t-shirt in 100% cotton jersey (dropped shoulders, "
                  "roomy short sleeves, ribbed neckband, plain folded cuffs and straight plain hem)",
        "fit": "relaxed loose fit: dropped shoulders, wide roomy short sleeves, straight boxy body, "
               "ribbed neckband, plain flat sleeve and bottom hems",
        "cuff": "plain", "sleeve": "short", "kind": "top",
        "aliases": ["01 纯棉短袖", "纯棉短袖", "01#CMDX", "cotton tee", "cotton t-shirt"],
        "note": "纯棉宽松款短袖，部分尺码已停产，替代款见 23 索罗娜短袖",
        "replaced_by": "23-sorona-tee",
    },
    "04": {
        "slug": "04-milk-silk-tee",
        "name": "04 牛奶丝",
        "phrase": "a relaxed-fit short-sleeve t-shirt in milk-silk jersey (fine polyester-spandex "
                  "knit with a silky fluid drape and a soft sheen, dropped shoulders, roomy short "
                  "sleeves, plain folded cuffs and straight plain hem)",
        "fit": "relaxed loose fit: dropped shoulders, roomy short sleeves, straight body with a soft "
               "fluid silky drape, plain flat sleeve and bottom hems",
        "cuff": "plain", "sleeve": "short", "kind": "top",
        "aliases": ["04 牛奶丝", "牛奶丝", "牛奶丝短袖", "04#NNS", "milk silk tee", "milk silk t-shirt"],
        "note": "牛奶丝宽松款短袖：面料垂坠有丝光，但版型仍是普通宽松 T 恤",
    },
    "05": {
        "slug": "05-cropped-tee",
        "name": "05 露脐T恤",
        "phrase": "a cropped short-sleeve t-shirt in cotton jersey (the body is cut short above the "
                  "waist so the midriff is bare, short sleeves, plain folded cropped hem)",
        "fit": "cropped regular fit: short body ending above the waist with the midriff exposed, "
               "set-in shoulders, short sleeves ending above the elbow, clean straight cropped hem",
        "cuff": "plain", "sleeve": "short", "kind": "top",
        "aliases": ["05 露脐T恤", "露脐T恤", "露脐", "05#LQT", "cropped tee", "crop top t-shirt"],
        "note": "露脐短款：衣长只到腰线以上，不是普通 T 恤",
        "replaced_by": "26-cropped-sorona-tee",
    },
    "08": {
        "slug": "08-tank-top",
        "name": "08 背心",
        "phrase": "a short cropped sleeveless tank top in stretch jersey (no sleeves at all, wide "
                  "shoulder straps with high-cut armholes, a deep scoop neckline, body following the "
                  "torso, plain folded armhole binding and straight plain hem)",
        "fit": "fitted cropped tank fit: sleeveless with wide shoulder straps and high-cut armholes, "
               "deep scoop neckline, body follows the torso, short body ending around the waist",
        "cuff": "plain", "sleeve": "none", "kind": "vest",
        "aliases": ["08 背心", "背心", "女背心", "08#BX", "tank top", "tank"],
        "note": "无袖背心：宽肩带、高袖笼、深圆领、衣长偏短，绝对不能长袖子",
    },
    "09": {
        "slug": "09-kids-cotton-tee",
        "name": "09 童装纯棉",
        "phrase": "a kids short-sleeve t-shirt in 100% cotton jersey (small child fit, dropped "
                  "shoulders, roomy short sleeves, ribbed neckband, plain folded cuffs and straight "
                  "plain hem)",
        "fit": "relaxed kids fit: dropped shoulders, roomy short sleeves, straight body, plain flat "
               "sleeve and bottom hems",
        "cuff": "plain", "sleeve": "short", "kind": "top",
        "aliases": ["09 童装纯棉", "童装纯棉", "09#TZCM", "kids cotton tee", "kids cotton t-shirt"],
        "note": "童装纯棉短袖，部分尺码已停产，替代款见 21 童装索罗娜",
        "replaced_by": "21-kids-sorona-tee",
    },
    "11": {
        "slug": "11-kids-dolman-tee",
        "name": "11 童装牛角袖",
        "phrase": "a kids short-sleeve RAGLAN t-shirt: the sleeves are joined to the body by a "
                  "diagonal raglan seam running from the crew neckline down to the underarm on each "
                  "side, the body panels stay white while the raglan sleeves and the neckband carry "
                  "the colourway colour, plain folded sleeve and body hems",
        "fit": "regular kids raglan fit: raglan sleeves with a diagonal seam from the neckline to "
               "the underarm instead of a straight shoulder seam, white body panels, coloured "
               "sleeves and neckband, straight body, plain hems",
        "cuff": "plain", "sleeve": "short", "kind": "top",
        "aliases": ["11 牛角袖", "牛角袖", "童装牛角袖", "11#NJX", "raglan tee", "baseball tee"],
        "note": "牛角袖＝插肩/raglan 双色：身子是白色，袖子和领口才是这个色号的颜色",
    },
    "12": {
        "slug": "12-curved-hem-tee",
        "name": "12 弧形短袖",
        "phrase": "a curved-hem short-sleeve t-shirt in stretch jersey (a wide scoop neckline band, "
                  "short sleeves, and a bottom hem that curves — scooped lower at the centre front "
                  "and back and rising at the side seams)",
        "fit": "slim-regular fit: body follows the torso, wide scoop neckline with a wide band, short "
               "sleeves ending above the elbow, and a crescent hem that dips at the centre and "
               "lifts at the side seams",
        "cuff": "plain", "sleeve": "short", "kind": "top",
        "aliases": ["12 弧形短袖", "弧形短袖", "弧形下摆", "12#HXDX", "curved hem tee"],
        "note": "下摆是弧形的（中间低、两侧高），不是平下摆",
    },
    "13": {
        "slug": "13-mens-tank",
        "name": "13 男士背心",
        "phrase": "a men's sleeveless tank top (no sleeves at all, ribbed round neckline, wide "
                  "shoulder straps, loose straight body, plain folded armhole binding and straight "
                  "plain hem)",
        "fit": "loose men's tank fit: sleeveless with wide shoulder straps, ribbed round neckline, "
               "straight boxy body, straight plain hem",
        "cuff": "plain", "sleeve": "none", "kind": "vest",
        "aliases": ["13 男士背心", "男士背心", "男背心", "13#NSBX", "men's tank", "mens tank top"],
        "note": "男士无袖背心：宽肩带、罗纹圆领、版型宽松",
    },
    "14": {
        "slug": "14-dolman-tee",
        "name": "14 成人牛角袖",
        "phrase": "an adult short-sleeve RAGLAN t-shirt: the sleeves are joined to the body by a "
                  "diagonal raglan seam running from the crew neckline down to the underarm on each "
                  "side, the body panels stay white while the raglan sleeves and the neckband carry "
                  "the colourway colour, plain folded sleeve and body hems",
        "fit": "regular adult raglan fit: raglan sleeves with a diagonal seam from the neckline to "
               "the underarm instead of a straight shoulder seam, white body panels, coloured "
               "sleeves and neckband, straight body, plain hems",
        "cuff": "plain", "sleeve": "short", "kind": "top",
        "aliases": ["14 成人牛角袖", "成人牛角袖", "14#CRNJX", "adult raglan tee"],
        "note": "成人牛角袖：同 11 的插肩双色结构，按成人版型",
    },
    "15": {
        "slug": "15-mid-shorts",
        "name": "15 中短裤",
        "phrase": "a pair of mid-length sweat shorts (an elasticated waistband with a flat drawstring, "
                  "side seam pockets, two separate loose legs with their own openings ending above "
                  "the knee, plain level leg hems)",
        "fit": "loose mid-length shorts: elasticated waistband with a flat drawstring, wide straight "
               "legs ending above the knee, side seam pockets, plain level leg hems",
        "cuff": "plain", "sleeve": "none", "kind": "bottom",
        "aliases": ["15 中短裤", "中短裤", "短裤", "15#ZDK", "mid shorts", "shorts"],
        "note": "下装：要的是有两条腿的短裤，不是上衣",
    },
    "16": {
        "slug": "16-sports-shorts",
        "name": "16 运动短裤",
        "phrase": "a pair of athletic running shorts (elasticated waistband with a round drawstring "
                  "at the centre front, short loose legs ending mid-thigh, white contrast piping "
                  "along the side seam and around the curved leg hem, small side slit)",
        "fit": "loose athletic shorts: elasticated waistband with a round drawstring, short wide legs "
               "ending mid-thigh, white contrast piping on the side seam and leg hem, curved hems",
        "cuff": "plain", "sleeve": "none", "kind": "bottom",
        "aliases": ["16 运动短裤", "运动短裤", "16#YDDK", "sports shorts", "athletic shorts"],
        "note": "下装：运动短裤，裤长到大腿中部，侧缝和裤脚有白色撞色滚边",
    },
    "17": {
        "slug": "17-kids-cropped-tee",
        "name": "17 童装短款",
        "phrase": "a kids cropped short-sleeve t-shirt in cotton jersey (small child fit, the body "
                  "is cut short above the waist, roomy short sleeves, plain folded cropped hem)",
        "fit": "cropped kids fit: short body ending above the waist, roomy short sleeves, straight "
               "cropped hem",
        "cuff": "plain", "sleeve": "short", "kind": "top",
        "aliases": ["17 童装短款", "童装短款", "17#TZDK", "kids cropped tee", "kids crop tee"],
        "note": "童装短款：衣长到腰线以上",
    },
    "18": {
        "slug": "18-halter-tank",
        "name": "18 挂脖背心",
        "phrase": "a halter-neck sleeveless top (straps that run up from the front bodice and "
                  "fasten behind the neck, shoulders and upper back left bare, no sleeves at all, "
                  "fitted body, straight plain hem)",
        "fit": "fitted halter fit: straps run up from the bodice to the back of the neck, shoulders "
               "and upper back bare, no sleeves, body follows the torso, straight plain hem",
        "cuff": "plain", "sleeve": "none", "kind": "vest",
        "aliases": ["18 挂脖背心", "挂脖背心", "挂脖", "18#GBBX", "halter top", "halter tank"],
        "note": "挂脖无袖：带子是绕到脖子后面系的，肩背是露的",
    },
    "19": {
        "slug": "19-u-neck-tee",
        "name": "19 U领T",
        "phrase": "a U-neck short-sleeve t-shirt in ribbed stretch jersey (a deep wide U-shaped "
                  "neckline with a wide band, fitted body, short fitted sleeves, plain folded cuffs "
                  "and straight plain hem)",
        "fit": "fitted rib-knit fit: deep wide U-shaped neckline with a wide band, body follows the "
               "torso, short sleeves hugging the upper arm and ending above the elbow, straight hem",
        "cuff": "plain", "sleeve": "short", "kind": "top",
        "aliases": ["19 U领T", "U领T", "U领T恤", "19#ULT", "u neck tee", "u-neck t-shirt"],
        "note": "U 形深宽领 + 修身罗纹，不是圆领宽松 T",
    },
    "20": {
        "slug": "20-distressed-tee",
        "name": "20 做旧T恤",
        "phrase": "an oversized distressed vintage-wash t-shirt (loose body, dropped shoulders, "
                  "wide short sleeves, an uneven sun-faded dye with softly worn edges, plain folded "
                  "cuffs and straight plain hem)",
        "fit": "oversized loose fit: dropped shoulders, wide boxy body, wide short sleeves, straight "
               "plain hem, deliberately faded and distressed fabric surface",
        "cuff": "plain", "sleeve": "short", "kind": "top",
        "aliases": ["20 做旧T恤", "做旧T恤", "做旧", "20#ZJT", "distressed tee", "acid wash tee"],
        "note": "做旧水洗效果：颜色是刻意不均的旧感，不能修成均匀新布",
    },
    "26": {
        "slug": "26-cropped-sorona-tee",
        "name": "26 露脐索罗娜",
        "phrase": "a women's cropped short-sleeve t-shirt in Sorona polyester blend (a boxy short "
                  "body cut off above the waist so the midriff is bare, dropped shoulders, wide "
                  "short sleeves, ribbed crew neckband, plain folded cropped hem)",
        "fit": "boxy cropped fit: short body ending above the waist with the midriff exposed, "
               "slightly dropped shoulders, wide short sleeves, ribbed crew neckband, clean straight "
               "cropped hem",
        "cuff": "plain", "sleeve": "short", "kind": "top",
        "aliases": ["26 露脐索罗娜", "露脐索罗娜", "26#LQSLN", "cropped sorona tee"],
        "note": "女款露脐短款索罗娜：童装与成人共用一表（110-130 与 S-XL 用不同图案编码）",
    },
    "31": {
        "slug": "31-ink-wash-tee",
        "name": "31 水墨T",
        "phrase": "a short-sleeve t-shirt in polyester jersey whose CLOTH ITSELF carries a soft grey "
                  "ink-wash marbling across the whole garment (blurred watercolour veins dyed into "
                  "the fabric, not a crisp printed graphic), boxy body, dropped shoulders, wide "
                  "short sleeves, ribbed neckband, plain folded hems",
        "fit": "boxy relaxed fit: dropped shoulders, wide short sleeves, straight boxy body, plain "
               "hems, and the fabric surface is a soft grey ink-wash marble with blurred organic "
               "veins rather than a hard-edged print",
        "cuff": "plain", "sleeve": "short", "kind": "top",
        "aliases": ["31 水墨T", "水墨T", "水墨", "31#SMT", "ink wash tee", "marble wash tee"],
        "note": "水墨印花款：面料本身就是晕开的水墨/大理石纹，不是硬边矢量印花",
    },
}


def read_catalog(xlsx: Path) -> list[dict]:
    """返回每个款式 sheet 的代号 / 标题 / 颜色行（含效果图 media 路径）。"""
    z = zipfile.ZipFile(xlsx)
    strings = _shared(z)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {
        rel.get("Id"): rel.get("Target")
        for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    }
    out: list[dict] = []
    for sheet in wb.find(f"{M}sheets"):
        title = sheet.get("name")
        target = rels.get(sheet.get(f"{R}id"), "")
        path = target.lstrip("/")
        if not path.startswith("xl/"):
            path = "xl/" + path
        cells = _cells(z, strings, path)
        head = cells.get((0, 0), "")
        match = CODE_RE.match(head)
        if not match:
            continue                                    # 目录页之类
        images = _anchors(z, path)
        effect_col = header_row = None
        for (row, col), text in sorted(cells.items()):
            if text.startswith("效果图"):
                effect_col, header_row = col, row
                break
        if effect_col is None:
            continue
        by_row: dict[int, str] = {}
        for item in images:
            if item["col"] == effect_col and item["row"] >= header_row:
                by_row.setdefault(item["row"], item["media"])
        header_cells = {col: text for (row, col), text in cells.items() if row == header_row}
        code_col = next((c for c, t in header_cells.items() if t.startswith("颜色编码")), 1)
        name_col = next((c for c, t in header_cells.items() if t.startswith("颜色名称")), 0)
        material = ""
        for (row, col), text in cells.items():
            if row == header_row and text.startswith("面料重量"):
                material = text.split("\n")[-1].strip("()（） ")
        colors: list[dict] = []
        for (row, col), text in sorted(cells.items()):
            if row <= header_row or col != name_col:
                continue
            code = cells.get((row, code_col), "")
            if not COLOR_CODE_RE.match(code):
                continue
            if not text or len(text) > 12 or "\n" in text or any(h in text for h in NOTE_HINTS):
                continue
            if text in {c["name_zh"] for c in colors}:
                continue
            colors.append(
                {
                    "name_zh": text,
                    "color_code": code,
                    "media": by_row.get(row, ""),
                    "row": row + 1,
                }
            )
        out.append(
            {
                "sheet": title,
                "code": match.group(1),
                "code_full": f"{match.group(1)}#{(match.group(2) or '').upper()}",
                "title": head[match.end():].strip(),
                "material": material,
                "colors": colors,
            }
        )
    return out


def label_for(name_zh: str, index: int, used: set[str]) -> str:
    label = COLOR_MAP.get(name_zh, ("", ""))[0] or f"c{index}"
    if label in used:
        label = f"{label}-{index}"
    used.add(label)
    return label


def main() -> int:
    xlsx = SOURCE
    for item in sys.argv[1:]:
        if item.startswith("--xlsx="):
            xlsx = Path(item.split("=", 1)[1])
    dry = "--dry-run" in sys.argv
    if not xlsx.is_file():
        print(f"找不到表格：{xlsx}")
        return 2

    catalog = read_catalog(xlsx)
    print(f"读到 {len(catalog)} 个款式 sheet（{xlsx.name}）")
    payload = json.loads(STYLES.read_text(encoding="utf-8"))
    styles: list[dict] = payload if isinstance(payload, list) else (payload.get("styles") or [])
    by_code = {str(s.get("style_code") or "").strip(): s for s in styles}

    plan: list[dict] = []
    for entry in catalog:
        code = entry["code"]
        meta = STYLE_META.get(code)
        existing = by_code.get(code)
        if existing is None and meta is None:
            print(f"  [skip] {entry['code_full']}：既没有现存款式，STYLE_META 里也没有建档信息")
            continue
        if not entry["colors"]:
            print(f"  [skip] {entry['code_full']}：没读到颜色行")
            continue
        slug = (existing or {}).get("slug") or meta["slug"]
        known = {str(c.get("color_code") or "").upper() for c in (existing or {}).get("colors") or []}
        new_colors = [c["color_code"] for c in entry["colors"] if c["color_code"].upper() not in known]
        plan.append({"entry": entry, "meta": meta, "existing": existing, "slug": slug,
                     "new": new_colors})
        flag = "新增款式" if existing is None else ("补色" if new_colors else "已齐")
        print(f"  {entry['code_full']:<12} → {slug:<24} {len(entry['colors']):>2} 色  {flag}"
              + (f"（新色 {', '.join(new_colors)}）" if new_colors and existing else ""))

    total_colors = sum(len(p["entry"]["colors"]) for p in plan)
    print(f"\n合计：{len(plan)} 款式 / {total_colors} 色")
    if dry:
        return 0

    REFS.mkdir(parents=True, exist_ok=True)
    EFFECTS.mkdir(parents=True, exist_ok=True)
    written = 0
    missing: list[str] = []
    with zipfile.ZipFile(xlsx) as z:
        for item in plan:
            entry, meta, existing = item["entry"], item["meta"], item["existing"]
            slug = item["slug"]
            ref_dir = REFS / slug
            eff_dir = EFFECTS / slug
            eff_dir.mkdir(parents=True, exist_ok=True)
            ref_dir.mkdir(parents=True, exist_ok=True)
            if existing is None:
                existing = {
                    "slug": slug,
                    "style_code": entry["code"],
                    "display_name": meta["name"],
                    "name_en": entry["code_full"].replace("#", "").upper(),
                    "aliases": meta["aliases"],
                    "phrase": meta["phrase"],
                    "cuff": meta["cuff"],
                    "sleeve": meta["sleeve"],
                    "kind": meta["kind"],
                    "fit": meta["fit"],
                    "source": f"{entry['sheet']} / {entry['code_full']}\n{entry['title']}",
                    "colors": [],
                }
                if meta.get("note"):
                    existing["notes"] = meta["note"]
                if meta.get("replaced_by"):
                    existing["replaced_by"] = meta["replaced_by"]
                styles.append(existing)
                by_code[entry["code"]] = existing
            else:
                # 老款式：只补品类/袖长（新字段），phrase/fit/别名保持已调好的版本
                existing.setdefault("kind", (meta or {}).get("kind", "top"))
                existing.setdefault("sleeve", (meta or {}).get("sleeve", "short"))
                existing.setdefault("cuff", (meta or {}).get("cuff", "plain"))
                # 本表建档的款式（STYLE_META 里有）→ 文案跟着表里的元数据走，重跑即刷新
                if meta and meta["slug"] == slug:
                    existing.update(
                        {
                            "style_code": entry["code"],
                            "display_name": meta["name"],
                            "name_en": entry["code_full"].replace("#", "").upper(),
                            "aliases": meta["aliases"],
                            "phrase": meta["phrase"],
                            "cuff": meta["cuff"],
                            "sleeve": meta["sleeve"],
                            "kind": meta["kind"],
                            "fit": meta["fit"],
                            "source": f"{entry['sheet']} / {entry['code_full']}\n{entry['title']}",
                        }
                    )
                    if meta.get("note"):
                        existing["notes"] = meta["note"]
                    if meta.get("replaced_by"):
                        existing["replaced_by"] = meta["replaced_by"]
                    else:
                        existing.pop("replaced_by", None)
                elif meta and meta.get("note") and not existing.get("notes"):
                    existing["notes"] = meta["note"]
            old_by_code = {
                str(c.get("color_code") or "").upper(): c for c in existing.get("colors") or []
            }
            used_labels = {str(c.get("label") or "") for c in existing.get("colors") or []}
            colors_out: list[dict] = []
            for index, color in enumerate(entry["colors"], start=1):
                code = str(color["color_code"]).upper()
                previous = old_by_code.get(code) or {}
                label = previous.get("label") or label_for(color["name_zh"], index, used_labels)
                media = color["media"]
                effect_rel = previous.get("effect") or ""
                image_rel = previous.get("image") or ""
                hex_value = previous.get("hex") or ""
                if media:
                    raw = z.read(media)
                    suffix = Path(media).suffix.lower() or ".jpg"
                    effect_path = eff_dir / f"{code}{suffix}"
                    effect_path.write_bytes(raw)
                    effect_rel = effect_path.relative_to(LIB).as_posix()
                    try:
                        with Image.open(BytesIO(raw)) as handle:
                            image = ImageOps.exif_transpose(handle).convert("RGB")
                            hex_value, _ = fabric_hex(image)
                            image.thumbnail((900, 900))
                            ref_path = ref_dir / f"{label}.jpg"
                            image.save(ref_path, format="JPEG", quality=85, optimize=True)
                            image_rel = ref_path.relative_to(LIB).as_posix()
                    except Exception as exc:                    # noqa: BLE001
                        missing.append(f"{slug}/{code}: 缩略图失败 {exc}")
                    written += 1
                else:
                    missing.append(f"{slug}/{code}: 表里这一行没有效果图")
                colors_out.append(
                    {
                        "label": label,
                        "name_zh": color["name_zh"],
                        "color_code": code,
                        "hex": hex_value,
                        "image": image_rel,
                        "effect": effect_rel,
                        "effect_source": f"{entry['code_full']} / {color['name_zh']} / "
                                         f"{media.split('/')[-1]}",
                    }
                )
            existing["colors"] = colors_out

    styles.sort(key=lambda s: str(s.get("style_code") or "").zfill(3))
    STYLES.write_text(json.dumps(styles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n写出 {written} 张效果图 → {EFFECTS}")
    print(f"styles.json：{len(styles)} 个款式 / "
          f"{sum(len(s.get('colors') or []) for s in styles)} 个色号")
    if missing:
        print(f"有问题的色号 {len(missing)} 条：")
        for line in missing[:10]:
            print("  -", line)
    meta = {
        "source_file": xlsx.name,
        "source_sha256": hashlib.sha256(xlsx.read_bytes()).hexdigest(),
        "source_bytes": xlsx.stat().st_size,
        "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "styles": len(styles),
        "images": written,
    }
    (EFFECTS / "manifest.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("manifest:", json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
