"""把《墨西哥-服装款式代号表》里的**效果图**导入款式库，并写回 `library/styles.json`。

效果图 = 每个款式每个颜色一张官方平铺产品图（1500×1500 白底），既是**版型的权威参考**
（袖长/袖口/衣宽/下摆/领型都照着它），也是**该颜色的色卡**。生成时随原图一起发给模型，
同款各色的版型就由这张图统一，不再靠「先出基准色」那套流程。

表格结构（每个款式一个 sheet，实测 2026-11 版）：
    第 1 行 A 列：`23#SLNDX 宽松款短袖索罗娜材质`
    表头行：      颜色名称 | 颜色编码 | 尺码编码 | 衣服重量g | 面料重量g | 效果图 | 包装规格 | 代发价格RMB
    数据行：      颜色名 | 色号(WH[0]) | … | ← F 列锚定该颜色的效果图

用法：
    python tools/import_style_catalog.py                       # 用 library/source/*.xlsx
    python tools/import_style_catalog.py --xlsx=<路径.xlsx>
    python tools/import_style_catalog.py --dry-run             # 只报告不改文件
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"
STYLES = LIB / "styles.json"
EFFECTS = LIB / "effects"
SOURCE_DIR = LIB / "source"

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
XDR = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

CODE_RE = re.compile(r"^\s*(\d{1,3})\s*#\s*([A-Za-z0-9]+)")


def col_index(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref).group(1)
    value = 0
    for ch in letters:
        value = value * 26 + (ord(ch) - 64)
    return value - 1


def row_index(ref: str) -> int:
    return int(re.search(r"(\d+)", ref).group(1)) - 1


def _cells(z: zipfile.ZipFile, strings: list[str], path: str) -> dict[tuple[int, int], str]:
    root = ET.fromstring(z.read(path))
    out: dict[tuple[int, int], str] = {}
    for cell in root.iter(f"{M}c"):
        ref = cell.get("r")
        kind = cell.get("t")
        value = cell.find(f"{M}v")
        inline = cell.find(f"{M}is")
        if kind == "s" and value is not None and value.text is not None:
            text = strings[int(value.text)] if int(value.text) < len(strings) else ""
        elif kind == "inlineStr" and inline is not None:
            text = "".join(t.text or "" for t in inline.iter(f"{M}t"))
        else:
            text = value.text if value is not None else ""
        text = (text or "").strip()
        if text:
            out[(row_index(ref), col_index(ref))] = text
    return out


def _anchors(z: zipfile.ZipFile, sheet_path: str) -> list[dict]:
    rel_path = sheet_path.replace("xl/worksheets/", "xl/worksheets/_rels/") + ".rels"
    if rel_path not in z.namelist():
        return []
    rels = ET.fromstring(z.read(rel_path))
    target = ""
    for rel in rels:
        if "drawing" in (rel.get("Type") or ""):
            target = rel.get("Target") or ""
    if not target:
        return []
    # Target 可能是相对 `xl/worksheets/` 的 `../drawings/drawing5.xml`
    import posixpath

    if target.startswith("/"):
        drawing_path = target.lstrip("/")
    else:
        drawing_path = posixpath.normpath(
            posixpath.join(posixpath.dirname(sheet_path), target)
        )
    if not drawing_path.startswith("xl/"):
        drawing_path = "xl/" + drawing_path
    drawing_rels = drawing_path.replace("xl/drawings/", "xl/drawings/_rels/") + ".rels"
    media_map: dict[str, str] = {}
    if drawing_rels in z.namelist():
        for rel in ET.fromstring(z.read(drawing_rels)):
            name = (rel.get("Target") or "").split("/")[-1]
            for candidate in z.namelist():
                if candidate.endswith("/media/" + name):
                    media_map[rel.get("Id")] = candidate
                    break
    out = []
    root = ET.fromstring(z.read(drawing_path))
    for anchor in root:
        frm = anchor.find(f"{XDR}from")
        if frm is None:
            continue
        col = int(frm.find(f"{XDR}col").text)
        row = int(frm.find(f"{XDR}row").text)
        for blip in anchor.iter(f"{A}blip"):
            media = media_map.get(blip.get(f"{R}embed") or "")
            if media:
                out.append({"row": row, "col": col, "media": media})
    return out


def _shared(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return [
        "".join(t.text or "" for t in si.iter(f"{M}t"))
        for si in root.findall(f"{M}si")
    ]


def read_catalog(xlsx: Path) -> list[dict]:
    """返回 [{sheet, code, name, colors: [{name_zh, color_code, media}]}]。"""
    z = zipfile.ZipFile(xlsx)
    strings = _shared(z)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {rel.get("Id"): rel.get("Target") for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    out: list[dict] = []
    for sheet in wb.find(f"{M}sheets"):
        title = sheet.get("name")
        target = rels.get(sheet.get(f"{R}id"), "")
        path = target.lstrip("/")
        if not path.startswith("xl/"):
            path = "xl/" + path
        cells = _cells(z, strings, path)
        images = _anchors(z, path)
        if not images:
            continue
        head = cells.get((0, 0), "")
        match = CODE_RE.match(head)
        if not match:
            continue
        # 表头行：找「效果图」那一格的列号
        effect_col = None
        header_row = None
        for (row, col), text in cells.items():
            if text.startswith("效果图"):
                effect_col, header_row = col, row
                break
        if effect_col is None:
            continue
        # 效果图锚点：列 == effect_col，行 >= 表头
        by_row: dict[int, str] = {}
        for item in images:
            if item["col"] == effect_col and item["row"] >= header_row:
                by_row.setdefault(item["row"], item["media"])
        # 颜色行：A 列颜色名 + B 列色号
        header_cells = {col: text for (row, col), text in cells.items() if row == header_row}
        code_col = next((c for c, t in header_cells.items() if t.startswith("颜色编码")), 1)
        name_col = next((c for c, t in header_cells.items() if t.startswith("颜色名称")), 0)
        colors = []
        for (row, col), text in sorted(cells.items()):
            if row <= header_row or col != name_col or row not in by_row:
                continue
            color_code = cells.get((row, code_col), "")
            if not color_code:
                continue
            colors.append(
                {
                    "name_zh": text,
                    "color_code": color_code,
                    "media": by_row[row],
                    "row": row + 1,
                }
            )
        out.append(
            {
                "sheet": title,
                "code": f"{match.group(1)}#{(match.group(2) or '').upper()}",
                "name": head[match.end():].strip(),
                "colors": colors,
                "zip": z,
            }
        )
    return out


def main() -> int:
    xlsx_arg = ""
    for item in sys.argv:
        if item.startswith("--xlsx="):
            xlsx_arg = item.split("=", 1)[1]
    dry = "--dry-run" in sys.argv
    if xlsx_arg:
        xlsx = Path(xlsx_arg)
    else:
        found = sorted(SOURCE_DIR.glob("*.xlsx")) if SOURCE_DIR.is_dir() else []
        if not found:
            print("没找到 xlsx：请放到 library/source/ 或用 --xlsx=<路径>")
            return 2
        xlsx = found[0]
    if not xlsx.is_file():
        print(f"xlsx 不存在：{xlsx}")
        return 2

    payload = json.loads(STYLES.read_text(encoding="utf-8"))
    styles = payload if isinstance(payload, list) else (payload.get("styles") or [])
    by_code = {}
    for style in styles:
        name_en = str(style.get("name_en") or "").upper()
        if name_en:
            by_code[name_en] = style

    catalog = read_catalog(xlsx)
    print(f"读到 {len(catalog)} 个款式 sheet（{xlsx.name}）")
    picked: list[dict] = []
    for entry in catalog:
        key = entry["code"].replace("#", "").upper()
        style = by_code.get(key)
        if style is None:
            print(f"  [skip] {entry['code']}（styles.json 里没有这个代号）")
            continue
        have = {str(c.get("color_code") or "").upper() for c in style.get("colors") or []}
        matched = []
        for color in entry["colors"]:
            code = str(color["color_code"]).upper()
            if code not in have:
                print(f"  [skip] {entry['code']} / {color['name_zh']} {code}（styles.json 没有这个色号）")
                continue
            matched.append(color)
        picked.append({"entry": entry, "style": style, "colors": matched})
        print(f"  {entry['code']} → {style['slug']}：{len(matched)}/{len(entry['colors'])} 色有图")

    if dry:
        total = sum(len(p["colors"]) for p in picked)
        print(f"\n[dry-run] 预计导入 {total} 张效果图")
        return 0

    EFFECTS.mkdir(parents=True, exist_ok=True)
    z = zipfile.ZipFile(xlsx)
    imported = 0
    for item in picked:
        entry, style = item["entry"], item["style"]
        slug = style["slug"]
        folder = EFFECTS / slug
        folder.mkdir(parents=True, exist_ok=True)
        for color in item["colors"]:
            media = color["media"]
            ext = Path(media).suffix.lower() or ".jpg"
            target = folder / f"{str(color['color_code']).upper()}{ext}"
            target.write_bytes(z.read(media))
            rel = target.relative_to(LIB).as_posix()
            for entry_color in style.get("colors") or []:
                if str(entry_color.get("color_code") or "").upper() == str(color["color_code"]).upper():
                    entry_color["effect"] = rel
                    entry_color["effect_source"] = f"{entry['code']} / {color['name_zh']} / {media.split('/')[-1]}"
            imported += 1
    payload_out = payload
    STYLES.write_text(json.dumps(payload_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    missing = [
        f"{style['slug']}/{color.get('color_code')}"
        for style in styles
        for color in style.get("colors") or []
        if not color.get("effect")
    ]
    print(f"\n导入 {imported} 张效果图 → {EFFECTS}")
    print(f"styles.json 已更新（{len(styles)} 款式）")
    if missing:
        print(f"仍缺效果图的色号（{len(missing)}）：{', '.join(missing[:20])}")
    meta = {
        "source_file": xlsx.name,
        "source_sha256": hashlib.sha256(xlsx.read_bytes()).hexdigest(),
        "source_bytes": xlsx.stat().st_size,
        "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "images": imported,
    }
    (EFFECTS / "manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("manifest:", json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
