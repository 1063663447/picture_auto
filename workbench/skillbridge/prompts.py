"""Prompt construction driven by the skill's own wording.

Three generation modes:

* ``single_pass``  – one API call per (image, color): change the garment type and
  recolor in a single prompt (the skill's 改款换色提示词).
* ``two_phase``   – one call per image to convert the garment type while keeping
  its original color, then one cheap recolor call per color. Costs one extra call
  per image but keeps the silhouette identical across all colorways.
* ``recolor_only`` – no type change at all: the garment type is already correct and
  only the fabric base color must change. This is the safest mode for products with
  printed / embroidered / appliqué artwork, because the model has the fewest degrees
  of freedom and therefore stops re-drawing the artwork.

Every mode carries a PRINT LOCK clause: the artwork is treated as an untouchable
asset, because image models routinely re-type, re-space, flatten or re-scale printed
logos when they are asked to "regenerate" a garment.
"""

from __future__ import annotations

from .skill import ColorRef, GarmentType, Skill

PRESERVE_CLAUSE = (
    "Keep the exact same person, face, hair, skin, hands, jewelry, watch, pose, body "
    "proportions, pants, shoes, background, bedding, wall, props, camera angle, framing, "
    "crop, lighting, depth of field, resolution, and image quality."
)

#: The single most important clause for e-commerce: never let the model re-create art.
PRINT_LOCK_CLAUSE = (
    "PRINT LOCK — treat the artwork already on the garment as an untouchable asset and "
    "reproduce it as an exact copy of the source: identical letterforms and typeface, "
    "identical stroke weights, identical letter widths, kerning, spacing and alignment, "
    "identical size, position and rotation on the garment, identical colour, and identical "
    "material relief (same raised 3D bevel depth, same gloss, same thin edge highlight, same "
    "drop shadow, same stitch or embroidery structure). Do not retype, re-letter, re-draw, "
    "regenerate, restyle, re-space, re-scale, straighten, warp, crop, smooth, sharpen or "
    "clean up any letter, word, number, logo, patch or stitch. The artwork must occupy the "
    "same bounding box relative to the garment as in the source image. If the source artwork "
    "is a flat print, keep it flat; if it is a raised or 3D appliqué, keep it raised and 3D "
    "with the same relief."
)

PRINT_CLAUSE = (
    "Preserve every visible original print, text, number, logo, embroidery, sticker, graphic, "
    "decorative artwork, and label; keep it readable, recognizable, and naturally positioned on "
    "the garment. Do not invent, redraw, remove, or replace any of it."
)

#: Kills the "clean vector sticker pasted on a photo" look: the artwork must keep the
#: source's physical material, photographic grain and contact shadow.
MATERIAL_CLAUSE = (
    "PHOTOGRAPHIC MATERIAL — this is a photograph of a real garment, so the artwork must keep "
    "the source's physical material and photographic look: the same surface finish (matte rubber, "
    "foam, embossed, embroidered or flocked, whichever the source shows), the same micro-texture "
    "and film grain inside the letters, the same slight edge softness, the same specular highlight "
    "shape and light direction, the same contact shadow and thickness where the artwork meets the "
    "fabric, and the garment's folds, wrinkles and lighting must continue across the artwork. "
    "The artwork must never look cleaner, sharper, glossier, flatter, more saturated or more "
    "'designed' than the photo around it: no vector-clean logo, no chrome or metallic gradient, "
    "no sticker or decal look, no synthetic 3D render, no uniform flat fill, no crisp digital "
    "edges, no pasted-on artwork."
)

STYLE_ONLY_TEMPLATE = (
    "Transform only the visible garment in this photo into a {phrase}. "
    "Change nothing but the garment's silhouette and construction: the shoulder line, armhole, "
    "neckline, cuffs, hem and folds must follow the person's pose and body shape, with no extra "
    "limbs, warped anatomy, or unnatural garment edges. "
    "Keep the garment's own original base colour exactly as it appears in the source photo; "
    "do not recolour the fabric. "
    + PRINT_CLAUSE
    + " "
    + PRESERVE_CLAUSE
    + " If an original print sits on a part that does not exist in the new garment type, keep "
    "only the part that remains visible and do not extend or redraw the artwork. "
    + PRINT_LOCK_CLAUSE
    + " "
    + MATERIAL_CLAUSE
)

RECOLOR_ONLY_TEMPLATE = (
    "This is a fabric colour replacement, not a redraw. Change ONLY the base colour of the "
    "existing garment fabric to {color_name} ({color_label}), approximately {color_hex} in sRGB. "
    "Keep the garment's silhouette, cut, seams, ribbing, cuffs, hem, neckline, folds, wrinkles, "
    "fabric texture and every existing pattern exactly as they are in the source image; only the "
    "fabric's base colour may change. "
    "Match the new colour's hue, brightness, saturation and warm/cool balance precisely, and let "
    "it follow the garment's existing highlights, shadows, folds and weave instead of becoming a "
    "flat colour block: keep visible fabric layering on dark and desaturated colours, and never "
    "spill colour onto skin, hair, hands, pants, shoes, jewelry, hardware or the background. "
    + PRINT_CLAUSE
    + " "
    + PRINT_LOCK_CLAUSE
    + " "
    + MATERIAL_CLAUSE
    + " "
    + PRESERVE_CLAUSE
)

COLOR_HINT_TEMPLATE = (
    " The target fabric base colour is {color_name} ({color_label}), approximately "
    "{color_hex} in sRGB; the attached colour reference image is authoritative for hue, "
    "brightness, saturation and temperature."
)

#: Extra negative constraints the workbench adds on top of the skill's own list,
#: all aimed at the most common failure: the model re-draws the printed artwork.
NEGATIVE_ART_LOCK = (
    "Do not substitute a different typeface or font, do not re-kern, re-space, stretch, squash, "
    "shear, rotate or re-align the lettering, do not change the number of letters or words, do "
    "not convert flat print into 3D relief or 3D relief into flat print, do not lose or flatten "
    "the bevel, gloss and edge highlight of raised appliqué, do not move, resize, crop or mirror "
    "the artwork, do not smooth or blur the artwork edges, do not add a new logo, tag or label, "
    "do not change an existing neck label, size tag or care label."
)

#: Realism negatives stay valid whatever the pattern policy is.
NEGATIVE_REALISM = (
    "Do not render the artwork as a clean vector logo or sticker, do not add chrome or metallic "
    "gradients, do not make a synthetic CGI render, do not flatten the letter fill, do not make "
    "the artwork edges crisper than the surrounding photo, do not remove the photographic grain, "
    "do not remove the contact shadow where the artwork meets the fabric."
)

NEGATIVE_EXTRA = NEGATIVE_ART_LOCK + " " + NEGATIVE_REALISM

#: 图案允许重画时的额外否定：防止"只是换了个颜色"骗过需求。
NEGATIVE_VARIANT = (
    NEGATIVE_REALISM
    + " Do not simply recolour or tint the original artwork, do not reuse the identical pattern in "
    "two colourways, do not paste the same graphic twice, do not leave the original pattern "
    "unchanged when a new design was requested."
)


# --------------------------------------------------------------------------
# 图案策略：用户说了算
#   preserve          图案逐像素保留（电商默认，最安全）
#   per_color_variant 每个颜色一套不同图案（用户要求时）
#   replace           整批换成指定新图案
#   free              图案可自由重绘（不做锁定）
# --------------------------------------------------------------------------
PATTERN_MODES = {
    "preserve": "图案逐像素保留（默认）",
    "per_color_variant": "每色一套不同图案",
    "replace": "换成新图案",
    "free": "图案可自由重绘",
}
PATTERN_MODE_KEYS = tuple(PATTERN_MODES)

PATTERN_VARIANT_CLAUSE = (
    "PATTERN VARIETY — this batch deliberately gives every colourway its OWN artwork. For the "
    "{color_name} version, design a coherent ORIGINAL pattern that follows the source's design "
    "language (same genre, similar stroke-weight family, similar visual density, same placement "
    "zone on the garment, same print technique and material), but change the motif, the lettering "
    "or the composition so no two colourways end up with the same artwork. The new artwork must be "
    "print-ready and photographic: it has to sit on the fabric with the source's relief, grain, "
    "slight edge softness, folds, wrinkles and lighting. Do not reuse, recolour or trace the "
    "original artwork for this colourway."
)

PATTERN_REPLACE_CLAUSE = (
    "PATTERN REPLACEMENT — replace the existing artwork with the new design described by the "
    "operator: {brief}. Keep the same placement zone, print size logic and print technique as the "
    "source (flat print stays flat, raised appliqué stays raised), and keep it photographic: "
    "same grain, edge softness, contact shadow, folds and lighting as the garment around it."
)

PATTERN_FREE_CLAUSE = (
    "PATTERN FLEXIBILITY — the artwork may be redesigned when the operator's request requires it; "
    "if it is not mentioned, keep the source artwork as close to identical as possible."
)

#: 需求分类关键词（中文为主，兼容英文）。顺序有意义：图案类先判定并从文本里摘掉，
#: 免得「每个颜色用不同图案」又被算成一次「改颜色」。
INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pattern_variant", ("每个颜色", "每个色", "一色一图案", "每色", "不同图案", "不同的图案",
                         "不同印花", "每款不同", "各自图案", "each color", "different pattern per")),
    ("pattern_replace", ("图案换成", "换成新的图案", "换图案", "改图案", "换成图案", "新图案",
                         "重新设计图案", "换印花", "改印花", "重画图案", "换一个图案",
                         "replace the pattern", "new artwork")),
    ("pattern_keep", ("图案不动", "图案别动", "图案原样", "保留图案", "不要动图案", "别改图案",
                      "原样不动", "保持原样", "不要改图案", "图案不要变", "keep the pattern",
                      "same artwork")),
    ("background", ("背景", "白底", "底色换成", "场景", "background")),
    ("light", ("亮度", "提亮", "亮一点", "暗一点", "曝光", "光线", "打光", "brightness", "exposure")),
    ("remove", ("去掉", "删除", "移除", "不要有", "拿掉", "remove")),
    ("add", ("加上", "增加", "新增", "添加", "加个", "add ")),
    ("size", ("放大", "缩小", "大一点", "小一点", "尺寸", "更大", "更小", "size")),
    ("spacing", ("间距", "字距", "排得", "紧凑", "spacing", "kerning")),
    ("color", ("颜色", "配色", "换色", "色调", "colour", "color")),
    ("text", ("文字", "文案", "字样", "字体", "改成「", "改字", "text", "wording")),
    ("resolution", ("高清", "清晰", "分辨率", "锐化", "upscale", "sharper")),
)

#: 用户提到这些类别时，对应的"不许变"约束要撤掉，否则自己打自己
INTENT_UNLOCKS: dict[str, tuple[str, ...]] = {
    "pattern_variant": ("artwork",),
    "pattern_replace": ("artwork",),
    "background": ("background",),
    "light": ("lighting",),
    "color": ("colour",),
    "text": ("artwork",),
    "resolution": ("resolution",),
    "size": ("artwork",),
    "spacing": ("artwork",),
}

KEEP_ITEMS: dict[str, str] = {
    "person": "the same person, face, hair, skin and hands",
    "pose": "the same pose and body proportions",
    "garment": "the same garment type, cut, seams, ribbing, cuffs, hem, collar, hood and pockets",
    "artwork": "the printed / embroidered artwork with the same letterforms, spacing, scale, "
               "position and material relief",
    "background": "the same background, props and bedding",
    "camera": "the same camera angle, framing and crop",
    "lighting": "the same light direction, contrast and shadow",
    "resolution": "the same photographic look, grain and resolution",
}


def color_hint(color: ColorRef) -> str:
    return COLOR_HINT_TEMPLATE.format(
        color_name=color.name_zh, color_label=color.label, color_hex=color.hex
    )


def operator_clause(extra: str | None) -> str:
    """兜底：没有识别出具体类别时，把原话作为权威要求附上。"""
    extra = " ".join((extra or "").split())
    if not extra:
        return ""
    return (
        "OPERATOR REQUEST (authoritative — honour it on top of the defaults above, and drop any "
        f"default lock that contradicts it): {extra}"
    )


def with_negative(prompt: str, negative: str | None, *, supported: bool) -> str:
    """Merge the negative constraints when the API has no negative field."""
    if supported or not negative:
        return prompt
    return (
        prompt
        + "\n\nStrict constraints (violating any of them makes the output unusable): "
        + " ".join(negative.split())
    )


def classify_requirement(text: str | None) -> list[str]:
    """把一句话需求归类，用来决定「哪些锁要撤掉」。

    每命中一类就把它匹配到的词从文本里摘掉，避免「每个颜色用不同图案」被重复算成
    「图案」+「颜色」两次，进而误撤掉颜色锁定。
    """
    remaining = (text or "").lower()
    if not remaining:
        return []
    hits: list[str] = []
    for key, words in INTENT_RULES:
        matched = [word for word in words if word.lower() in remaining]
        if not matched:
            continue
        hits.append(key)
        for word in matched:
            remaining = remaining.replace(word.lower(), " ")
    return hits


def detect_pattern_mode(text: str | None) -> str | None:
    """从需求里推断图案策略；没提就返回 None（沿用默认）。"""
    hits = classify_requirement(text)
    if "pattern_keep" in hits:
        return "preserve"
    if "pattern_variant" in hits:
        return "per_color_variant"
    if "pattern_replace" in hits:
        return "replace"
    return None


def pattern_clause(pattern_mode: str, color_name: str = "", brief: str = "") -> str:
    mode = (pattern_mode or "preserve").strip().lower()
    if mode == "per_color_variant":
        return PATTERN_VARIANT_CLAUSE.format(color_name=color_name or "this")
    if mode == "replace":
        return PATTERN_REPLACE_CLAUSE.format(brief=brief or "the new design described by the operator")
    if mode == "free":
        return PATTERN_FREE_CLAUSE
    return ""


def keep_clause(intents: list[str], *, pattern_mode: str = "preserve") -> str:
    """把"必须保持不变"的部分列成一句；用户要改的项自动从清单里去掉。"""
    unlocked: set[str] = set()
    for intent in intents:
        unlocked.update(INTENT_UNLOCKS.get(intent, ()))
    if (pattern_mode or "preserve") != "preserve":
        unlocked.add("artwork")
    if "pattern_replace" in intents or "pattern_variant" in intents:
        unlocked.add("artwork")
    items = [text for key, text in KEEP_ITEMS.items() if key not in unlocked]
    if not items:
        return ""
    return "KEEP UNCHANGED — " + "; ".join(items) + "."


def requirement_block(requirement: str, intents: list[str], pattern_mode: str) -> str:
    """把运营的原话变成"权威指令"，并写明它覆盖了哪些默认锁定。"""
    requirement = " ".join((requirement or "").split())
    if not requirement:
        return ""
    labels = {
        "pattern_variant": "图案：每个颜色用不同的图案（不要复用同一套图）",
        "pattern_replace": "图案：换成新的图案设计",
        "pattern_keep": "图案：保持原样",
        "background": "背景：按要求调整",
        "light": "光线/亮度：按要求调整",
        "remove": "移除指定的元素",
        "add": "添加指定的元素",
        "size": "尺寸/比例：按要求调整",
        "spacing": "字距/间距：按要求调整",
        "color": "颜色：按要求调整",
        "text": "文字/文案：按要求调整",
        "resolution": "清晰度：按要求调整",
    }
    lines = [
        "OPERATOR REQUEST (authoritative — it outranks the default locks of this skill):",
        f'"{requirement}"',
    ]
    read = [labels[key] for key in intents if key in labels]
    if read:
        lines.append("How to apply it: " + "; ".join(read) + ".")
    if (pattern_mode or "preserve") != "preserve":
        lines.append(
            "Because the request changes the printed artwork, the usual 'reproduce the artwork "
            "pixel for pixel' lock is LIFTED for this batch; every other lock below still applies."
        )
    return " ".join(lines)


def build_single_pass(
    skill: Skill,
    garment: GarmentType,
    color: ColorRef,
    extra: str | None = None,
    *,
    pattern_mode: str = "preserve",
    pattern_brief: str = "",
) -> str:
    mode = (pattern_mode or "preserve").strip().lower()
    if mode not in PATTERN_MODES:
        mode = detect_pattern_mode(extra) or "preserve"
    intents = classify_requirement(extra)

    template = skill.prompt_template or (
        "Transform only the visible garment into {phrase}. " + PRINT_CLAUSE + " " + PRESERVE_CLAUSE
    )
    if "[TARGET_GARMENT_TYPE]" in template:
        prompt = template.replace("[TARGET_GARMENT_TYPE]", garment.phrase)
    else:
        prompt = f"{template}\n\nTarget garment type: {garment.phrase}."
    prompt = " ".join(prompt.split())

    parts = [prompt, color_hint(color).strip()]
    requirement = requirement_block(extra or "", intents, mode)
    if requirement:
        parts.append(requirement)
    else:
        parts.append(operator_clause(extra) if extra else "")

    if mode == "preserve":
        parts.append(PRINT_LOCK_CLAUSE)
    else:
        # 用户要改图案：不再塞"逐像素保留"，改成正向的图案策略
        parts.append(pattern_clause(mode, color.name_zh, pattern_brief))
    parts.append(MATERIAL_CLAUSE)
    keep = keep_clause(intents, pattern_mode=mode)
    if keep:
        parts.append(keep)
    elif mode == "preserve":
        parts.append(PRESERVE_CLAUSE)
    return " ".join(part for part in parts if part).strip()


def build_style_only(garment: GarmentType) -> str:
    return STYLE_ONLY_TEMPLATE.format(phrase=garment.phrase)


def build_recolor_only(color: ColorRef, *, pattern_mode: str = "preserve") -> str:
    if (pattern_mode or "preserve") != "preserve":
        return (
            f"This is a colour + artwork task, not a redraw of the photo. Change the garment's base "
            f"fabric colour to {color.name_zh} ({color.label}), approximately {color.hex} in sRGB, "
            f"and give this colourway its own artwork. "
            + pattern_clause(pattern_mode, color.name_zh)
            + " "
            + MATERIAL_CLAUSE
            + " "
            + keep_clause([], pattern_mode=pattern_mode)
        )
    return RECOLOR_ONLY_TEMPLATE.format(
        color_name=color.name_zh, color_label=color.label, color_hex=color.hex
    )


def full_negative(skill: Skill, pattern_mode: str = "preserve") -> str:
    """Skill negative prompt + workbench negatives（图案允许重画时换一套否定词）。"""
    base = " ".join((skill.negative_prompt or "").split())
    mode = (pattern_mode or "preserve").strip().lower()
    extra = NEGATIVE_EXTRA if mode == "preserve" else NEGATIVE_VARIANT
    return (base + " " + extra).strip()


ARTWORK_LOCK_FRAGMENTS = (
    PRINT_LOCK_CLAUSE,
    PRINT_CLAUSE,
    "the printed / embroidered artwork with the same letterforms, spacing, scale, position and "
    "material relief; ",
    "fabric texture and every existing pattern exactly as they are in the source image",
)


def strip_artwork_lock(text: str) -> str:
    """用户要改图案时，把上一段拼进去的「图案必须保持原样」文字删掉，避免自相矛盾。"""
    out = text or ""
    for fragment in ARTWORK_LOCK_FRAGMENTS:
        if fragment and fragment in out:
            out = out.replace(fragment, " ")
    return " ".join(out.split())


def apply_requirement(
    base_prompt: str,
    requirement: str,
    *,
    pattern_mode: str = "",
    pattern_brief: str = "",
) -> str:
    """把一条需求整合进"已经拼好的提示词"（harness 预览/改图用）。

    与 `build_single_pass` 同一套逻辑：需求当权威指令写进去，冲突的锁被显式撤销，
    而不是在末尾贴一句原话。
    """
    requirement = " ".join((requirement or "").split())
    if not requirement:
        return base_prompt
    intents = classify_requirement(requirement)
    mode = (pattern_mode or "").strip().lower()
    if mode not in PATTERN_MODES:
        mode = detect_pattern_mode(requirement) or "preserve"
    prompt = base_prompt if mode == "preserve" else strip_artwork_lock(base_prompt)
    parts = [prompt, requirement_block(requirement, intents, mode)]
    if mode == "preserve":
        parts.append(
            "The request refines this batch; the artwork lock below still applies unless the "
            "request explicitly changes the artwork."
        )
        parts.append(PRINT_LOCK_CLAUSE)
    else:
        parts.append(pattern_clause(mode, brief=pattern_brief or requirement))
    parts.append(keep_clause(intents, pattern_mode=mode))
    return " ".join(part for part in parts if part).strip()


def preview(
    skill: Skill,
    garment: GarmentType,
    color: ColorRef | None,
    *,
    requirement: str = "",
    pattern_mode: str = "",
) -> dict:
    color = color or (garment.colors[0] if garment.colors else None)
    if color is None:
        raise KeyError(f"类型 {garment.slug} 没有颜色参考图")
    mode = (pattern_mode or "").strip().lower()
    if mode not in PATTERN_MODES:
        mode = detect_pattern_mode(requirement) or "preserve"
    return {
        "single_pass": build_single_pass(
            skill, garment, color, requirement, pattern_mode=mode
        ),
        "style_only": build_style_only(garment),
        "recolor_only": build_recolor_only(color, pattern_mode=mode),
        "negative": full_negative(skill, mode),
        "pattern_mode": mode,
        "pattern_modes": PATTERN_MODES,
        "color": color.as_dict(),
    }

