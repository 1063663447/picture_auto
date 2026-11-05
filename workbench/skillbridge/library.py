"""款式库（workbench/library）：把《墨西哥-服装款式代号表》里的款式接进工作台。

原 skill（apparel-style-recolor）保持不动，这里做一层**叠加**：

* `library/styles.json`   —— 款式 → 代号 / 展示名 / 版型短语 / 别名 / 颜色（label、中文名、色号、HEX、参考图）
* `library/refs/<slug>/`  —— 每个颜色一张效果图，既做界面缩略图，也作为「参考图」发给模型

`load_library()` 返回可以直接塞进 `Skill.types` 的 GarmentType 列表；`merge_into_skill()`
负责合并（同一 slug 以 skill 为准，避免覆盖手工维护的款式）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .skill import (
    CUFF_PLAIN,
    CUFF_VALUES,
    SLEEVE_VALUES,
    ColorRef,
    GarmentType,
    Skill,
    infer_cuff,
    infer_sleeve,
    style_key_from,
)

LIBRARY_DIR = Path(__file__).resolve().parent.parent / "library"
MANIFEST_NAME = "styles.json"


@dataclass
class LibraryColors:
    slug: str
    name: str


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = (value or "").strip().lstrip("#")
    if len(text) != 6:
        return (128, 128, 128)
    try:
        return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (128, 128, 128)


#: 平口款式里不该出现的螺纹措辞 → 改成平口（否则提示词自相矛盾：
#: 一句说「ribbed cuffs」、另一句说「no ribbed band」，模型只会挑一个照做）
_RIBBED_CUFF_MENTIONS = (
    "ribbed cuffs and neckband",
    "ribbed cuffs and ribbed neckband",
    "ribbed cuffs and hem",
    "ribbed cuffs",
    "ribbed cuff",
    "ribbed hem",
)


def phrase_for_cuff(phrase: str, cuff: str) -> str:
    """按袖口工艺校正版型描述，去掉跟平口冲突的螺纹措辞。"""
    text = " ".join(str(phrase or "").split())
    if cuff != CUFF_PLAIN or "rib" not in text.lower():
        return text
    for mention in _RIBBED_CUFF_MENTIONS:
        if mention not in text.lower():
            continue
        start = text.lower().index(mention)
        head, tail = text[:start], text[start + len(mention) :]
        if "neckband" in mention:
            fixed = "plain flat cuffs and hem, ribbed neckband only"
        elif "cuff" in mention and "hem" in mention:
            fixed = "plain flat cuffs and hem"
        elif "cuff" in mention:
            fixed = "plain flat cuffs"
        else:
            fixed = "plain flat hem"
        text = " ".join(f"{head}{fixed}{tail}".split())
    return text


def load_library(
    base: Path | None = None, warnings: list[str] | None = None
) -> list[GarmentType]:
    """读 styles.json，返回 GarmentType 列表（按代号排序）。"""
    root = Path(base or LIBRARY_DIR)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        payload = payload.get("styles") or []

    types: list[GarmentType] = []
    for item in payload:
        slug = str(item.get("slug") or "").strip()
        if not slug:
            continue
        colors: list[ColorRef] = []
        for color in item.get("colors") or []:
            image = str(color.get("image") or "").strip()
            if not image:
                continue
            path = root / image
            if not path.is_file():
                continue
            hex_value = str(color.get("hex") or "").upper()
            if not hex_value.startswith("#"):
                hex_value = f"#{hex_value}"
            # 效果图（款式表里每色一张官方平铺图）：缺失就只发颜色参考图
            effect_path = None
            effect_rel = str(color.get("effect") or "").strip()
            if effect_rel:
                candidate = root / effect_rel
                if candidate.is_file():
                    effect_path = candidate
            colors.append(
                ColorRef(
                    label=str(color.get("label") or path.stem),
                    path=path,
                    hex=hex_value,
                    rgb=_hex_to_rgb(hex_value),
                    name_zh=str(color.get("name_zh") or ""),
                    # 色号编码（WH[0] / BK[0] …）：成品文件名用它
                    code=str(color.get("color_code") or ""),
                    effect=effect_path,
                )
            )
        if not colors:
            continue
        aliases = [str(a) for a in (item.get("aliases") or []) if str(a).strip()]
        display = str(item.get("display_name") or slug)
        if display and display not in aliases:
            aliases.insert(0, display)
        phrase = str(item.get("phrase") or "")
        # 袖口工艺：JSON 里显式写就以它为准（并容错），没写才按描述推断
        cuff = str(item.get("cuff") or "").strip().lower()
        if cuff not in CUFF_VALUES:
            cuff = infer_cuff(slug, aliases, phrase)
        # 袖长：短袖 / 长袖（画错就「不是这件衣服」，跟着款式走）
        sleeve = str(item.get("sleeve") or "").strip().lower()
        if sleeve not in SLEEVE_VALUES:
            sleeve = infer_sleeve(slug, aliases, phrase)
        fixed = phrase_for_cuff(phrase, cuff)
        if fixed != " ".join(phrase.split()) and warnings is not None:
            warnings.append(
                f"款式 {slug} 标为平口，版型描述里的螺纹措辞已改正：{fixed}"
            )
        phrase = fixed
        # 英文款式代号（24CXCR）：JSON 里写了就用，没写从 `24#CXCR` 这类别名里取
        name_en = str(item.get("name_en") or "").strip().upper()
        if not name_en:
            name_en = style_key_from(str(item.get("style_code") or ""), aliases)
        types.append(
            GarmentType(
                slug=slug,
                aliases=aliases or [slug],
                ref_dir=root / "refs" / slug,
                phrase=phrase,
                colors=colors,
                cuff=cuff,
                sleeve=sleeve,
                name_en=name_en,
                fit=str(item.get("fit") or "").strip(),
            )
        )
    types.sort(key=lambda g: g.slug)
    return types


def merge_into_skill(skill: Skill, base: Path | None = None) -> int:
    """把款式库并进 skill.types（不覆盖已有 slug），返回新增数量。"""
    added = 0
    for garment in load_library(base, warnings=skill.warnings):
        if garment.slug in skill.types:
            skill.warnings.append(f"款式库跳过 {garment.slug}：skill 里已有同名款式")
            continue
        skill.types[garment.slug] = garment
        added += 1
    return added
