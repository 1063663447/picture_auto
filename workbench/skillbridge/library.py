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

from .skill import ColorRef, GarmentType, Skill

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


def load_library(base: Path | None = None) -> list[GarmentType]:
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
            colors.append(
                ColorRef(
                    label=str(color.get("label") or path.stem),
                    path=path,
                    hex=hex_value,
                    rgb=_hex_to_rgb(hex_value),
                    name_zh=str(color.get("name_zh") or ""),
                )
            )
        if not colors:
            continue
        aliases = [str(a) for a in (item.get("aliases") or []) if str(a).strip()]
        display = str(item.get("display_name") or slug)
        if display and display not in aliases:
            aliases.insert(0, display)
        types.append(
            GarmentType(
                slug=slug,
                aliases=aliases or [slug],
                ref_dir=root / "refs" / slug,
                phrase=str(item.get("phrase") or ""),
                colors=colors,
            )
        )
    types.sort(key=lambda g: g.slug)
    return types


def merge_into_skill(skill: Skill, base: Path | None = None) -> int:
    """把款式库并进 skill.types（不覆盖已有 slug），返回新增数量。"""
    added = 0
    for garment in load_library(base):
        if garment.slug in skill.types:
            skill.warnings.append(f"款式库跳过 {garment.slug}：skill 里已有同名款式")
            continue
        skill.types[garment.slug] = garment
        added += 1
    return added
