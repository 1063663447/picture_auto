"""Load the apparel-style-recolor skill as machine-readable data.

Everything the workbench sends to an image API is derived from the skill files
(`SKILL.md`, `references/color-reference.md`, `assets/color-references/`), so the
skill stays the single source of truth: edit the markdown, and the workbench
follows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps

# --------------------------------------------------------------------------- #
# Fallbacks (used only when a skill file cannot be parsed)
# --------------------------------------------------------------------------- #

FALLBACK_PHRASES: dict[str, str] = {
    "crewneck-sweatshirt": (
        "crewneck sweatshirt with a ribbed crew neckline, long sleeves, ribbed cuffs, "
        "and ribbed hem, with no hood, drawstrings, or zipper"
    ),
    "hoodie": (
        "hoodie with a natural hood, drawstrings, long sleeves, ribbed cuffs and a ribbed hem, "
        "and sweatshirt fabric"
    ),
    "short-sleeve": (
        "short-sleeve T-shirt with a natural short sleeve length, T-shirt collar, "
        "and lightweight knit fabric"
    ),
}

FALLBACK_LABELS: dict[str, list[str]] = {
    "crewneck-sweatshirt": ["圆领卫衣", "圆领套头卫衣", "crewneck sweatshirt"],
    "hoodie": ["连帽卫衣", "帽衫", "hoodie"],
    "short-sleeve": ["短袖", "短袖T恤", "T恤", "short-sleeve"],
}

#: 袖口/下摆工艺。T 恤是**平口**（平车折边），只有卫衣/帽衫才有**螺纹**。
#: 图片模型最容易在这里犯错：把 T 恤画成卫衣的罗纹袖口。
CUFF_PLAIN = "plain"
CUFF_RIBBED = "ribbed"
CUFF_VALUES = (CUFF_PLAIN, CUFF_RIBBED)
CUFF_ZH = {CUFF_PLAIN: "平口", CUFF_RIBBED: "螺纹"}

#: 这些词说明是卫衣系（螺纹袖口/下摆），出现在 slug / 别名 / 版型描述里都算。
RIBBED_HINTS = ("卫衣", "帽衫", "sweatshirt", "hoodie", "sweat shirt")

ZH_COLOR_NAMES: dict[str, str] = {
    "black": "黑色",
    "white": "白色",
    "gray": "灰色",
    "grey": "灰色",
    "heather-gray": "麻灰色",
    "heather-grey": "麻灰色",
    "apricot": "杏色",
    "burgundy": "酒红色",
    "blue": "蓝色",
    "brown": "棕色",
    "dark-green": "深绿色",
    "pink": "粉色",
    "red": "红色",
    "navy": "藏青色",
    "beige": "米色",
    "khaki": "卡其色",
}

TYPE_LINE = re.compile(r"^-\s*(?P<aliases>[^>]*?)\s*->\s*`(?P<dir>[^`]+?)`\s*$")
ALIAS_RE = re.compile(r"`([^`]+)`")
PHRASE_LINE = re.compile(r"^-\s*(?P<label>[^：:`]+?)\s*[：:]\s*`(?P<phrase>.+?)`\s*$")
MAX_BYTES_RE = re.compile(r"(\d[\d,]*)\s*字节")
#: 款式表里的代号写法：`24#CXCR` → 成品文件名用 `24CXCR`
STYLE_CODE_RE = re.compile(r"^(\d{1,3})\s*#\s*([A-Za-z]{2,8})$")

#: skill 自带的三个款式（不在款式表里）对应的英文代号，成品文件名不要长得像乱码
SKILL_STYLE_KEYS: dict[str, str] = {
    "crewneck-sweatshirt": "03YLWY",
    "hoodie": "06LMWY",
    "short-sleeve": "SHORT",
}

#: 没有色号编码时的兜底（skill 自带色卡没有 code）——按款式表的通用色号
COLOR_CODES: dict[str, str] = {
    "white": "WH[0]", "off-white": "WH[0]", "black": "BK[0]",
    "heather-grey": "FGY[0]", "grey": "GY[0]", "gray": "GY[0]", "dark-grey": "DGY[0]",
    "apricot": "APR[0]", "beige": "BE[0]", "oatmeal": "OT[0]", "khaki": "KH[0]",
    "burgundy": "DRD[0]", "red": "RD[0]", "bright-red": "RD[0]", "rose-red": "RSD[0]",
    "pink": "PK[0]", "light-pink": "LPK[0]",
    "royal-blue": "BU[0]", "blue": "BU[0]", "navy": "NV[0]", "deep-blue": "DBU[0]",
    "sky-blue": "SBU[0]", "light-blue": "LBU[0]",
    "forest-green": "GN[0]", "green": "GN[0]", "army-green": "AGN[0]",
    "brown": "BN[0]", "coffee": "CF[0]", "purple": "VT[0]", "lavender": "LV[0]",
    "yellow": "YL[0]", "mustard": "MT[0]", "orange": "OR[0]",
}


#: 袖长：short（短袖 T 恤）/ long（长袖 T 恤、卫衣、帽衫）
SLEEVE_SHORT = "short"
SLEEVE_LONG = "long"
SLEEVE_VALUES = (SLEEVE_SHORT, SLEEVE_LONG)
SLEEVE_ZH = {SLEEVE_SHORT: "短袖", SLEEVE_LONG: "长袖"}
LONG_SLEEVE_HINTS = ("long-sleeve", "long sleeve", "sweatshirt", "hoodie", "卫衣", "帽衫", "长袖")


def infer_sleeve(slug: str, aliases: Iterable[str] | None = None, phrase: str = "") -> str:
    """推断袖长：描述/款式名里有长袖、卫衣、帽衫 → 长袖；其余按短袖。"""
    text = " ".join([str(slug or ""), *(str(a) for a in (aliases or [])), str(phrase or "")]).lower()
    if any(hint in text for hint in LONG_SLEEVE_HINTS):
        return SLEEVE_LONG
    return SLEEVE_SHORT


def style_key_from(style_code: str, aliases: Iterable[str] | None = None) -> str:
    """从款式代号表里的 `24#CXCR` 拿到英文键 `24CXCR`（成品文件名/打包文件夹用）。"""
    for alias in aliases or ():
        match = STYLE_CODE_RE.match(str(alias or "").strip())
        if match:
            return f"{match.group(1)}{match.group(2).upper()}"
    code = str(style_code or "").strip()
    return code


def infer_cuff(slug: str, aliases: Iterable[str] | None = None, phrase: str = "") -> str:
    """推断袖口工艺：描述里写了 ribbed cuff，或款式名是卫衣 → 螺纹；其余一律平口。

    宁可默认平口：把 T 恤错画成罗纹袖口是硬伤（客户一眼看出不是自己那件衣服），
    而卫衣少了螺纹相对好补。
    """
    text = " ".join([str(slug or ""), *(str(a) for a in (aliases or [])), str(phrase or "")]).lower()
    if "ribbed cuff" in text or "ribbed cuffs" in text:
        return CUFF_RIBBED
    if any(hint in text for hint in RIBBED_HINTS):
        return CUFF_RIBBED
    return CUFF_PLAIN


@dataclass
class ColorRef:
    """One bundled color reference image."""

    label: str
    path: Path
    hex: str
    rgb: tuple[int, int, int]
    name_zh: str
    #: 色号编码，形如 `WH[0]` / `BK[0]`（来自款式表），成品文件名用它
    code: str = ""

    @property
    def file_code(self) -> str:
        """成品文件名里的色号：优先款式表给的 `WH[0]`，没有就按通用色号兜底。"""
        code = (self.code or "").strip()
        if code:
            return code
        return COLOR_CODES.get(self.label.lower(), self.label.upper().replace("-", ""))

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "code": self.code,
            "name_zh": self.name_zh,
            "hex": self.hex,
            "rgb": list(self.rgb),
            "url": f"/api/refs/{self.path.parent.name}/{self.path.name}",
        }


@dataclass
class GarmentType:
    """One supported target garment type."""

    slug: str
    aliases: list[str]
    ref_dir: Path
    phrase: str
    colors: list[ColorRef] = field(default_factory=list)
    #: "plain"（平口折边，T 恤）/ "ribbed"（罗纹，卫衣）
    cuff: str = CUFF_PLAIN
    #: "short"（短袖 T 恤）/ "long"（长袖 T 恤、卫衣、帽衫）
    sleeve: str = SLEEVE_SHORT
    #: 款式英文代号，形如 `24CXCR`（= 代号 24 + 字母 CXCR，来自款式表），成品文件名/文件夹用它
    name_en: str = ""

    @property
    def sleeve_zh(self) -> str:
        return SLEEVE_ZH.get(self.sleeve, SLEEVE_ZH[SLEEVE_SHORT])

    @property
    def file_key(self) -> str:
        """成品文件名和打包文件夹用的英文款式键（`24CXCR`）。"""
        name = (self.name_en or "").strip()
        if name:
            return name
        mapped = SKILL_STYLE_KEYS.get(self.slug)
        if mapped:
            return mapped
        return self.slug.upper().replace("-", "_")

    @property
    def cuff_zh(self) -> str:
        return CUFF_ZH.get(self.cuff, CUFF_ZH[CUFF_PLAIN])

    @property
    def is_ribbed(self) -> bool:
        return self.cuff == CUFF_RIBBED

    @property
    def display_name(self) -> str:
        return self.aliases[0] if self.aliases else self.slug

    @property
    def batch_name(self) -> str:
        return f"{self.slug}-style-recolor-batch"

    def color(self, key: str) -> ColorRef | None:
        key = key.strip().lower()
        for ref in self.colors:
            if ref.label.lower() == key or ref.name_zh == key:
                return ref
        return None

    def resolve_colors(self, keys: Iterable[str] | None) -> list[ColorRef]:
        """Resolve user-supplied color keys; empty/None means every bundled color."""
        keys = [k for k in (keys or []) if str(k).strip()]
        if not keys:
            return list(self.colors)
        seen: list[ColorRef] = []
        for key in keys:
            ref = self.color(str(key))
            if ref is None:
                raise KeyError(f"未知颜色 `{key}`，可用：{[c.label for c in self.colors]}")
            if ref not in seen:
                seen.append(ref)
        return seen

    def as_dict(self) -> dict:
        return {
            "slug": self.slug,
            "display_name": self.display_name,
            "name_en": self.file_key,
            "aliases": self.aliases,
            "phrase": self.phrase,
            "cuff": self.cuff,
            "cuff_zh": self.cuff_zh,
            "sleeve": self.sleeve,
            "sleeve_zh": self.sleeve_zh,
            "batch_name": self.batch_name,
            "colors": [c.as_dict() for c in self.colors],
        }


@dataclass
class Skill:
    root: Path
    types: dict[str, GarmentType]
    prompt_template: str
    negative_prompt: str
    max_bytes: int
    warnings: list[str] = field(default_factory=list)
    #: 不在界面「目标款式」下拉里展示的 slug（例如已被款式库取代的原生三款）
    hidden: set[str] = field(default_factory=set)

    def type_by_alias(self, name: str | None) -> GarmentType:
        """Match a free-form user string (`短袖T恤`, `hoodie`, ...) to a type.

        界面里隐藏的款式（`hidden`）排在后面：同一个别名同时命中时，
        优先用界面上能选到的那一款，避免"看到的和实际用的不是一款"。
        """
        if not name:
            raise KeyError("请先选择目标服装类型")
        raw = name.strip()
        lowered = raw.lower()
        ordered = [
            (slug, garment) for slug, garment in self.types.items() if slug not in self.hidden
        ] + [
            (slug, garment) for slug, garment in self.types.items() if slug in self.hidden
        ]
        if lowered in self.types:
            return self.types[lowered]
        for _slug, garment in ordered:
            for alias in garment.aliases:
                if alias.lower() == lowered:
                    return garment
        for _slug, garment in ordered:
            for alias in garment.aliases:
                if alias.lower() in lowered or lowered in alias.lower():
                    return garment
        raise KeyError(f"无法识别目标服装类型 `{name}`")

    def as_dict(self, *, include_hidden: bool = False) -> dict:
        items = {
            slug: garment.as_dict()
            for slug, garment in self.types.items()
            if include_hidden or slug not in self.hidden
        }
        return {
            "root": str(self.root),
            "types": items,
            "hidden": sorted(self.hidden),
            "prompt_template": self.prompt_template,
            "negative_prompt": self.negative_prompt,
            "max_bytes": self.max_bytes,
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #

def _blockquote_after(lines: list[str], marker: str) -> str:
    """Collect the first `> ...` block that follows a marker line."""
    start = None
    for index, line in enumerate(lines):
        if marker in line:
            start = index + 1
            break
    if start is None:
        return ""
    chunk: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if chunk:
                break
            continue
        if stripped.startswith(">"):
            chunk.append(stripped.lstrip(">").strip())
        elif chunk:
            break
    return " ".join(part for part in chunk if part).strip()


def _dominant_color(path: Path) -> tuple[str, tuple[int, int, int]]:
    """Estimate the garment body color of a reference photo.

    The reference images are product shots: the garment occupies the middle and
    the background is a flat studio tone. We drop the background tone, then take
    the per-channel median of what is left.
    """
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((96, 96))
        width, height = image.size
        pixels = list(image.getdata())

    if not pixels or width < 4 or height < 4:
        return "#808080", (128, 128, 128)

    def bucket(pixel: tuple[int, int, int]) -> tuple[int, int, int]:
        return (pixel[0] // 24, pixel[1] // 24, pixel[2] // 24)

    border = [
        pixels[y * width + x]
        for y in range(height)
        for x in range(width)
        if x < 2 or y < 2 or x >= width - 2 or y >= height - 2
    ]
    counts: dict[tuple[int, int, int], int] = {}
    for pixel in border:
        key = bucket(pixel)
        counts[key] = counts.get(key, 0) + 1
    bg_key = max(counts, key=counts.get)
    bg = (bg_key[0] * 24 + 12, bg_key[1] * 24 + 12, bg_key[2] * 24 + 12)

    def far(pixel: tuple[int, int, int]) -> bool:
        return sum((pixel[i] - bg[i]) ** 2 for i in range(3)) > 55 ** 2

    body = [pixel for pixel in pixels if far(pixel)]
    if len(body) < len(pixels) * 0.04:
        body = pixels

    channels = []
    for index in range(3):
        values = sorted(pixel[index] for pixel in body)
        channels.append(values[len(values) // 2])
    rgb = (channels[0], channels[1], channels[2])
    return "#{:02x}{:02x}{:02x}".format(*rgb), rgb


def _load_color_refs(ref_dir: Path, warnings: list[str]) -> list[ColorRef]:
    refs: list[ColorRef] = []
    if not ref_dir.is_dir():
        warnings.append(f"颜色参考目录不存在：{ref_dir}")
        return refs
    for path in sorted(ref_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        try:
            hex_value, rgb = _dominant_color(path)
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"无法解析参考图颜色 {path.name}: {exc}")
            hex_value, rgb = "#808080", (128, 128, 128)
        label = path.stem
        refs.append(
            ColorRef(
                label=label,
                path=path,
                hex=hex_value,
                rgb=rgb,
                name_zh=ZH_COLOR_NAMES.get(label.lower(), label),
            )
        )
    return refs


def load_skill(root: Path | str) -> Skill:
    root = Path(root).expanduser().resolve()
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"找不到 SKILL.md：{skill_md}")

    text = skill_md.read_text(encoding="utf-8")
    lines = text.splitlines()
    warnings: list[str] = []

    # --- type -> reference directory ---------------------------------------- #
    dir_by_slug: dict[str, list[str]] = {}
    for line in lines:
        match = TYPE_LINE.match(line.strip())
        if not match:
            continue
        target_dir = match.group("dir").strip().strip("/")
        aliases = [a.strip() for a in ALIAS_RE.findall(match.group("aliases")) if a.strip()]
        if not target_dir or not aliases:
            continue
        dir_by_slug.setdefault(target_dir, aliases)
    if not dir_by_slug:
        warnings.append("SKILL.md 中未解析到类型映射，使用内置默认映射")
        dir_by_slug = {slug: aliases for slug, aliases in FALLBACK_LABELS.items()}

    # --- garment phrase per label ------------------------------------------- #
    phrase_by_label: dict[str, str] = {}
    for line in lines:
        match = PHRASE_LINE.match(line.strip())
        if match:
            phrase_by_label[match.group("label").strip()] = match.group("phrase").strip()

    types: dict[str, GarmentType] = {}
    for slug, aliases in dir_by_slug.items():
        phrase = ""
        for alias in aliases:
            if alias in phrase_by_label:
                phrase = phrase_by_label[alias]
                break
        if not phrase:
            phrase = FALLBACK_PHRASES.get(slug, "")
            if phrase:
                warnings.append(f"类型 `{slug}` 未在 SKILL.md 找到版型描述，使用内置描述")
        ref_dir = root / "assets" / "color-references" / slug
        colors = _load_color_refs(ref_dir, warnings)
        if not colors:
            warnings.append(f"类型 `{slug}` 没有任何颜色参考图")
        types[slug] = GarmentType(
            slug=slug,
            aliases=aliases,
            ref_dir=ref_dir,
            phrase=phrase,
            colors=colors,
            cuff=infer_cuff(slug, aliases, phrase),
            sleeve=infer_sleeve(slug, aliases, phrase),
        )

    prompt_template = _blockquote_after(lines, "改款换色提示词")
    if not prompt_template:
        warnings.append("未能从 SKILL.md 解析改款换色提示词模板")
    negative_prompt = _blockquote_after(lines, "负面约束")
    if not negative_prompt:
        warnings.append("未能从 SKILL.md 解析负面提示词")

    max_bytes = 2_000_000
    match = MAX_BYTES_RE.search(text)
    if match:
        try:
            max_bytes = int(match.group(1).replace(",", ""))
        except ValueError:
            pass

    return Skill(
        root=root,
        types=types,
        prompt_template=prompt_template,
        negative_prompt=negative_prompt,
        max_bytes=max_bytes,
        warnings=warnings,
    )
