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
NEGATIVE_EXTRA = (
    "Do not substitute a different typeface or font, do not re-kern, re-space, stretch, squash, "
    "shear, rotate or re-align the lettering, do not change the number of letters or words, do "
    "not convert flat print into 3D relief or 3D relief into flat print, do not lose or flatten "
    "the bevel, gloss and edge highlight of raised appliqué, do not move, resize, crop or mirror "
    "the artwork, do not smooth or blur the artwork edges, do not add a new logo, tag or label, "
    "do not change an existing neck label, size tag or care label, do not render the artwork as a clean vector logo or sticker, do not add chrome or metallic gradients, do not make a synthetic CGI render, do not flatten the letter fill, do not make the artwork edges crisper than the surrounding photo, do not remove the photographic grain, do not remove the contact shadow where the artwork meets the fabric."
)


def color_hint(color: ColorRef) -> str:
    return COLOR_HINT_TEMPLATE.format(
        color_name=color.name_zh, color_label=color.label, color_hex=color.hex
    )


def operator_clause(extra: str | None) -> str:
    """操作员本次的特殊要求（来自开工前的需求沟通）。"""
    extra = " ".join((extra or "").split())
    if not extra:
        return ""
    return (
        " OPERATOR REQUIREMENTS for this batch — honour these on top of everything above, "
        f"without breaking any earlier constraint: {extra}"
    )


def build_single_pass(
    skill: Skill, garment: GarmentType, color: ColorRef, extra: str | None = None
) -> str:
    template = skill.prompt_template or (
        "Transform only the visible garment into {phrase}. " + PRINT_CLAUSE + " " + PRESERVE_CLAUSE
    )
    if "[TARGET_GARMENT_TYPE]" in template:
        prompt = template.replace("[TARGET_GARMENT_TYPE]", garment.phrase)
    else:
        prompt = f"{template}\n\nTarget garment type: {garment.phrase}."
    prompt = " ".join(prompt.split())
    return (
        prompt
        + color_hint(color)
        + " "
        + PRINT_LOCK_CLAUSE
        + " "
        + MATERIAL_CLAUSE
        + operator_clause(extra)
    )


def build_style_only(garment: GarmentType) -> str:
    return STYLE_ONLY_TEMPLATE.format(phrase=garment.phrase)


def build_recolor_only(color: ColorRef) -> str:
    return RECOLOR_ONLY_TEMPLATE.format(
        color_name=color.name_zh, color_label=color.label, color_hex=color.hex
    )


def full_negative(skill: Skill) -> str:
    """Skill negative prompt + workbench print-lock negatives."""
    base = " ".join((skill.negative_prompt or "").split())
    return (base + " " + NEGATIVE_EXTRA).strip()


def with_negative(prompt: str, negative: str | None, *, supported: bool) -> str:
    """Merge the negative constraints when the API has no negative field."""
    if supported or not negative:
        return prompt
    return (
        prompt
        + "\n\nStrict constraints (violating any of them makes the output unusable): "
        + " ".join(negative.split())
    )


def preview(skill: Skill, garment: GarmentType, color: ColorRef | None) -> dict:
    color = color or (garment.colors[0] if garment.colors else None)
    if color is None:
        raise KeyError(f"类型 {garment.slug} 没有颜色参考图")
    return {
        "single_pass": build_single_pass(skill, garment, color),
        "style_only": build_style_only(garment),
        "recolor_only": build_recolor_only(color),
        "negative": full_negative(skill),
        "color": color.as_dict(),
    }
