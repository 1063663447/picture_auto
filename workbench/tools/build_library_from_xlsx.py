"""把 墨西哥款式表 解析成工作台款式库（styles.json）+ 抽出每种颜色的效果图。

输出：
  workbench/library/refs/<slug>/<label>.png   每种颜色的参考图（缩到长边 1000，体积可控）
  workbench/library/styles.json               款式/颜色/hex/图片清单
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from collections import Counter
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"C:\Users\admin\.dsh\attachments\v1\files\0b"
    r"\0ba7247b76eca759909465a67220e900cce6cb414c09bbaf53309a21d54503ea"
    r"\墨西哥-服装款式代号表.xlsx"
)
LIB = ROOT / "library"
REFS = LIB / "refs"
MANIFEST = LIB / "styles.json"

REL_WB = re.compile(r"<Relationship[^>]*Id=\"(rId\d+)\"[^>]*Target=\"([^\"]+)\"")
REL_ANY = re.compile(r"<Relationship[^>]*Target=\"([^\"]+)\"[^>]*Id=\"(rId\d+)\"|"
                     r"<Relationship[^>]*Id=\"(rId\d+)\"[^>]*Target=\"([^\"]+)\"")
CELL_RE = re.compile(r"<c r=\"([A-Z]+)(\d+)\"([^>]*)>(?:<v>(.*?)</v>)?", re.S)
ANCHOR_RE = re.compile(
    r"<xdr:(?:twoCellAnchor|oneCellAnchor)[^>]*>(.*?)</xdr:(?:twoCellAnchor|oneCellAnchor)>", re.S
)
FROM_ROW = re.compile(r"<xdr:from>\s*<xdr:col>\d+</xdr:col>\s*<xdr:colOff>\d+</xdr:colOff>\s*"
                     r"<xdr:row>(\d+)</xdr:row>")
BLIP = re.compile(r"<a:blip[^>]*r:embed=\"(rId\d+)\"")

# 中文颜色名 → (英文 label, 展示名)
COLOR_MAP: dict[str, tuple[str, str]] = {
    "白色": ("white", "白色"), "黑色": ("black", "黑色"), "花灰色": ("heather-grey", "花灰色"),
    "麻灰色": ("heather-grey", "麻灰色"), "灰色": ("grey", "灰色"), "深灰色": ("dark-grey", "深灰色"),
    "浅灰色": ("light-grey", "浅灰色"), "炭灰色": ("charcoal", "炭灰色"),
    "酒红色": ("burgundy", "酒红色"), "酒红": ("burgundy", "酒红色"),
    "红色": ("red", "红色"), "大红色": ("bright-red", "大红色"), "玫红色": ("rose-red", "玫红色"),
    "紫红色": ("plum", "紫红色"),
    "宝蓝色": ("royal-blue", "宝蓝色"), "藏青色": ("navy", "藏青色"), "藏蓝": ("navy", "藏青色"),
    "蓝色": ("blue", "蓝色"), "深蓝色": ("deep-blue", "深蓝色"), "浅蓝色": ("light-blue", "浅蓝色"),
    "天蓝色": ("sky-blue", "天蓝色"),
    "墨绿色": ("forest-green", "墨绿色"), "绿色": ("green", "绿色"), "军绿色": ("army-green", "军绿色"),
    "杏色": ("apricot", "杏色"), "米白色": ("off-white", "米白色"), "米白": ("off-white", "米白色"),
    "米色": ("beige", "米色"), "燕麦色": ("oatmeal", "燕麦色"), "卡其色": ("khaki", "卡其色"),
    "粉色": ("pink", "粉色"), "浅粉色": ("light-pink", "浅粉色"),
    "紫色": ("purple", "紫色"), "浅紫色": ("lavender", "浅紫色"),
    "棕色": ("brown", "棕色"), "咖啡色": ("coffee", "咖啡色"),
    "黄色": ("yellow", "黄色"), "姜黄色": ("mustard", "姜黄色"),
    "橙色": ("orange", "橙色"), "橘色": ("orange", "橘色"),
    "花红色": ("heather-red", "花红色"), "花蓝色": ("heather-blue", "花蓝色"),
    "花绿色": ("heather-green", "花绿色"),
}

#: 颜色编码形如 BK[0] / FGY[0]；不像的都当作说明文字跳过
COLOR_CODE_RE = __import__("re").compile(r"^[A-Z]{1,5}\[\d+\]$")
NOTE_HINTS = ("【", "选图", "材质", "定价", "价格", "说明", "http", "注意")

# 款式代号 → 英文描述（给生图模型看的版型短语）+ 展示名
STYLE_META: dict[str, dict] = {
    "03": {
        "slug": "03-crewneck-sweatshirt",
        "name": "03 圆领卫衣",
        "phrase": "a crewneck sweatshirt (no hood, ribbed collar, cuffs and hem, mid-weight "
                  "brushed fleece body)",
        "aliases": ["03 圆领卫衣", "圆领卫衣", "03#YLWY", "crewneck sweatshirt", "sweatshirt"],
    },
    "06": {
        "slug": "06-hoodie",
        "name": "06 连帽卫衣",
        "phrase": "a pullover hoodie (attached hood with drawstrings, kangaroo pocket, ribbed "
                  "cuffs and hem, mid-weight brushed fleece body)",
        "aliases": ["06 连帽卫衣", "连帽卫衣", "06#LMWY", "hoodie", "hooded sweatshirt"],
    },
    "07": {
        "slug": "07-washed-tee",
        "name": "07 水洗T恤",
        "phrase": "an oversized washed / vintage-finish t-shirt (garment-dyed, slightly faded "
                  "cotton surface, dropped shoulders, ribbed neckband)",
        "aliases": ["07 水洗T恤", "水洗T恤", "07#SXT", "washed tee", "vintage t-shirt"],
    },
    "10": {
        "slug": "10-stretch-tee",
        "name": "10 弹力短袖",
        "phrase": "a stretch short-sleeve t-shirt in fine rib-knit jersey (dense vertical ribs "
                  "across the body and both sleeves, fitted body, short sleeves, ribbed neckband, "
                  "plain folded sleeve and body hems)",
        "aliases": ["10 弹力短袖", "弹力短袖", "10#TLDX", "stretch t-shirt", "stretch tee"],
    },
    "21": {
        "slug": "21-kids-sorona-tee",
        "name": "21 童装索罗娜",
        "phrase": "a kids short-sleeve t-shirt in Sorona polyester blend (small child fit, "
                  "short sleeves, ribbed neckband)",
        "aliases": ["21 童装索罗娜", "21 童装", "21#TZSLN", "kids tee", "kids t-shirt"],
    },
    "23": {
        "slug": "23-sorona-tee",
        "name": "23 索罗娜短袖",
        "phrase": "a relaxed-fit short-sleeve t-shirt in Sorona polyester blend (soft drape, "
                  "short sleeves, ribbed neckband)",
        "aliases": ["23 索罗娜短袖", "23 索罗娜", "23#SLNDX", "sorona tee", "sorona t-shirt"],
    },
    "24": {
        "slug": "24-long-sleeve",
        "name": "24 长袖成人",
        "phrase": "a long-sleeve t-shirt for adults (cotton jersey, ribbed cuffs and neckband)",
        "aliases": ["24 长袖成人", "24 长袖", "24#CXCR", "long-sleeve tee", "long sleeve t-shirt"],
    },
    "25": {
        "slug": "25-kids-long-sleeve",
        "name": "25 长袖童装",
        "phrase": "a kids long-sleeve t-shirt (small child fit, cotton jersey, ribbed cuffs "
                  "and neckband)",
        "aliases": ["25 长袖童装", "25 童装长袖", "25#CXTZ", "kids long-sleeve"],
    },
    "28": {
        "slug": "28-kids-sweatshirt",
        "name": "28 卫衣童装",
        "phrase": "a kids sweatshirt (small child fit, crewneck, ribbed cuffs and hem, "
                  "brushed fleece body)",
        "aliases": ["28 卫衣童装", "28 童装卫衣", "28#WYTZ", "kids sweatshirt"],
    },
    "29": {
        "slug": "29-kids-washed-tee",
        "name": "29 水洗童装",
        "phrase": "a kids washed / vintage-finish t-shirt (garment-dyed cotton, slightly faded "
                  "surface, ribbed neckband)",
        "aliases": ["29 水洗童装", "29 童装水洗", "29#SXTZ", "kids washed tee"],
    },
}


def fabric_hex(image: Image.Image) -> tuple[str, tuple[int, int, int]]:
    """取中间区域、剔除过曝/纯背景像素后的主色。"""
    img = ImageOps.exif_transpose(image).convert("RGB")
    w, h = img.size
    crop = img.crop((int(w * 0.25), int(h * 0.2), int(w * 0.75), int(h * 0.8)))
    crop.thumbnail((160, 160))
    pixels = list(crop.getdata())
    kept = []
    for r, g, b in pixels:
        mx, mn = max(r, g, b), min(r, g, b)
        if mx > 246 and mx - mn < 12:      # 纯白背景
            continue
        if mx < 12:                        # 纯黑阴影
            continue
        kept.append((r, g, b))
    sample = kept or pixels
    sample.sort(key=lambda p: p[0] * 0.3 + p[1] * 0.6 + p[2] * 0.1)
    r, g, b = sample[len(sample) // 2]
    return f"#{r:02X}{g:02X}{b:02X}", (r, g, b)


def parse_workbook(zf: zipfile.ZipFile) -> list[dict]:
    shared: list[str] = []
    if "xl/sharedStrings.xml" in zf.namelist():
        data = zf.read("xl/sharedStrings.xml").decode("utf-8", "replace")
        for si in re.findall(r"<si>(.*?)</si>", data, re.S):
            shared.append("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))

    wb = zf.read("xl/workbook.xml").decode("utf-8", "replace")
    sheets = re.findall(r"<sheet[^>]*name=\"([^\"]+)\"[^>]*r:id=\"(rId\d+)\"", wb)
    wb_rels = dict(
        (rid, target) for rid, target in REL_WB.findall(
            zf.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace")
        )
    )

    styles: list[dict] = []
    for sheet_name, rid in sheets:
        if sheet_name == "目录":
            continue
        target = wb_rels[rid].split("/")[-1]
        body = zf.read(f"xl/worksheets/{target}").decode("utf-8", "replace")
        rows: dict[int, dict[str, str]] = {}
        for ref, row_no, attrs, value in CELL_RE.findall(body):
            if not value:
                continue
            text = shared[int(value)] if 't="s"' in attrs and int(value) < len(shared) else value
            rows.setdefault(int(row_no), {})[ref] = text
        title = (rows.get(1, {}).get("A") or "").replace("\n", " ").strip()

        colors = []
        for row_no in sorted(rows):
            if row_no < 4:
                continue
            name = (rows[row_no].get("A") or "").strip()
            code = (rows[row_no].get("B") or "").strip()
            if not name or not COLOR_CODE_RE.match(code):
                continue      # 表里还混着说明段落/材质栏，靠颜色编码过滤掉
            if len(name) > 12 or any(hint in name for hint in NOTE_HINTS) or "\n" in name:
                continue
            if name and name not in {c["name"] for c in colors}:
                colors.append({"name": name, "code": code, "row": row_no})

        # 该 sheet 的 drawing → 行号 ↔ 图片
        drawing_map: dict[int, str] = {}
        sheet_rels = f"xl/worksheets/_rels/{target}.rels"
        if sheet_rels in zf.namelist():
            rels_body = zf.read(sheet_rels).decode("utf-8", "replace")
            drawing = ""
            for rid2, tgt in REL_WB.findall(rels_body):
                if "drawings/drawing" in tgt:
                    drawing = tgt
            if drawing:
                drawing_path = "xl/" + drawing.replace("../", "").lstrip("/")
                d_body = zf.read(drawing_path).decode("utf-8", "replace")
                d_rels_path = f"xl/drawings/_rels/{Path(drawing_path).name}.rels"
                dmap: dict[str, str] = {}
                if d_rels_path in zf.namelist():
                    for rid3, tgt3 in REL_WB.findall(
                        zf.read(d_rels_path).decode("utf-8", "replace")
                    ):
                        dmap[rid3] = tgt3
                for block in ANCHOR_RE.findall(d_body):
                    row_match = FROM_ROW.search(block)
                    blip = BLIP.search(block)
                    if not row_match or not blip:
                        continue
                    row_index = int(row_match.group(1)) + 1
                    media = dmap.get(blip.group(1), "")
                    if not media:
                        continue
                    # drawing 里的 Target 是相对 xl/drawings/ 的：../media/x.jpg → xl/drawings/media/x.jpg
                    media_path = "xl/drawings/" + media.replace("../", "").lstrip("/")
                    drawing_map.setdefault(row_index, media_path)

        styles.append({
            "sheet": sheet_name,
            "title": title,
            "colors": colors,
            "images": drawing_map,
        })
    return styles


def style_code(sheet_name: str, title: str) -> str:
    match = re.match(r"(\d+)", sheet_name.strip())
    if match:
        return match.group(1)
    match = re.match(r"(\d+)#", title.strip())
    return match.group(1) if match else sheet_name[:2]


def main() -> int:
    LIB.mkdir(parents=True, exist_ok=True)
    if REFS.exists():                       # 重建时清掉旧图，避免残留不同命名的文件
        import shutil

        shutil.rmtree(REFS)
    REFS.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    missing: list[str] = []

    with zipfile.ZipFile(SRC) as zf:
        styles = parse_workbook(zf)
        for sheet in styles:
            code = style_code(sheet["sheet"], sheet["title"])
            meta = STYLE_META.get(code)
            if not meta:
                print(f"[!] 未配置款式元数据：{sheet['sheet']} / {sheet['title']}")
                continue
            slug = meta["slug"]
            out_dir = REFS / slug
            out_dir.mkdir(parents=True, exist_ok=True)
            colors_out: list[dict] = []
            used_labels: set[str] = set()

            for index, color in enumerate(sheet["colors"], start=1):
                name = color["name"]
                label, display = COLOR_MAP.get(name, (f"c{index}-{slug[:2]}", name))
                if label in used_labels:
                    label = f"{label}-{index}"
                used_labels.add(label)
                media = sheet["images"].get(color["row"], "")
                entry = {
                    "label": label,
                    "name_zh": display,
                    "color_code": color["code"],
                    "hex": "",
                    "image": "",
                }
                if media:
                    try:
                        raw = zf.read(media)
                        with Image.open(BytesIO(raw)) as handle:
                            image = ImageOps.exif_transpose(handle).convert("RGB")
                            entry["hex"], _ = fabric_hex(image)
                            image.thumbnail((900, 900))
                            target = out_dir / f"{label}.jpg"
                            image.save(target, format="JPEG", quality=85, optimize=True)
                            entry["image"] = str(target.relative_to(LIB)).replace("\\", "/")
                    except Exception as exc:  # noqa: BLE001
                        missing.append(f"{slug}/{label}: {exc}")
                else:
                    missing.append(f"{slug}/{label}: 无效果图")
                colors_out.append(entry)

            manifest.append({
                "slug": slug,
                "style_code": code,
                "display_name": meta["name"],
                "aliases": meta["aliases"],
                "phrase": meta["phrase"],
                "source": f"{sheet['sheet']} / {sheet['title']}",
                "colors": colors_out,
            })
            print(f"{meta['name']:<14} 颜色 {len(colors_out):>2} 个 → refs/{slug}/")

    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    total_colors = sum(len(s["colors"]) for s in manifest)
    print(f"\n款式 {len(manifest)} 个 · 颜色 {total_colors} 个 · 清单 {MANIFEST}")
    if missing:
        print("缺口:", len(missing))
        for item in missing[:10]:
            print("  -", item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
