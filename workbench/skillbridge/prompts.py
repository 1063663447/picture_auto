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

import re

from .skill import ColorRef, GarmentType, Skill

PRESERVE_CLAUSE = (
    "Keep the exact same person, face, hair, skin, hands, jewelry, watch, pose, body "
    "proportions, pants, shoes, background, bedding, wall, props, camera angle, framing, "
    "crop, lighting, depth of field, resolution, and image quality."
)

PERSON_CLAUSE = (
    "KEEP ORIGINAL PERSON — HIGHEST-PRIORITY OUTPUT RULE (subject=person / 保留原人物): "
    "the customer's ORIGINAL UPLOAD alone defines the wearer count, identity, body, face, hair, "
    "skin, hands, pose and scene. Preserve every original person and all visible body parts in "
    "the same positions, proportions and framing. Never remove the original wearer, turn a worn "
    "garment into empty clothing, replace the wearer with a mannequin, or copy a reference model. "
    "Style/effect and color references control ONLY their permitted garment properties, NEVER "
    "wearer count, person, anatomy, pose or scene; an empty reference does not mean an empty output. "
    "If a current result, approved master or intermediate has lost or changed the original person, "
    "restore that same person from an attached original upload while retaining approved garment "
    "cut, target color, construction and intentional artwork changes. Original-person recovery "
    "overrides current/master pixel locks, inherited carrier-removal instructions and context. "
    "Apply the following only to real people already visible in the original upload: "
    + PRESERVE_CLAUSE + " "
    "Do not invent people, unseen faces or body parts absent from the original upload; when the "
    "original is empty or uncertain, preserve its actual visible subject without adding a model. "
    "Without an attached original, preserve available person evidence and never claim to recover "
    "an unseen identity. This subject rule outranks all reference roles and default/operator "
    "context below. Preserve the entire originally visible head, neck, hair, arms, hands, torso, "
    "trousers, legs and feet within the original framing: never keep only a floating garment "
    "or torso, hide the head under a hood, amputate limbs, erase trousers, zoom or crop out "
    "original body regions. A reference product shot never determines the canvas or crop."
)
_LEGACY_PERSON_CLAUSE = PERSON_CLAUSE.split("context below.", 1)[0] + "context below."

NEGATIVE_PERSON = (
    "Do not remove, erase, replace, duplicate or invent the original wearer; do not change their "
    "identity, face, hair, skin, hands, body proportions or pose. Do not turn a person into empty "
    "clothing or a mannequin. Do not copy a reference carrier or let an empty style/effect/color "
    "reference erase the original person. Do not retain a current/master omission when an "
    "attached original shows that person. Do not add people or body parts absent from the "
    "original upload."
)

#: **主体中立**约束：原图有模特就保持同一个模特，原图是平铺就保持平铺，且绝不新增
#: 原图没有的人和身体部位。这一条对两种原图都成立 —— 以前直接写 "keep the same person,
#: face, hair, skin, hands" 是在**邀请**模型给纯衣服平铺图补一个模特（实测出现过）。
_LEGACY_SUBJECT_CLAUSE = (
    "SUBJECT — the source image alone decides what the subject is, so do not change it. If the "
    "source shows the garment on a person, keep that same person: same face, hair, skin tone, "
    "hands, pose, body proportions, pants and shoes. If the source shows the garment on its own "
    "(flat lay, hanger, invisible or ghost mannequin), keep it exactly that way. Either way, never "
    "introduce anything the source does not already contain: no new person, no model, no face, no "
    "head, no hair, no neck, no hands, no arms, no legs, no feet, no skin, no body, no stylist, and "
    "no extra body part or limb."
)
SUBJECT_CLAUSE = (
    "SUBJECT — the source image alone decides what the subject is — HIGHEST-PRIORITY OUTPUT RULE: "
    "the customer's ORIGINAL UPLOAD controls actual wearer presence, presentation, pose, framing "
    "and background, never a style/effect/color reference or an imperfect generated master. "
    "Preserve the same visible subject and scene. A back-facing, hooded, fully covered, gloved, "
    "grayscale or cropped wearer is still a wearer even when NO skin, face or hands are visible. "
    "Keep that worn presentation, body silhouette, garment drape and original background; never "
    "extract it into empty clothing, a flat lay, ghost mannequin or a white product background. "
    "Keep covered or cropped anatomy covered or cropped; do not invent a face, head, skin or limbs. "
    "If the original contains only flat-lay or hanging clothes, keep those clothes empty with "
    "the same backdrop and no added person or body parts. If a current result or approved master "
    "lost the original wearer or scene, recover only the actually visible content from an "
    "attached original while keeping approved garment cut, construction, target color and "
    "intentional artwork changes. Without an attached original, preserve the visible edit target "
    "without inventing missing anatomy or claiming recovery. Empty reference garments cannot "
    "override the original subject or backdrop; this rule outranks current/master pixel locks."
)
NEGATIVE_SUBJECT = (
    "Do not change the original upload's wearer presence, worn/flat-lay presentation, pose or "
    "background. Do not remove a covered, back-facing or cropped wearer because no skin is visible. "
    "Do not replace an original worn scene with an empty garment, ghost mannequin, flat lay or "
    "white studio backdrop. Do not copy a reference carrier or backdrop. Do not add a person "
    "where the original shows empty clothes, or invent covered/cropped faces, skin or limbs."
)

#: garment 是输出要求，不是「原图已经没人」的假设；也适用于真人原图和有人的基准图。
NO_PERSON_CLAUSE = (
    "NO PERSON — HIGHEST-PRIORITY OUTPUT RULE (subject=garment / 只有衣服 禁止加人): "
    "the output must contain ONLY empty garments in ALL original garment views, regardless of "
    "who or what appears in ANY input image. This rule overrides source-subject, scene, pose, "
    "pixel-preservation and operator/context defaults below. If the source or approved master "
    "has a wearer, extract the "
    "garment and REMOVE the original wearer completely; do not preserve that person or mannequin. "
    "No human, model, mannequin, dress form, face, head, hair, neck, chest, torso, body, skin, "
    "arms, hands, fingers, legs or feet may remain anywhere, including reflections and shadows. "
    "Each garment view stays empty and hollow: the neckline shows backdrop or the dark inside "
    "of the collar, never a neck or a chest; sleeves and body contain no body or mannequin. Nothing "
    "wears, holds, lifts or touches it. Reconstruct garment areas occluded by the wearer as "
    "natural empty fabric following the target cut and construction, not a body-shaped fill; "
    "preserve visible artwork, material and garment-detail locks. Keep an already unoccupied "
    "flat-lay or hanger presentation; otherwise present all extracted garment views without a "
    "carrier and restore the exposed backdrop without human shadows or reflections. Preserve "
    "camera, lighting and nonhuman scene details only where compatible with complete carrier removal. "
    "REFERENCE CARRIERS ARE FORBIDDEN — IMAGE 2 humans and mannequins are forbidden carriers, "
    "not output subjects. Transfer ONLY the permitted garment cut, color and construction, "
    "never the carrier, anatomy, pose, face, body, skin, hands or legs. This exclusion applies "
    "to ALL references: source IMAGE 1, style/effect IMAGE 2, approved master and color references "
    "when those roles are present, and to original uploads in any image slot. "
    "A color-only reference supplies ONLY garment fabric color, never its cut or construction."
)

# Saved v9.1 prompts can still contain the previous, singular output rule.
_LEGACY_NO_PERSON_CLAUSE = (
    NO_PERSON_CLAUSE.replace(
        "ONLY empty garments in ALL original garment views", "ONLY the empty garment"
    ).replace("Each garment view stays empty and hollow", "The garment stays empty and hollow")
    .replace("present all extracted garment views without a carrier", "present the extracted garment alone")
    .replace(
        "color references when those roles are present, and to original uploads in any image slot. ",
        "color references. ",
    )
)

# Canvas composition is independent of garment cut/color and of the operator's pattern policy.
_LEGACY_COMPOSITION_CLAUSE = (
    "ORIGINAL UPLOAD COMPOSITION — preserve the ORIGINAL UPLOAD canvas, not a reference product "
    "photo's composition. Keep ALL original garment views and garments: the exact view count, "
    "front/back/side identities, relative scale, positions, orientation, spacing, overlap and "
    "relative arrangement in the same canvas and framing. A front-and-back upload must remain "
    "front AND back together; never collapse it into a single front-only garment, merge views, "
    "drop a garment, or crop out a side. If the original upload contains only one view, keep "
    "only that view; do NOT invent a back view, unseen side or extra garment. Preserve each "
    "side's own unique artwork and placement, never copy the front graphic onto the back or "
    "replace a missing side's design with a reference design. Explicit operator artwork changes "
    "and pattern variants still apply on their intended sides; this composition rule does not "
    "revoke them. A style/effect reference controls ONLY permitted garment cut, fabric color, "
    "cuffs and construction, NEVER the upload's layout, view count or missing artwork. When a "
    "current result, approved master or intermediate pass has lost an original view, restore "
    "that view from an attached original upload instead of preserving the omission; retain "
    "approved revised color/cut/details, not the original fabric color. No-person output rules "
    "remain higher priority than preserving carriers or human scene content."
)
COMPOSITION_CLAUSE = _LEGACY_COMPOSITION_CLAUSE.replace(
    "No-person output rules remain higher priority than preserving carriers or human scene content.",
    "The selected output-subject rule remains higher priority than composition and scene defaults.",
)

NEGATIVE_COMPOSITION = (
    "Do not collapse a multi-view or multi-garment original upload into one front-only garment; "
    "do not drop, merge, crop out, swap or rearrange original views, change the garment/view "
    "count, lose front/back identities, overlap or relative arrangement, or copy a style/effect "
    "reference's single-view layout. If the original has only one view, do not invent a back, "
    "unseen side or extra garment. Unless artwork changes were explicitly requested, do not "
    "omit each side's unique artwork, swap front/back designs or duplicate the front print on "
    "the back. Do not restore original fabric color over an approved revised color."
)

GARMENT_PRESERVE_CLAUSE = (
    "Preserve the garment details and photographic quality; preserve only nonhuman background, "
    "props, camera and lighting compatible with extracting an empty garment. Reconstruct the "
    "garment and exposed backdrop wherever a wearer or mannequin must be removed."
)

#: 成品复查发现"又画了个人"时，追加重画用的强化句。
NO_PERSON_REINFORCE = (
    " HARD RULE, THE PREVIOUS ATTEMPT FAILED THIS — the image must contain ONLY empty garments "
    "in ALL original garment views: absolutely no person, no face, no head, no hair, no hands, "
    "no arms, no legs, no skin and no body anywhere in the frame, not even in shadow or out of focus."
)

#: garment 输出的否定词不以原图/参考图已经无人为前提。
NEGATIVE_NO_PERSON = (
    "No person, no human being, no model, no woman, no man, no child, no face, no portrait, no "
    "selfie, no headshot, no head, no hair, no neck, no skin, no skin tone, no arms, no hands, no "
    "fingers, no legs, no feet, no body, no torso, no shoulder wearing the shirt, no one wearing "
    "the garment, no model posing, no stylist or assistant, no reflection or shadow of a person, "
    "no mannequin of any kind, no dress form, no filled-out garment silhouette, no body shape "
    "showing through the fabric. Do not retain the original wearer or copy any human or mannequin "
    "from IMAGE 1, IMAGE 2, a style/effect reference, an approved master or a color reference. "
    "Do not preserve body pixels, pose, skin, hands or legs from any input."
)


def preserve_clause(subject: str = "") -> str:
    """按主体给出"保持不变"的那一段。

    * ``garment``（判定为纯衣服）→ NO_PERSON_CLAUSE，明确禁止画人。
    * ``person``（真人上身）→ PERSON_CLAUSE，最高优先级锁住原人物。
    * 空/auto（不确定）→ SUBJECT_CLAUSE，中立表述，既不邀请也不擦除。
    """
    kind = str(subject or "").strip().lower()
    if kind == "garment":
        return NO_PERSON_CLAUSE
    if kind == "person":
        return PERSON_CLAUSE
    return SUBJECT_CLAUSE


def subject_clause(subject: str = "") -> str:
    """中性主体句：只有在主体未知时才需要额外强调（其余两种已含在 preserve_clause 里）。"""
    kind = str(subject or "").strip().lower()
    if kind in {"garment", "person"}:
        return ""
    return SUBJECT_CLAUSE

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
    "with the same relief. If the source garment is BLANK (no print, no text, no logo), the "
    "result stays completely BLANK — never invent a graphic, slogan, lettering, flag or badge "
    "that is not in the source."
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

# Ink support and exposed cloth are separate, even inside a graphic's bounds.
ARTWORK_TOPOLOGY_CLAUSE = (
    "PRINT INK VS EXPOSED FABRIC — preserve the original printed shapes and their negative "
    "spaces separately. The graphic's rectangular bounding box is NOT a solid decal or ink "
    "mask. Cloth visible between a printed character's legs, around shoes, between separate "
    "figures, inside open letter counters and around the graphic remains fabric: recolor "
    "those gaps to this task's target fabric colour with the same folds and lighting. Never "
    "turn the original dark cloth, antialiasing fringe, gap or transparent-looking area into "
    "new black ink, a black rectangle, an outline halo, a drop-shadow blob or a connecting "
    "bridge. Preserve genuine black ink exactly where it exists in the ORIGINAL UPLOAD, "
    "including boots, eyes, antlers, outlines and lettering; do NOT remove all dark pixels "
    "or recolor genuine black ink to the fabric colour. Printed characters' coloured trousers, "
    "separate legs, cuffs, shoes, small details and silhouette boundaries retain their original "
    "ink colours, shapes, spacing and separation unless explicitly edited by the operator. "
    "A dark gap between legs is NOT an extension of a boot, and a red printed trouser leg "
    "must not become a wider black leg. If an attached original shows that an approved master "
    "invented a black patch, widened a dark feature or filled exposed cloth, correct ONLY that "
    "artifact from the original ink boundaries; the master's pixel lock does not protect "
    "fabric/ink segmentation errors. Keep the approved garment cut, target fabric colour, "
    "retained people and scene. Without an attached original, preserve evidenced ink shapes "
    "without claiming unseen details were recovered. No global de-blackening or pasted artwork."
)
NEGATIVE_ARTWORK_TOPOLOGY = (
    "No new black matte, rectangular patch, black border, halo, shadow blob or merged bridge "
    "around or between printed details; no dark cloth trapped as ink inside the graphic. "
    "Do not merge separate printed legs/shoes, expand black boots into coloured trousers, "
    "erase negative-space gaps, or lose small ink features. Preserve original legitimate "
    "black ink; do not use a blanket remove-black rule or invent highlights to hide defects."
)


def enforce_artwork_topology(prompt: str, negative: str, pattern_mode: str = "preserve") -> tuple[str, str]:
    """Idempotent final-boundary rule; never freezes requested replacement/variant artwork."""
    body = " ".join((prompt or "").replace(ARTWORK_TOPOLOGY_CLAUSE, "").split())
    neg = " ".join((negative or "").replace(NEGATIVE_ARTWORK_TOPOLOGY, "").split())
    if (pattern_mode or "preserve").strip().lower() == "preserve":
        return body + " " + ARTWORK_TOPOLOGY_CLAUSE, neg + " " + NEGATIVE_ARTWORK_TOPOLOGY
    return body, neg


#: 袖口/下摆工艺 —— 图片模型最爱的错：把长袖 T 恤画成卫衣的罗纹袖口。
#: 平口（T 恤系）：袖口和下摆都是**同布平车折边**，没有罗纹/松紧/收口；
#: 螺纹（卫衣系）：袖口下摆必须是真实的 1x1 罗纹带。选错就是「不是这件衣服」。
#: 带效果图时的平口约束：只说"折边、没有罗纹"，**不提袖筒宽度** ——
#: 原来那句 "the sleeve stays at full width all the way to its edge" 会让紧身款的袖子画成宽袖。
CUFF_PLAIN_CLAUSE_REF = (
    "CUFF & HEM CONSTRUCTION — the sleeve openings and the bottom hem are PLAIN, FLAT, self-fabric "
    "turned-and-stitched edges, exactly like a cotton jersey T-shirt: a single narrow hem folded "
    "under, and a straight flat hem line on the body. There is NO rib-knit band, NO elastic band, "
    "NO gathered or shirred cuff, NO elasticated waistband and NO contrast trim on the sleeve or "
    "hem. The sleeve keeps whatever width the reference photo shows (it may taper on a fitted "
    "style); the only ribbed part is the neckband. If the source photo shows a sweatshirt-style "
    "rib-knit cuff or ribbed hem, STRIP that ribbing: rebuild the sleeve end and the hem as plain "
    "self-fabric turned-and-stitched hems, with no ribs, no gathering and no elastic, and let the "
    "sleeve run straight to that hem instead of cinching in. Keep the cuffs and hem plain even "
    "where the hip or sleeve edge is in shadow or partly covered."
)

CUFF_PLAIN_CLAUSE = (
    "CUFF & HEM CONSTRUCTION — the sleeve openings and the bottom hem are PLAIN, FLAT, "
    "self-fabric turned-and-stitched edges, exactly like a cotton jersey T-shirt: the sleeve stays "
    "at full width all the way to its edge and simply folds under with a single narrow hem, and the "
    "body ends in a straight flat hem line. There is NO rib-knit band, NO elastic band, NO gathered "
    "or shirred cuff, NO elasticated waistband, NO contrast trim on the sleeve or hem, and NO "
    "sweatshirt-style ribbing anywhere on this garment — the only ribbed part is the neckband. "
    "If the source photo shows a sweatshirt-style rib-knit cuff or ribbed hem, STRIP that ribbing: "
    "the new sleeve runs straight down to a plain folded hem and the body ends in a plain straight "
    "hem, with no ribs, no gathering and no elastic. Keep the cuffs and hem plain even where the "
    "wrist or hip is in shadow or partly covered."
)

CUFF_RIBBED_CLAUSE = (
    "CUFF & HEM CONSTRUCTION — this is a sweatshirt / fleece top, so the sleeve ends and the bottom "
    "hem are real rib-knit (1x1 rib) bands with clearly visible vertical ribs, slightly narrower "
    "than the sleeve and body, joined to the garment with a seam, exactly as on a sweatshirt. The "
    "neckline is a ribbed crew neckband. Do not replace them with plain raw folded edges."
)


def cuff_clause(garment: GarmentType, *, reference: bool = False) -> str:
    """按款式的袖口工艺给出构造约束；没有 cuff 信息时默认平口（T 恤更多、更怕画错）。

    ``reference=True``（带着效果图重塑版型）时不写"袖筒到边保持原宽度"，
    否则紧身款的袖型会被这句话拉宽。下装换成腰头/裤脚，背心换成袖笼/领口那一段。
    """
    kind = getattr(garment, "kind", "")
    sleeve = str(getattr(garment, "sleeve", "") or "").strip().lower()
    if kind == "bottom":
        return CUFF_HEM_BOTTOM_CLAUSE
    if kind == "vest" or sleeve == "none":
        return CUFF_HEM_VEST_CLAUSE
    cuff = str(getattr(garment, "cuff", "") or "").strip().lower()
    if cuff == "ribbed":
        return CUFF_RIBBED_CLAUSE
    return CUFF_PLAIN_CLAUSE_REF if reference else CUFF_PLAIN_CLAUSE


def cuff_negative(garment: GarmentType) -> str:
    """袖口相关的否定词（只加跟该款式匹配的那一组）。"""
    kind = getattr(garment, "kind", "")
    sleeve = str(getattr(garment, "sleeve", "") or "").strip().lower()
    if kind == "bottom":
        return (
            "Do not add ribbed leg cuffs, do not add gathered or elasticated leg openings, and do "
            "not change the shape of the leg hems this style has."
        )
    if kind == "vest" or sleeve == "none":
        return (
            "Do not add a rib-knit or elastic band to the armholes or the hem, do not gather or "
            "cinch the armhole edge, and do not add a contrast trim to the armhole or hem."
        )
    cuff = str(getattr(garment, "cuff", "") or "").strip().lower()
    if cuff == "ribbed":
        return (
            "Do not replace the ribbed cuffs or ribbed hem with plain folded raw edges, do not add an "
            "elastic or gathered cuff, do not turn the ribbed hem into a straight cut hem."
        )
    return (
        "Do not add ribbed cuffs, do not add a ribbed or elastic hem band, do not add gathered or "
        "elasticated sleeve openings, do not turn this T-shirt into a sweatshirt or hoodie, do not "
        "add sweatshirt fleece ribbing anywhere."
    )


#: 袖长 —— 上一轮 100 张实测里出现过「长袖款跑出短袖」，所以单独钉死
SLEEVE_LONG_CLAUSE = (
    "SLEEVE LENGTH — FULL-LENGTH LONG SLEEVES: each sleeve must run unbroken from the shoulder "
    "down to the wrist and cover the forearm completely. If the source photo shows a short-sleeve "
    "tee, lengthen the sleeves so they reach the wrist and end there. Do not leave short sleeves, "
    "do not roll or push the sleeves up, do not create three-quarter or elbow-length sleeves, and "
    "do not leave a bare forearm where the garment should be covering it."
)

SLEEVE_SHORT_CLAUSE = (
    "SLEEVE LENGTH — GENUINE SHORT SLEEVES: each sleeve must end above the elbow, mid-upper-arm, "
    "with a clean hem, exactly like a short-sleeve T-shirt. If the source photo shows long "
    "sleeves or a sweatshirt, shorten them. Do not keep long sleeves, do not leave three-quarter "
    "or wrist-length sleeves, do not push or roll the sleeves up, and do not cover the forearm."
)

#: 背心 / 挂脖 / 无袖：**没有袖子**，所以肩线、袖口那些句子一句都不能留（模型会照着补袖子）。
SLEEVE_NONE_CLAUSE = (
    "SLEEVE LENGTH — NO SLEEVES AT ALL: this garment is sleeveless. The armholes are cut open and "
    "finished with a narrow self-fabric binding or a turned-and-stitched edge, and the shoulder or "
    "neck straps run straight from the front panel to the back panel over the shoulders. There is "
    "no sleeve head, no cap sleeve, no short sleeve and no long sleeve anywhere. If the source "
    "photo shows a T-shirt, sweatshirt or any sleeved top, cut the sleeves off completely and "
    "rebuild the bodice with clean armholes and straps."
)

#: 下装（短裤）：整件衣服换成了裤子，袖长/袖口的句子全部不适用。
LEG_SHORTS_CLAUSE = (
    "GARMENT PART — SHORTS, NOT A TOP: the finished garment is a pair of shorts worn on the lower "
    "body. It has a waistband at the top, a crotch, and TWO separate legs, each with its own leg "
    "opening, ending above the knee. It has no sleeves, no neckline, no collar and no bodice. If "
    "the source photo shows a T-shirt, sweatshirt, hoodie or any top, remove that entire top and "
    "build the shorts in its place, keeping the printed artwork on the fabric where it still "
    "makes sense. Do not return a T-shirt, a dress, long trousers or a skirt."
)

#: 短裤的下摆/腰头工艺（平口那套「袖口」措辞对裤子没有意义）。
#: 注意：16 运动短裤本来就带白色撞色滚边，所以这里不能写成「没有撞色」这种绝对句。
CUFF_HEM_BOTTOM_CLAUSE = (
    "HEM & WAIST CONSTRUCTION — the top is a clean elasticated rib-knit waistband; each leg opening "
    "ends in the hem finish this style actually has (a plain turned-and-stitched hem, or the contrast "
    "piping the product shows). Keep the hem line clean and continuous around each leg, with no ribbed "
    "leg cuff and no gathered leg opening, and keep any trim exactly where the reference puts it."
)

#: 背心（无袖）没有袖口，只有袖笼和领口：别让模型去找"袖子的边缘"。
CUFF_HEM_VEST_CLAUSE = (
    "ARMHOLE & HEM CONSTRUCTION — the armholes are finished with a narrow self-fabric binding (or a "
    "turned-and-stitched edge where the photo shows one) that follows the armhole curve smoothly, and "
    "the neckline edge is finished the same way this style shows. The bottom hem is a plain straight "
    "self-fabric hem. There is NO sleeve opening to build, and no rib-knit band, elastic band or "
    "gathered edge anywhere on the armhole or the hem."
)


def sleeve_clause(garment: GarmentType) -> str:
    """按款式的袖长给出构造约束（背心/短裤单独一套，不能套上衣服的句子）。"""
    if getattr(garment, "kind", "") == "bottom":
        return LEG_SHORTS_CLAUSE
    sleeve = str(getattr(garment, "sleeve", "") or "").strip().lower()
    if sleeve == "none":
        return SLEEVE_NONE_CLAUSE
    if sleeve == "long":
        return SLEEVE_LONG_CLAUSE
    return SLEEVE_SHORT_CLAUSE


def sleeve_negative(garment: GarmentType) -> str:
    """袖长否定词（跟 cuff 那组分开写，模型更容易守）。"""
    if getattr(garment, "kind", "") == "bottom":
        return (
            "Do not turn the shorts into a T-shirt, a sweatshirt, a dress, long trousers or a "
            "skirt; do not add a neckline, sleeves or a bodice; do not merge the two legs into one."
        )
    sleeve = str(getattr(garment, "sleeve", "") or "").strip().lower()
    if sleeve == "none":
        return (
            "Do not add sleeves, cap sleeves, shoulder sleeves, short sleeves or long sleeves; do "
            "not turn this sleeveless top into a T-shirt or a sweatshirt, and keep the armholes and "
            "the straps exactly as this design has them."
        )
    if sleeve == "long":
        return (
            "Do not produce short sleeves, half sleeves, three-quarter sleeves, rolled-up sleeves "
            "or an exposed forearm; the sleeves must reach the wrists."
        )
    return (
        "Do not produce long sleeves, three-quarter sleeves, wrist-length sleeves or pushed-up "
        "sleeves; the sleeves must end above the elbow."
    )


STYLE_ONLY_TEMPLATE = (
    "Transform only the visible garment in this photo into a {phrase}. "
    "Change nothing but the garment's silhouette and construction: the shoulder line, armhole, "
    "neckline, cuffs, hem and folds must follow the source's own silhouette and drape — a worn "
    "garment follows that person's pose and body shape, a flat-lay follows its own folds and "
    "hanging shape — with no extra limbs, warped anatomy, or unnatural garment edges, and without "
    "adding a person or a body that the source does not show. "
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
    "Keep the garment's silhouette, cut, seams, cuffs, hem, neckline, folds, wrinkles, "
    "fabric texture and every existing pattern exactly as they are in the source image — "
    "including whether the cuffs and hem are plain folded edges or ribbed bands; do not add "
    "ribbing that is not there and do not remove ribbing that is. On a plain-hemmed T-shirt the "
    "cuffs and hem must stay plain. Only the fabric's base colour may change. "
    "Match the new colour's hue, brightness, saturation and warm/cool balance precisely, and let "
    "it follow the garment's existing highlights, shadows, folds and weave instead of becoming a "
    "flat colour block: keep visible fabric layering on dark and desaturated colours, and never "
    "spill colour onto the background, props or hardware — nor, when the garment is being worn, "
    "onto skin, hair, hands, pants, shoes or jewelry. "
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

#: **效果图**（款式表里每个颜色一张官方平铺产品图）随原图一起发送时的角色说明。
#: 有了这张图，版型/工艺/颜色都有权威依据，同款各色天然一致，不再需要「先出基准色」那套流程。
_LEGACY_EFFECT_CLAUSE = (
    "REFERENCE ROLES — two images are attached and they have different jobs. IMAGE 1 is the "
    "customer's own photo: it defines the scene (person or flat lay, background, props, camera "
    "angle, framing, lighting) and it owns the printed artwork — the lettering, numbers, logo, "
    "embroidery, patches and their placement are an untouchable asset that must be carried over "
    "onto the new garment exactly as they are. IMAGE 2 is the official product photo of the TARGET "
    "garment in the target colour, and it is the authority for the garment itself: silhouette, "
    "shoulder drop, body width, hem position, sleeve length and sleeve shape, sleeve opening, "
    "neckline and neckband, cuff and hem construction (plain folded edge vs rib-knit band), fabric "
    "weight, drape, seam lines and the exact fabric colour. Build the garment so that it matches "
    "IMAGE 2 on every one of those points, put IMAGE 1's artwork on it, and keep IMAGE 1's scene "
    "unchanged. Do not borrow anything else from IMAGE 2: no print, text, logo, patch or label of "
    "its own, no person, no mannequin, no background, no props, no watermark, and do not replace "
    "the customer's scene with IMAGE 2's white studio background. "
    # 下面是关键的一句：不写死"重塑"，模型就会把原图的版型原样搬过来（实测：
    # 原图是宽松大落肩、效果图是紧身弹力，结果出来还是宽松大落肩）
    "RESHAPE, DO NOT RECOLOUR — the garment in IMAGE 1 is REPLACED by the construction in IMAGE 2: "
    "its own fit, body width, shoulder line (drop vs set-in), sleeve length and sleeve shape, "
    "neckline shape, hem and drape must NOT be preserved or reused; adopt IMAGE 2's fit exactly, "
    "whether that is slim, body-hugging, regular or oversized. Keeping IMAGE 1's original silhouette "
    "and only changing its colour is a failure. What carries over from IMAGE 1 is only the scene "
    "and the artwork: the printed lettering, numbers, logo, patches and chest script move onto the "
    "rebuilt garment with the same relative placement, proportion and photographic relief."
)

EFFECT_CLAUSE = _LEGACY_EFFECT_CLAUSE.replace(
    "two images are attached and they have different jobs.",
    "the attached images have different jobs.",
) + (
    " IMAGE 2 controls ONLY garment cut, fabric color, cuffs and construction, NEVER the "
    "ORIGINAL UPLOAD canvas composition, number of garments/views, front/back arrangement, "
    "overlap or each side's artwork. Apply its construction to ALL original garment views; "
    "do not copy its single-view layout or use its design to fill missing artwork."
)


def has_effect(color: "ColorRef | None") -> bool:
    """这个颜色有没有效果图（款式表导入的官方产品图）。"""
    return getattr(color, "effect", None) is not None


#: 效果图上「这个款式的版型长什么样」的具体描述（来自 styles.json 的 fit 字段）。
#: 只写抽象一句「按效果图的版型」模型会把原图的版型照搬（实测），必须给具体形状词。
FIT_TEMPLATE = (
    " IMAGE 2 also shows this style's fit, and that is the one to build: {fit}."
)


def effect_clause(
    color: "ColorRef | None",
    override: bool | None = None,
    fit: str = "",
) -> str:
    """按是否带效果图给出角色说明；override 用于预览/测试强制开关。"""
    enabled = has_effect(color) if override is None else bool(override)
    if not enabled:
        return ""
    text = EFFECT_CLAUSE
    fit = " ".join(str(fit or "").split())
    if fit:
        text = text + FIT_TEMPLATE.format(fit=fit)
    return text


#: 同款第 2..N 个颜色：参考图换成「上一版成品（=基准）」+「该色效果图」。
#: 版型/印花/场景照基准，颜色照效果图 —— 同款各色既一致、颜色又准。
_LEGACY_MASTER_EFFECT_CLAUSE = (
    "REFERENCE ROLES — two images are attached. IMAGE 1 is the approved master shot of this exact "
    "style, produced from the customer's own photo: it carries the printed artwork, the scene, the "
    "camera, the lighting and the approved garment shape, and nothing about it may move. IMAGE 2 is "
    "the official product photo of this garment in the TARGET colour, and it is the authority for "
    "colour only: match its exact fabric base colour (hue, brightness, saturation, warm/cool, and "
    "how heathered or marled yarns read). Change only the fabric base colour and keep the master's "
    "silhouette, shoulder drop, body width, sleeve length and sleeve shape, neckline, cuffs, hem, "
    "seams, folds, wrinkles, print, print placement, camera, framing, background and lighting "
    "exactly as they are. Do not borrow the print, text, logo, person, background or props of "
    "IMAGE 2."
)

MASTER_EFFECT_CLAUSE = _LEGACY_MASTER_EFFECT_CLAUSE.replace(
    "two images are attached. ", "the attached images have different jobs. "
) + (
    " Keep ALL views from the ORIGINAL UPLOAD canvas, including their front/back identities, "
    "overlap, arrangement and each side's own artwork. IMAGE 2 is color-only, NEVER an authority "
    "for view count, layout, cut, cuffs or missing artwork. Restore a dropped original view "
    "from an attached original upload rather than copying a master omission."
)


#: 派生色改发「面料色卡」时的角色说明（色卡里没有任何衣服形状可抄）。
SWATCH_CLAUSE = (
    "REFERENCE ROLES — IMAGE 1 is the approved master shot of this exact style: it carries the scene, "
    "the person, the printed artwork, the camera, the lighting and the approved garment construction, "
    "and nothing about it may move. IMAGE 2 is only a small SWATCH of this colourway's fabric: match "
    "its fabric colour — hue, brightness, saturation, warm/cool balance, and how heathered or marled "
    "yarn reads — and nothing else. The swatch carries NO garment shape, so do not read any cut, "
    "silhouette, sleeve length, hood, collar, cuff or hem from it. Change only the fabric base colour; "
    "keep IMAGE 1's garment type, hood or lack of hood, neckline, sleeves, cuffs, hem, seams, folds, "
    "wrinkles, print, print placement, camera, framing, background and lighting exactly as they are."
)


def master_effect_clause(color: "ColorRef | None", override: bool | None = None) -> str:
    """基准色派生用（image 1 = 基准成品，image 2 = 该色效果图）。"""
    enabled = has_effect(color) if override is None else bool(override)
    return MASTER_EFFECT_CLAUSE if enabled else ""

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

#: **版型锁**：同款不同色必须长得一模一样。做法是「先出基准色，再以它为参考图只改颜色」，
#: 所以这段是给第 2..N 个颜色用的（理由与实测见 README §7 与 tools/check_shape_lock.py）。
SILHOUETTE_LOCK_CLAUSE = (
    "COLOUR PASS ON THE APPROVED MASTER — the attached image is the approved master shot of this "
    "exact style, and the goal is that every colourway looks like the SAME physical garment. Hold "
    "the master image pixel-identical and change ONLY the fabric base colour: same silhouette, same "
    "shoulder drop and sleeve length, same sleeve angle and opening, same body width and hem "
    "position, same neckline shape and ribbing, same seams, pockets and cuffs, same folds, wrinkles, "
    "drape, print placement and print scale, same camera, framing, crop, background, lighting, "
    "shadows and grain. Do not redraw, reshape, slim, widen, lengthen, shorten, straighten, "
    "re-crop or re-frame the garment, do not change how it is lying or how the sleeves hang, and do "
    "not move the print. If the requested colour would make you redraw any part of the shape, keep "
    "the master's shape exactly and only change the colour."
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
    # 改款意图：命中就撤掉「保持原服装类型/剪裁/袖口」的锁，按目标款式重建
    # 只放「款式/工艺」词：像「换成」这种太泛的词会把「把背景换成白色」也误判成改款
    ("garment_change", ("长袖", "短袖", "卫衣", "连帽", "帽衫", "圆领", "改款", "换款", "换款式",
                        "款式改", "罗纹", "螺纹", "平口", "袖口", "hoodie", "sweatshirt",
                        "long sleeve", "short sleeve", "long-sleeve", "short-sleeve",
                        "ribbed", "cuff", "crewneck")),
)

#: 用户提到这些类别时，对应的"不许变"约束要撤掉，否则自己打自己
INTENT_UNLOCKS: dict[str, tuple[str, ...]] = {
    "pattern_variant": ("artwork",),
    "pattern_replace": ("artwork",),
    "garment_change": ("garment",),
    "background": ("background",),
    "light": ("lighting",),
    "color": ("colour",),
    "text": ("artwork",),
    "resolution": ("resolution",),
    "size": ("artwork",),
    "spacing": ("artwork",),
}

KEEP_ITEMS: dict[str, str] = {
    # 主体统一成一条中性表述：以前写死 "the same person, face, hair, skin and hands"，
    # 对纯衣服平铺图等于要求模型「造一个人出来」。
    "subject": "the same subject exactly as in the source (a person stays that same person, with "
               "the same face, hair, skin and hands; a flat-lay or hanger shot stays a flat-lay "
               "with nothing and nobody added)",
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


#: **白色要干净的白**（9.6.33 客户：「很多款式的白色都不够白，有点像灰色」）。
#: 白色的色号是 #E6E6E8，但模型会顺着原图/场景的环境光把整件压成浅灰。
#: 这里把「白就是白」钉死：高光接近纯白、阴影中性偏冷，但不许整件发灰/发米/发蓝。
WHITE_CLAUSE = (
    " WHITE COLOURWAY — this garment is WHITE and must read as BRIGHT, CLEAN, TRUE WHITE: the lit "
    "surfaces sit at paper-white (very close to #FFFFFF), highlights stay white, and shadows are "
    "neutral (a touch cool at most). The white must not come out light grey, ash grey, milky "
    "off-white, cream, ivory, beige or bluish-grey, and must not inherit the dim ambient tint or "
    "the exposure falloff of the source photo. Do not darken the fabric to blend it into the scene; "
    "keep it clean white with crisp tonal separation from the background."
)


def is_white_colour(color: ColorRef | None) -> bool:
    """这个颜色算不算「白色」（按 label / 中文名 / 色号亮度综合判断）。"""
    if color is None:
        return False
    label = str(getattr(color, "label", "") or "").strip().lower()
    name = str(getattr(color, "name_zh", "") or "").strip()
    if label in {"white", "off-white"} or name in {"白色", "奶白色"}:
        return True
    digits = str(getattr(color, "hex", "") or "").strip().lstrip("#")
    if len(digits) == 6:
        try:
            r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return False
        return min(r, g, b) >= 205 and (max(r, g, b) - min(r, g, b)) <= 16
    return False


def white_clause(color: ColorRef | None) -> str:
    return WHITE_CLAUSE if is_white_colour(color) else ""


#: 版型描述自带冠词（"a long-sleeve t-shirt …"），模板里又写了 a/an →
#: "into a a long-sleeve …"。这种别扭句子会分散模型注意力，顺手压掉。
_DOUBLE_ARTICLE = re.compile(r"\b(a|an)\s+(a|an)\s+", re.IGNORECASE)


def tidy_articles(text: str) -> str:
    def _fix(match: "re.Match[str]") -> str:
        return "an " if match.group(2).lower() == "an" else "a "

    previous = None
    out = text or ""
    while previous != out:
        previous = out
        out = _DOUBLE_ARTICLE.sub(_fix, out)
    return " ".join(out.split())


def _garment_subject_text(text: str) -> str:
    """Remove known source/person locks, not garment/artwork locks, for garment output.

    Shared by builders and the runner boundary so saved/rework prompts receive the
    same rule. These replacements cover the skill and refine contracts; they are
    not a vision detector or an attempt to interpret arbitrary operator prose.
    """
    out = " ".join((text or "").split())
    for fragment in (PERSON_CLAUSE, _LEGACY_PERSON_CLAUSE, NEGATIVE_PERSON, NO_PERSON_CLAUSE, _LEGACY_NO_PERSON_CLAUSE,
                     SUBJECT_CLAUSE, _LEGACY_SUBJECT_CLAUSE, NEGATIVE_SUBJECT, KEEP_ITEMS["subject"]):
        out = out.replace(fragment, "")
    out = out.replace(PRESERVE_CLAUSE, GARMENT_PRESERVE_CLAUSE)
    replacements = {
        "Transform only the visible garment":
            "Extract all original garment views, remove any carrier, then transform each garment view",
        "Change nothing but the garment's silhouette and construction:":
            "After carrier removal, change only the garment's silhouette and construction:",
        "This is a fabric colour replacement, not a redraw.":
            "After carrier removal and empty-garment reconstruction, recolor without redesigning "
            "the garment.",
        "This is a colour + artwork task, not a redraw of the photo.":
            "After carrier removal, change garment color and artwork without redesigning the "
            "remaining nonhuman scene.",
        "Keep the image photographic and unchanged everywhere the request does not touch:":
            "After required carrier removal, keep the remaining garment and nonhuman scene "
            "photographic and unchanged except where the request applies:",
        "the source's own silhouette and drape — a worn garment follows that person's pose and "
        "body shape, a flat-lay follows its own folds and hanging shape":
            "the empty garment's silhouette and drape, with natural unsupported fabric folds",
        "the same subject the reference image shows — the same person if it shows a person, an "
        "empty garment if it shows the garment on its own, and never a person, body part or prop "
        "that the reference does not already contain;": "the empty garment after carrier removal;",
        "subject (the same person if the image shows one, an empty garment if it does not), "
        "pose or folds": "empty garment and its fabric folds after carrier removal",
        "it defines the scene (person or flat lay, background, props, camera angle, framing, "
        "lighting)": "it provides only compatible nonhuman scene details, camera and lighting",
        "keep IMAGE 1's scene unchanged": "keep only IMAGE 1's compatible nonhuman scene details",
        "only the scene and the artwork": "only compatible nonhuman scene details and the artwork",
        "and nothing about it may move": "with garment details fixed after complete carrier removal",
        "Hold the master image pixel-identical and change ONLY the fabric base colour":
            "After removing any carrier, hold the master's garment structure identical and change "
            "ONLY the fabric base colour",
        "Only the fabric's base colour may change.":
            "Apart from required carrier removal and empty-garment reconstruction, only the "
            "fabric's base colour may change.",
        "background and lighting exactly as they are":
            "compatible nonhuman background and lighting after carrier removal",
        "same background, props, camera angle, framing, crop, lighting, grain and resolution":
            "compatible nonhuman background, props, camera, lighting, grain and resolution "
            "after carrier removal",
        "same camera, framing, crop, background, lighting, shadows and grain":
            "same camera, framing, crop, lighting and grain, and only compatible nonhuman "
            "background and shadows after carrier removal",
        "background and framing exactly as they are":
            "compatible nonhuman background and framing after carrier removal",
        "background replacement, ": "unrequested changes to the remaining nonhuman background, ",
    }
    for old, new in replacements.items():
        out = out.replace(old, new)
    # The loaded SKILL.md has its own neutral source-subject paragraph and negatives.
    # Remove those complete clauses rather than leaving 'keep the same person' later.
    for pattern in (
        r"NO PERSON — the source photo shows the garment on its own,.*?image quality exactly "
        r"as they are\.",
        r"The source image alone decides what the subject is,.*?empty and unoccupied\.",
        r"Keep the same background, props, camera angle, framing, lighting, and image quality, "
        r"and never introduce .*?source does not already contain\.",
        r"Do not change the subject the source shows:.*?stays empty\.",
        r"Do not change furniture, bedding, wall, background, lighting direction, camera angle, "
        r"crop, pose, body shape, image composition, or non-garment pixels\.",
        r"Never add a person, model, face, head, hair, neck, hands, arms, legs, feet or skin "
        r"where the source shows only the garment\.",
        r"\b(?:Keep|Preserve) (?:the )?(?:exact )?same person[^.!?]*[.!?]",
        r"\bDo not change (?:the )?(?:pose|body shape|non-garment pixels)[^.!?]*[.!?]",
        r"This original also controls wearer count,.*?must never erase or replace that person\.",
        r"IMAGE 2 is also authoritative for the original wearer count,.*?Later related uploads "
        r"do not add extra wearers\.",
    ):
        out = re.sub(pattern, "", out, flags=re.IGNORECASE)
    return " ".join(out.split())


def _person_subject_text(text: str) -> str:
    """Remove known empty-current/garment contracts from saved person requests."""
    out = " ".join((text or "").split())
    for fragment in (PERSON_CLAUSE, _LEGACY_PERSON_CLAUSE, PRESERVE_CLAUSE, NO_PERSON_CLAUSE, _LEGACY_NO_PERSON_CLAUSE,
                     NO_PERSON_REINFORCE.strip(), NEGATIVE_NO_PERSON, NEGATIVE_PERSON,
                     GARMENT_PRESERVE_CLAUSE, SUBJECT_CLAUSE, _LEGACY_SUBJECT_CLAUSE,
                     NEGATIVE_SUBJECT, KEEP_ITEMS["subject"]):
        out = out.replace(fragment, " ")
    replacements = {
        "the same subject the reference image shows — the same person if it shows a person, an "
        "empty garment if it shows the garment on its own, and never a person, body part or prop "
        "that the reference does not already contain;": "the original upload's subject and pose;",
        "the same empty garment exactly as it is, with nobody in the frame — no person, no face, "
        "no head, no hair, no neck, no hands, no arms, no legs and no skin anywhere, and nothing "
        "holding or wearing it;": "the original upload's subject and pose;",
        "subject (the same person if the image shows one, an empty garment if it does not), "
        "pose or folds": "original-upload subject and pose, recovering any lost original wearer",
        "do not add a person or body part that the reference does not show":
            "do not add people or anatomy absent from the original upload",
        "all original empty garment views only, with any original wearer or mannequin removed":
            "all original garment views and their original wearers",
        "Extract all original garment views, remove any carrier, then transform each garment view":
            "Preserve all original wearers and transform each original garment view",
        "the empty garment's silhouette and drape, with natural unsupported fabric folds":
            "the approved garment silhouette and drape on the original subject",
        "empty garment and its fabric folds after carrier removal":
            "original wearer and approved garment folds",
        "remaining nonhuman background, props and bedding after carrier removal":
            "original background, props and bedding",
        "without a carrier": "with the original subject preserved",
        "empty garment after carrier removal": "garment with its original wearer restored",
        "After removing any carrier": "With the original wearer preserved or restored",
        "After carrier removal and empty-garment reconstruction": "With the original wearer preserved or restored",
        "Apart from required carrier removal and empty-garment reconstruction":
            "Apart from restoring the original wearer when lost",
        "After required carrier removal": "With the original wearer preserved or restored",
        "After carrier removal": "With the original wearer preserved or restored",
        "after complete carrier removal": "with the original wearer preserved or restored",
        "after carrier removal": "with the original wearer preserved or restored",
        "only compatible nonhuman scene details": "original scene details",
        "compatible nonhuman background": "original background",
        "remaining nonhuman scene": "original scene",
    }
    for old, new in replacements.items():
        out = out.replace(old, new)
    for pattern in (
        r"NO PERSON — the source photo shows the garment on its own,.*?image quality exactly as they are\.",
        r"The source image alone decides what the subject is,.*?empty and unoccupied\.",
        r"Do not change the subject the source shows:.*?stays empty\.",
        r"Never add a person, model, face, head, hair, neck, hands, arms, legs, feet or skin "
        r"where the source shows only the garment\.",
    ):
        out = re.sub(pattern, " ", out, flags=re.IGNORECASE)
    return " ".join(out.split())


def enforce_subject_rule(prompt: str, subject: str = "") -> str:
    """Enforce the selected output subject above all saved/reference contracts."""
    kind = str(subject or "").strip().lower()
    if kind == "garment":
        return tidy_articles(NO_PERSON_CLAUSE + " " + _garment_subject_text(prompt))
    if kind == "person":
        return tidy_articles(PERSON_CLAUSE + " " + _person_subject_text(prompt))
    return tidy_articles(SUBJECT_CLAUSE + " " + _person_subject_text(prompt))


def enforce_subject_negative(negative: str, subject: str = "") -> str:
    kind = str(subject or "").strip().lower()
    if kind == "person":
        return NEGATIVE_PERSON + " " + _person_subject_text(negative)
    if kind == "garment":
        return NEGATIVE_NO_PERSON + " " + _garment_subject_text(
            (negative or "").replace(NEGATIVE_NO_PERSON, "")
        )
    return NEGATIVE_SUBJECT + " " + _person_subject_text(negative)


def enforce_composition_rule(prompt: str) -> str:
    """Idempotent canvas lock, placed after the selected subject output rule."""
    out = " ".join((prompt or "").split())
    if NO_PERSON_CLAUSE in out or _LEGACY_NO_PERSON_CLAUSE in out:
        out = enforce_subject_rule(out, "garment")
    elif PERSON_CLAUSE in out:
        out = enforce_subject_rule(out, "person")
    for fragment in (COMPOSITION_CLAUSE, _LEGACY_COMPOSITION_CLAUSE):
        out = " ".join(out.replace(fragment, "").split())
    for head in (NO_PERSON_CLAUSE, PERSON_CLAUSE, SUBJECT_CLAUSE):
        if out.startswith(head):
            body = out[len(head):].strip()
            return " ".join(part for part in (head, COMPOSITION_CLAUSE, body) if part)
    return " ".join(part for part in (COMPOSITION_CLAUSE, out) if part)


#: **框选标注**：运营在基准图上画框标出「哪块要改」，并给每个框写了要求。
#: 标注图只是「改动区域的地图」——框线、编号、标注文字都不是衣服设计，输出里绝不能出现；
#: 改动只发生在框内，框外的一切（版型、印花、场景、光线）保持不动。
ANNOTATION_CLAUSE = (
    "OPERATOR ANNOTATION MAP — one of the attached images is a marked-up copy of the garment: it has "
    "drawn boxes with numbers on it, and each box marks a region the operator wants changed. Treat "
    "those boxes as a MAP of where to work, not as garment design: the box outlines, the numbers and "
    "any note text on that image must NEVER appear in the result, and no box shape may become a "
    "print, a border, a patch or a seam. Apply the requested change only inside the marked region; "
    "outside the boxes keep the silhouette, the print, the fabric, the scene, the camera and the "
    "lighting exactly as the unmarked copy shows. If a box sits on the neckline, cuffs, hem or sleeve "
    "opening, reshape that construction so it matches the note. The unmarked copy stays the "
    "authority for what the garment looks like everywhere else."
)


def annotation_clause(index: int = 2) -> str:
    """标注图是第几张（1 基）时给的那段约束。"""
    if not index:
        return ANNOTATION_CLAUSE
    return ANNOTATION_CLAUSE.replace("one of the attached images", f"IMAGE {index}")


def original_upload_reference_clause(image_index: int = 3, subject: str = "") -> str:
    """Role for an actually attached original after a master/intermediate + optional effect."""
    person = (
        "This original also controls wearer count, identity, body, face, hair, skin, hands, pose "
        "and scene. Restore its original person if IMAGE 1 lost them; garment references and "
        "current/master pixel locks must never erase or replace that person. "
    ) if str(subject).strip().lower() == "person" else (
        "This original also controls the actual wearer presence, worn or empty presentation, "
        "covered/back-facing body silhouette, pose and background. Recover that exact visible "
        "presentation if IMAGE 1 lost it; never invent cropped anatomy or add a person absent "
        "from this original. "
    ) if str(subject).strip().lower() != "garment" else ""
    return (
        f"ORIGINAL UPLOAD REFERENCE — IMAGE {int(image_index)} is the customer's ORIGINAL UPLOAD, "
        "not another style or color target. It is authoritative for ALL original garments/views, "
        "front/back identities, canvas layout, overlap, relative arrangement and each side's "
        "unique artwork, unless the operator explicitly changes that artwork. IMAGE 1 remains "
        "authoritative for the approved revised garment cut, construction and details; keep the "
        "requested target fabric color, using the color/effect reference when attached. Restore "
        "any view or side-specific artwork dropped by IMAGE 1 from this original upload, applying "
        "the approved revised cut/color to every recovered view. Do not copy the original fabric "
        "color back or undo intentional artwork changes/pattern variants. " + person
        + "This original-upload reference rule overrides inherited current/master composition locks."
    )


def rework_reference_clause(original_count: int, subject: str = "") -> str:
    """Describe exactly the original-upload references attached after the current edit target."""
    count = max(0, int(original_count or 0))
    roles = (
        "REWORK REFERENCE ROLES — IMAGE 1 is the current edit target, the accepted result to "
        "revise, NOT the original upload. It is authoritative for revised fabric color, garment "
        "cut, cuffs, construction and all already approved changes, except where this revision "
        "explicitly changes them. "
    )
    if count:
        originals = "IMAGE 2 is the original upload" if count == 1 else (
            f"IMAGE 2 through IMAGE {count + 1} are the original uploads"
        )
        roles += (
            f"{originals}, NOT style/effect references. These originals are authoritative for "
            "the original garment/view count, front/back identities, canvas layout, overlap, "
            "relative arrangement and each side's own unique artwork. Recover missing original "
            "views and their side-specific artwork from these uploads; never preserve a "
            "front-only omission or copy a front design onto a back. Apply IMAGE 1's revised "
            "color/cut/details to recovered views; do NOT copy original fabric color back or "
            "undo approved revisions. Explicit artwork changes and pattern variants still win "
            "on their intended sides; do not overwrite intentionally revised artwork with the "
            "old original design. Restoring dropped views overrides inherited current-result "
            "pixel/composition locks and the production brief. "
        )
        if count > 1:
            roles += (
                "IMAGE 2 is the original canvas corresponding to this edit task; restore ALL "
                "views already inside that canvas. Later original-upload images are related "
                "views supplied as context, not instructions to merge separate upload files. "
                "Do not combine independently uploaded front/back files into one new canvas "
                "unless the operator explicitly requests that combination. "
            )
    else:
        roles += (
            "IMAGE 1 is the only attached image; no original upload is attached. Preserve all "
            "views visible in this edit target. Without original-upload evidence, do not "
            "invent an unseen back, missing artwork or extra garment, or claim to have "
            "recovered a missing view from an unattached original. "
        )
    if str(subject).strip().lower() == "person":
        roles += (
            "IMAGE 2 is also authoritative for the original wearer count, identity, face, hair, "
            "body, skin, hands, pose and scene. Restore the same original person if IMAGE 1 lost "
            "them; keep approved garment changes. Later related uploads do not add extra wearers. "
        ) if count else (
            "Original-person identity evidence is unavailable here; preserve visible people "
            "without inventing an unseen wearer or claiming recovery from an unattached original. "
        )
    elif str(subject).strip().lower() != "garment":
        roles += (
            "IMAGE 2 determines the ORIGINAL subject presentation, covered/back-facing wearer, "
            "pose and backdrop; restore its actual visible content if IMAGE 1 became empty "
            "clothing or a white product scene. Preserve covered/cropped anatomy and keep an "
            "original flat lay empty. Later related uploads do not add wearers or merge scenes. "
        ) if count else (
            "No original subject evidence is attached: preserve IMAGE 1's visible presentation "
            "without inventing a missing wearer or claiming unseen anatomy was recovered. "
        )
    return roles + "These rework reference roles override any inherited production reference roles."


def _strip_production_reference_roles(prompt: str) -> str:
    """Remove only known role contracts, including v9.1 and garment-normalized saved prompts."""
    out = " ".join((prompt or "").split())
    for fragment in (
        EFFECT_CLAUSE, _LEGACY_EFFECT_CLAUSE, MASTER_EFFECT_CLAUSE,
        _LEGACY_MASTER_EFFECT_CLAUSE, SILHOUETTE_LOCK_CLAUSE,
    ):
        for version in (fragment, _garment_subject_text(fragment), _person_subject_text(fragment),
                        _person_subject_text(_garment_subject_text(fragment))):
            out = out.replace(version, " ")
    for pattern in (
        r"REWORK REFERENCE ROLES — .*?These rework reference roles override any inherited "
        r"production reference roles\.",
        r"ORIGINAL UPLOAD REFERENCE — .*?This original-upload reference rule overrides inherited "
        r"current/master composition locks\.",
        r"IMAGE 2 also shows this style's fit, and that is the one to build: .*?\.",
        r"The target fabric base colour is .*? in sRGB; the attached colour reference image is "
        r"authoritative for hue, brightness, saturation and temperature\.",
    ):
        out = re.sub(pattern, " ", out)
    return " ".join(out.split())


def build_rework_reference_prompt(
    prompt: str, subject: str = "", original_count: int = 1
) -> str:
    """Rebind inherited generation roles to current + actually attached originals; idempotent."""
    body = _strip_production_reference_roles(prompt)
    return enforce_composition_rule(enforce_subject_rule(
        rework_reference_clause(original_count, subject) + " " + body, subject
    ))


def operator_clause(extra: str | None) -> str:
    """兜底：没有识别出具体类别时，把原话作为权威要求附上。"""
    extra = " ".join((extra or "").split())
    if not extra:
        return ""
    return (
        "OPERATOR REQUEST (authoritative — honour it on top of the defaults above, and drop any "
        f"default lock that contradicts it): {extra}"
    )


def apply_output_policy(prompt: str, negative: str, subject: str, requirement: str) -> tuple[str, str]:
    from .output_policy import apply_policy
    if not requirement:
        return prompt, negative
    return apply_policy(prompt, negative, subject=subject, requirement=requirement,
                        person_head=PERSON_CLAUSE, auto_head=SUBJECT_CLAUSE,
                        composition=COMPOSITION_CLAUSE, old_composition=_LEGACY_COMPOSITION_CLAUSE,
                        keep_subject=KEEP_ITEMS["subject"])


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


def keep_clause(
    intents: list[str],
    *,
    pattern_mode: str = "preserve",
    subject: str = "",
    effect: bool = False,
    garment_change: bool = False,
) -> str:
    """把"必须保持不变"的部分列成一句；用户要改的项自动从清单里去掉。

    ``effect=True``（带着效果图生成）时**不再锁原图的版型**：衣服的版型要以效果图为准，
    再写「保持同样的 cut/seams/cuffs」就是自相矛盾 —— 实测模型会因此把原图的宽松大落肩原样搬过去。
    """
    unlocked: set[str] = set()
    for intent in intents:
        unlocked.update(INTENT_UNLOCKS.get(intent, ()))
    if (pattern_mode or "preserve") != "preserve":
        unlocked.add("artwork")
    if "pattern_replace" in intents or "pattern_variant" in intents:
        unlocked.add("artwork")
    if effect or garment_change:
        # effect：版型以效果图为准 → 不再锁原图的剪裁
        # garment_change：运营明确要换款式 → 同理，否则「保持罗纹袖口」会和需求对打
        unlocked.add("garment")
    garment_output = str(subject or "").strip().lower() == "garment"
    if garment_output:
        unlocked.add("subject")
    items = [text for key, text in KEEP_ITEMS.items() if key not in unlocked]
    if garment_output:
        items = [
            "remaining nonhuman background, props and bedding after carrier removal"
            if text == KEEP_ITEMS["background"] else text
            for text in items
        ]
        items.append("all original empty garment views only, with any original wearer or mannequin removed")
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
    subject: str = "",
    effect: bool | None = None,
) -> str:
    mode = (pattern_mode or "preserve").strip().lower()
    if mode not in PATTERN_MODES:
        mode = detect_pattern_mode(extra) or "preserve"
    intents = classify_requirement(extra)

    template = skill.prompt_template or (
        "Transform only the visible garment into {phrase}. " + PRINT_CLAUSE + " "
        + preserve_clause(subject)
    )
    if "[TARGET_GARMENT_TYPE]" in template:
        prompt = template.replace("[TARGET_GARMENT_TYPE]", garment.phrase)
    else:
        prompt = f"{template}\n\nTarget garment type: {garment.phrase}."
    prompt = " ".join(prompt.split())

    want_effect = has_effect(color) if effect is None else bool(effect)
    parts = [
        style_positive(garment, reference=want_effect),
        prompt,
        color_hint(color).strip(),
        white_clause(color),
    ]
    effect_text = effect_clause(color, effect, getattr(garment, "fit", ""))
    if effect_text:
        parts.append(effect_text)
    requirement = requirement_block(extra or "", intents, mode)
    if requirement:
        parts.append(requirement)
    else:
        parts.append(operator_clause(extra) if extra else "")

    if mode == "preserve":
        parts.append(PRINT_LOCK_CLAUSE)
        parts.append(ARTWORK_TOPOLOGY_CLAUSE)
    else:
        # 用户要改图案：不再塞"逐像素保留"，改成正向的图案策略
        parts.append(pattern_clause(mode, color.name_zh, pattern_brief))
    parts.append(MATERIAL_CLAUSE)
    keep = keep_clause(intents, pattern_mode=mode, subject=subject, effect=bool(effect_text))
    if keep:
        parts.append(keep)
    # 主体约束始终补一次：skill 模板已改成中性表述，这里按任务主体的判定结果钉死
    parts.append(subject_clause(subject))
    kind = str(subject or "").strip().lower()
    if kind == "garment":
        parts.append(NO_PERSON_CLAUSE)
    elif kind == "person":
        # 真人上身：锁住同一个人（skill 模板不再写死这句，必须显式补）
        parts.append(PRESERVE_CLAUSE)
    elif mode == "preserve" and not keep:
        parts.append(preserve_clause(subject))
    return enforce_composition_rule(enforce_subject_rule(
        tidy_articles(" ".join(part for part in parts if part).strip()), subject
    ))


def build_style_only(garment: GarmentType, *, subject: str = "", effect: bool = False) -> str:
    base = STYLE_ONLY_TEMPLATE.replace(PRESERVE_CLAUSE, preserve_clause(subject))
    return enforce_composition_rule(enforce_subject_rule(tidy_articles(
        style_positive(garment, reference=effect)
        + " "
        + base.format(phrase=garment.phrase)
        + (" " + EFFECT_CLAUSE if effect else "")
    ), subject))


def build_recolor_only(
    color: ColorRef,
    *,
    pattern_mode: str = "preserve",
    garment: GarmentType | None = None,
    subject: str = "",
    effect: bool | None = None,
) -> str:
    want_effect = has_effect(color) if effect is None else bool(effect)
    if (pattern_mode or "preserve") != "preserve":
        base = (
            f"This is a colour + artwork task, not a redraw of the photo. Change the garment's base "
            f"fabric colour to {color.name_zh} ({color.label}), approximately {color.hex} in sRGB, "
            f"and give this colourway its own artwork. "
            + pattern_clause(pattern_mode, color.name_zh)
            + " "
            + MATERIAL_CLAUSE
            + " "
            + keep_clause([], pattern_mode=pattern_mode, subject=subject)
        )
    else:
        base = RECOLOR_ONLY_TEMPLATE.format(
            color_name=color.name_zh, color_label=color.label, color_hex=color.hex
        )
    if garment is not None:
        # 换色不该改工艺，但模型会"顺手"补罗纹/改袖长/把帽绳画串：把这几条再钉一次
        base = base + " " + style_positive(garment, reference=want_effect)
    if white_clause(color):
        # 白色单独钉一次「白就是白」（9.6.33：客户反馈浅色被画成灰色）
        base = base + white_clause(color)
    effect_text = effect_clause(color, effect, getattr(garment, "fit", "") if garment else "")
    if effect_text:
        base = base + " " + effect_text
    if (pattern_mode or "preserve").strip().lower() == "preserve":
        base += " " + ARTWORK_TOPOLOGY_CLAUSE
    base = base.replace(PRESERVE_CLAUSE, preserve_clause(subject))
    if str(subject or "").strip().lower() == "person" and PRESERVE_CLAUSE not in base:
        base = base + " " + PRESERVE_CLAUSE
    return enforce_composition_rule(enforce_subject_rule(tidy_articles(base), subject))


#: **款式锁**：把「这件衣服到底是什么款、哪些部件不许出现」显式写出来。
#: 起因（2026-11-09，飞书改款记录 1382~1390）：3 款式 × 3 颜色的单子里，
#: 基准色是对的，但**派生色**（跟随基准只改颜色）的提示词里既没有版型描述、也没有 no hood，
#: 模型自己发挥，把长袖 T 恤/索罗娜短袖画成了**带帽兜抽绳的卫衣**（9 张里错 4 张）。
#: 所以「只改颜色」必须同时说清楚「这是哪件衣服、不许加帽兜/抽绳/口袋/罗纹」。
GARMENT_LOCK_TEMPLATE = (
    "GARMENT TYPE LOCK — the finished garment must be {phrase}. Keep it that exact garment type in "
    "every pixel: {forbidden}. The printed artwork, its placement and the scene stay as instructed, "
    "but the garment itself must never drift into another style."
)

#: 明显属于「不该出现」的部件（按款式特征生成，不写死在某一个款上）
_HOOD_FREE_HINT = ("no hood", "not a hoodie", "no drawstring", "no kangaroo pocket")


def garment_forbidden(garment: GarmentType) -> str:
    """按款式给出「不许出现」的部件清单。卫衣类才允许帽兜/抽绳/罗纹。

    背心（无袖）和短裤（下装）单独一套：对它们说「no long sleeves / no rib-knit cuffs」
    这类上衣措辞等于没说到点上，真正要禁的是「给它加上袖子」「把它画成上衣」。
    """
    phrase = str(getattr(garment, "phrase", "") or "").lower()
    slug = str(getattr(garment, "slug", "") or "").lower()
    kind = str(getattr(garment, "kind", "") or "").strip().lower()
    is_hood = "hoodie" in phrase or "hoodie" in slug or "hood" in phrase.replace("no hood", "")
    cuff = str(getattr(garment, "cuff", "") or "").strip().lower()
    sleeve = str(getattr(garment, "sleeve", "") or "").strip().lower()
    items: list[str] = []
    if kind == "bottom":
        # 短裤的「不该出现」跟上衣完全不同：不许加上衣的部件，也不许变成一条长裤/裙子；
        # 抽绳、松紧腰这些下装本来就有的东西**不能**列进来（15/16 都带抽绳）
        return ", ".join(
            [
                "no sleeves",
                "no neckline or collar",
                "no bodice with a waist seam",
                "no single skirt-like tube",
                "no long trousers reaching the ankle",
            ]
        )
    if not is_hood:
        items += [
            "no hood",
            "no drawstrings",
            "no kangaroo pocket",
            "no sweatshirt or fleece body",
        ]
    if kind == "vest" or sleeve == "none":
        return ", ".join(items + ["no sleeves", "no cap sleeves", "no shoulder sleeves"])
    if cuff != "ribbed":
        items += ["no rib-knit cuffs", "no rib-knit hem", "no elastic gathered cuff or hem"]
    if sleeve == "short":
        items += ["no long sleeves", "no three-quarter sleeves"]
    elif sleeve == "long":
        items += ["no short sleeves", "no rolled-up sleeves"]
    return ", ".join(items) if items else "no change to the garment type"


#: **帽绳规格**（只对连帽款注入，放在提示词靠前位置）。
#: 客户原话「连帽卫衣的带子容易串」= **颜色串色**：黑卫衣生成出白帽绳。
#: 9.6.26 又补了一条更硬的规格：绳子整根跟衣身同色，绳头是**银色金属头**（客户给的对照片）——
#: 之前只写「同色系」，模型会把源图那根绳的材质/毛边/乱色一起搬过来，两根还互不一致。
DRAWSTRING_CLAUSE = (
    "HOOD DRAWSTRING — fixed design, identical on every colourway. (1) COLOUR: the whole length of "
    "both cords is the SAME colour as THIS hoodie's fabric — white hoodie gets white cords, black "
    "gets black, charcoal gets charcoal, grey gets grey, navy gets navy, sage gets sage; exactly that "
    "colour, not a tone lighter or darker. The cords are made from the garment's own fabric. "
    "(2) TIPS: each cord ends in a SHORT SILVER METAL AGLET — a small polished silver metal end cap, "
    "exactly one per cord, both tips silver and the same size. (3) TWINS: the two cords are identical "
    "twins — same colour, same thickness, same length, same silver tip — hanging symmetrically out of "
    "the two eyelets and starting at the same height. The cords in the source photo belong to the OLD "
    "colourway of that photo: replace their colour with this colourway's fabric colour, never copy "
    "it. Never one cord lighter than the other; never a frayed, tasselled, knotted or tufted end; "
    "never stone, marble, burlap or thick-rope texture."
)


def is_hooded(garment: GarmentType | None) -> bool:
    """这个款式有没有帽兜（决定要不要注入帽绳约束）。"""
    if garment is None:
        return False
    phrase = str(getattr(garment, "phrase", "") or "").lower().replace("no hood", "")
    slug = str(getattr(garment, "slug", "") or "").lower()
    return "hoodie" in slug or "hoodie" in phrase or "hood" in phrase


def drawstring_clause(garment: GarmentType | None) -> str:
    return DRAWSTRING_CLAUSE if is_hooded(garment) else ""


#: 帽绳的负面清单（9.6.26：绳身跟衣身同色、绳头银色的反面写清楚，单列成常量方便复用/校验）
DRAWSTRING_NEGATIVE = (
    "Do not colour the cord body anything other than this garment's own fabric colour; do not "
    "keep the source photo's cord colour (no white cords on a dark hoodie, no charcoal cords on a "
    "light hoodie); do not borrow another colourway's cord colour; do not make the two cords "
    "differ in colour, thickness, length or tip; do not draw frayed, tasselled, knotted or tufted "
    "cord ends; do not texture the cords like stone, marble, burlap or thick rope; do not use "
    "gold, brass, black or gunmetal tips — the tips are plain polished SILVER metal; do not make "
    "the whole cord metallic (only the two bottom tips are silver); do not remove the cords, the "
    "eyelets or the hood."
)


def drawstring_negative(garment: GarmentType | None) -> str:
    if not is_hooded(garment):
        return ""
    return DRAWSTRING_NEGATIVE


#: ---------------------------------------------------------------------------
#: 款式级提示词：每个款式一套专属默认（library/prompts/<slug>.json）
#:   `garment.style_positive` / `garment.style_negative` 有值就用它，
#:   没有就回落到「按属性拼装」（convert + 款式锁 + 袖长 + 袖口 + 帽绳）。
#: ---------------------------------------------------------------------------
#: 款式「记住的规则」：审核/对齐时确认过的要求（见 style_memory），生成时追加在款式核心后面。
#: 放在这里而不是写进 json：json 是「款式结构」，lessons 是「运营一次次教的习惯」，分开更清楚。
LESSONS_TEMPLATE = (
    " STYLE RULES LEARNED FROM PREVIOUS APPROVED BATCHES (this exact style — they held then and "
    "still hold now unless the operator overrides them today): {items}"
)

#: **款式级硬规则**：审核拍板后被判定为「这个款式的通病」（款式跑偏的真正原因），
#: 位置放在款式核心后面（靠前，模型更守），措辞比普通规则更硬。
PROMOTED_RULES_TEMPLATE = (
    " SKILL-LEVEL RULES FOR THIS STYLE (locked in after operator review of earlier batches; they "
    "are the reason those batches drifted, so treat them as part of the style's own construction "
    "and never trade them away for a nicer-looking result): {items}"
)


def _rule_block(clauses: list, template: str, limit: int) -> str:
    items: list[str] = []
    for raw in clauses or []:
        clause = " ".join(str(raw or "").split())
        if clause and clause not in items:
            items.append(clause)
        if len(items) >= max(1, limit):
            break
    if not items:
        return ""
    joined = "; ".join(f"{index}) {text.rstrip('.')}" for index, text in enumerate(items, start=1))
    return template.format(items=joined + ".")


def promoted_clause(garment: GarmentType | None, limit: int = 4) -> str:
    """款式级硬规则（审核拍板后升级的，位置靠前）。"""
    if garment is None:
        return ""
    return _rule_block(list(getattr(garment, "style_promoted", []) or []), PROMOTED_RULES_TEMPLATE, limit)


def lessons_clause(garment: GarmentType | None, limit: int = 6) -> str:
    """其余款式级规则（学到的经验，放在硬规则后面）。"""
    if garment is None:
        return ""
    return _rule_block(list(getattr(garment, "style_lessons", []) or []), LESSONS_TEMPLATE, limit)


def style_positive(garment: GarmentType | None, *, reference: bool = True) -> str:
    """这个款式的「正向核心段」：怎么拆掉源图特征、怎么建目标款式。"""
    if garment is None:
        return ""
    core = " ".join(str(getattr(garment, "style_positive", "") or "").split())
    if not core:
        core = " ".join(
            part for part in (
                convert_clause(garment),
                garment_lock(garment),
                sleeve_clause(garment),
                cuff_clause(garment, reference=reference),
                drawstring_clause(garment),
            ) if part
        )
    return (core + " " + promoted_clause(garment) + " " + lessons_clause(garment)).strip()


def style_negative(garment: GarmentType | None) -> str:
    """这个款式专属的否定词（没有专属文件时按属性拼）。"""
    if garment is None:
        return ""
    core = " ".join(str(getattr(garment, "style_negative", "") or "").split())
    if core:
        return core
    return " ".join(
        part for part in (
            sleeve_negative(garment),
            cuff_negative(garment),
            garment_negative(garment),
            drawstring_negative(garment),
        ) if part
    )


def garment_lock(garment: GarmentType | None) -> str:
    """款式锁整段；没有款式信息就返回空。"""
    if garment is None:
        return ""
    phrase = " ".join(str(getattr(garment, "phrase", "") or "").split()) or garment.slug
    return GARMENT_LOCK_TEMPLATE.format(phrase=phrase, forbidden=garment_forbidden(garment))


#: **款式转换段**（放在提示词最前面）。
#: 起因（2026-11-10）：客户原图是「圣诞卫衣 promo 图」，要求改成 25 长袖童装，
#: 结果只换了颜色、袖口还是卫衣的罗纹收口。实测：只写「保持别动 + 目标款式」时模型会照搬源图结构；
#: 把「源图那件衣服要按规则**拆掉特征**」写成第一段，长袖/平口才真的出现。
CONVERT_TEMPLATE = (
    "CONVERT THE GARMENT TYPE (highest priority) — whatever garment the wearer has on in the source, "
    "the result must be exactly this garment: {phrase}. Rebuild it inside the scene instead of "
    "recolouring what is there: {strip}. The garment's fabric must {fabric_link} rather than the "
    "source, and none of these may appear on it: {forbidden}. Only the printed artwork, the wearer, "
    "the scene and any text overlays are preserved."
)


CONVERT_MARK = "CONVERT THE GARMENT TYPE"
CONVERT_END = "any text overlays are preserved."


def promote_convert_clause(prompt: str) -> str:
    """把「款式转换段」提回提示词最前面。

    请求边界会再 prepend 一遍主体/构图规则（那是最高优先级的另外两块），
    会把转换段顶到几千字符之后 —— 实测这样模型就不听了，所以这里再抢一次位置。
    """
    text = prompt or ""
    index = text.find(CONVERT_MARK)
    if index <= 0:
        return text
    end = text.find(CONVERT_END, index)
    if end == -1:
        return text
    end += len(CONVERT_END)
    block = text[index:end]
    rest = (text[:index] + " " + text[end:]).strip()
    # 纯衣服批次的「禁人物」规则要保住第一位（那是这类的最高优先级），转换段紧随其后
    if rest.startswith(NO_PERSON_CLAUSE):
        tail = rest[len(NO_PERSON_CLAUSE):].strip()
        return (NO_PERSON_CLAUSE + " " + block + " " + tail).strip()
    return (block + " " + rest).strip()


def convert_clause(garment: GarmentType | None, *, reference_image: bool = True) -> str:
    """把「源图这件衣服该怎么拆、该怎么建」写成第一段（按目标款式的袖口工艺给不同规则）。

    ``reference_image=False``：改图只有一张参考图时不要提「IMAGE 2」，改成直接描述面料。
    """
    if garment is None:
        return ""
    phrase = " ".join(str(getattr(garment, "phrase", "") or "").split()) or garment.slug
    cuff = str(getattr(garment, "cuff", "") or "").strip().lower()
    kind = str(getattr(garment, "kind", "") or "").strip().lower()
    if kind == "bottom":
        strip = (
            "if the source shows a t-shirt, sweatshirt, hoodie, dress or any other top, remove that "
            "top completely and build the shorts in its place: a waistband at the waist, a crotch and "
            "two separate legs, each with its own opening, ending above the knee"
        )
        fabric = "lightweight knit with a clean elasticated waistband and the leg hems this style has"
    elif kind == "vest" or str(getattr(garment, "sleeve", "") or "").strip().lower() == "none":
        strip = (
            "if the source shows a T-shirt, sweatshirt or any sleeved garment, cut the sleeves off "
            "completely: rebuild the bodice sleeveless with clean armholes finished by a narrow "
            "self-fabric binding, and keep only the straps this design has (shoulder straps or a "
            "halter strap that fastens behind the neck)"
        )
        fabric = "soft stretch jersey with plain bound armholes and a plain hem"
    elif cuff == "ribbed":
        strip = (
            "if the source shows a plain folded cuff or a straight plain hem, replace them with real "
            "1x1 rib-knit cuffs and a rib-knit hem band, slightly narrower than the sleeve and body"
        )
        fabric = "mid-weight brushed fleece with rib-knit trims"
    else:
        strip = (
            "if the source shows a rib-knit cuff, a gathered or elastic cuff, a band that cinches the "
            "wrist or a ribbed waistband, remove all of that and finish the sleeves and the bottom hem "
            "as plain self-fabric folded hems of the sleeve's own width; the sleeve must run straight "
            "to that hem"
        )
        fabric = "thin cotton jersey with plain folded hems and a ribbed neckband only"
    fabric_link = f"match IMAGE 2 ({fabric})" if reference_image else f"be {fabric}"
    text = CONVERT_TEMPLATE.format(
        phrase=phrase, strip=strip, fabric_link=fabric_link, forbidden=garment_forbidden(garment)
    )
    sleeve = str(getattr(garment, "sleeve", "") or "").strip().lower()
    if kind == "bottom":
        text += (
            " The result has two separate legs and one waistband, and no sleeves or neckline at all."
        )
    elif kind == "vest" or sleeve == "none":
        text += " The result is sleeveless, with clean bound armholes and no sleeve heads."
    elif sleeve == "long":
        text += " The sleeves must be full length, reaching the wrist."
    elif sleeve == "short":
        text += " The sleeves must be genuine short sleeves ending above the elbow."
    return text


def garment_negative(garment: GarmentType | None) -> str:
    """否定词版：把同样的部件清单写成 Do not ... 形式。"""
    if garment is None:
        return ""
    forbidden = garment_forbidden(garment)
    if not forbidden or forbidden == "no change to the garment type":
        return ""
    return "Do not add " + forbidden.replace("no ", "") + "."


def build_master_recolor(
    color: ColorRef,
    *,
    garment: GarmentType | None = None,
    subject: str = "",
    effect: bool | None = None,
    swatch: bool = False,
) -> str:
    """同款第 2..N 色的提示词：基准成品 + 该色效果图 → 只改颜色。

    有作用果图（默认）时走 `MASTER_EFFECT_CLAUSE`（版型看基准、颜色看效果图）；
    没有效果图时退回 `SILHOUETTE_LOCK_CLAUSE`（只锁版型，颜色靠色值）。
    """
    want_effect = has_effect(color) if effect is None else bool(effect)
    base = RECOLOR_ONLY_TEMPLATE.replace(PRESERVE_CLAUSE, preserve_clause(subject)).format(
        color_name=color.name_zh, color_label=color.label, color_hex=color.hex
    )
    if garment is not None:
        base = base + " " + style_positive(garment, reference=want_effect)
    if white_clause(color):
        # 派生色也钉一次：浅色最容易被环境光压成灰（9.6.33）
        base = base + white_clause(color)
    if swatch:
        # 派生色改发面料色卡：形状只能看基准，色卡里没有任何衣服形状
        roles = SWATCH_CLAUSE
    else:
        roles = master_effect_clause(color, effect)
        if not roles:
            roles = SILHOUETTE_LOCK_CLAUSE
    base = base + " " + roles
    base += " " + ARTWORK_TOPOLOGY_CLAUSE
    return enforce_composition_rule(enforce_subject_rule(tidy_articles(base), subject))


def full_negative(
    skill: Skill,
    pattern_mode: str = "preserve",
    garment: GarmentType | None = None,
    subject: str = "",
    effect: bool | None = None,
) -> str:
    """Skill negative prompt + workbench negatives（图案允许重画时换一套否定词）。

    传了 garment 就再按它的袖口工艺追加一组否定（平口/螺纹互相排斥的那几条）；
    主体判定为「只有衣服」时再追加一组禁人的否定词。
    """
    base = " ".join((skill.negative_prompt or "").split())
    mode = (pattern_mode or "preserve").strip().lower()
    extra = (NEGATIVE_EXTRA if mode == "preserve" else NEGATIVE_VARIANT) + " " + NEGATIVE_COMPOSITION
    if mode == "preserve":
        extra += " " + NEGATIVE_ARTWORK_TOPOLOGY
    if garment is not None:
        style_neg = style_negative(garment)
        if style_neg:
            extra = extra + " " + style_neg
    return enforce_subject_negative((base + " " + extra).strip(), subject)


ARTWORK_LOCK_FRAGMENTS = (
    PRINT_LOCK_CLAUSE,
    ARTWORK_TOPOLOGY_CLAUSE,
    NEGATIVE_ARTWORK_TOPOLOGY,
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
    subject: str = "",
) -> str:
    """把一条需求整合进"已经拼好的提示词"（harness 预览/改图用）。

    与 `build_single_pass` 同一套逻辑：需求当权威指令写进去，冲突的锁被显式撤销，
    而不是在末尾贴一句原话。
    """
    requirement = " ".join((requirement or "").split())
    if not requirement:
        return enforce_composition_rule(enforce_subject_rule(base_prompt, subject))
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
        if ARTWORK_TOPOLOGY_CLAUSE not in prompt:
            parts.append(ARTWORK_TOPOLOGY_CLAUSE)
    else:
        parts.append(pattern_clause(mode, brief=pattern_brief or requirement))
    parts.append(keep_clause(intents, pattern_mode=mode, subject=subject))
    parts.append(subject_clause(subject))
    return enforce_composition_rule(enforce_subject_rule(
        " ".join(part for part in parts if part).strip(), subject
    ))


def preview(
    skill: Skill,
    garment: GarmentType,
    color: ColorRef | None,
    *,
    requirement: str = "",
    pattern_mode: str = "",
    subject: str = "",
) -> dict:
    color = color or (garment.colors[0] if garment.colors else None)
    if color is None:
        raise KeyError(f"类型 {garment.slug} 没有颜色参考图")
    mode = (pattern_mode or "").strip().lower()
    if mode not in PATTERN_MODES:
        mode = detect_pattern_mode(requirement) or "preserve"
    return {
        "single_pass": build_single_pass(
            skill, garment, color, requirement, pattern_mode=mode, subject=subject
        ),
        "style_only": build_style_only(garment, subject=subject),
        "recolor_only": build_recolor_only(
            color, pattern_mode=mode, garment=garment, subject=subject
        ),
        "negative": full_negative(skill, mode, garment, subject),
        "pattern_mode": mode,
        "pattern_modes": PATTERN_MODES,
        "subject": subject or "auto",
        "cuff": garment.cuff,
        "cuff_zh": garment.cuff_zh,
        "color": color.as_dict(),
    }

