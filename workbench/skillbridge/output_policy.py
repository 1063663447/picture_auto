"""Small output-policy helpers; explicit operator edits are not model defaults.

No vision claims: these rules preserve evidence in attached originals and avoid
restoring a person the operator deliberately removed in an earlier approved pass.
"""
from __future__ import annotations
import re

_PERSON = r"(?:模特|人物|男人|女人|男士|女士|男性|女性|男生|女生|男模|女模|男的|女的|其中一[个人位]|另一个人)"
_PERSON_EDIT = re.compile(
    rf"(?:去掉|去除|删除|移除|拿掉|只保留|仅保留|保留一个|去掉其中).{{0,35}}{_PERSON}"
    rf"|{_PERSON}.{{0,12}}(?:去掉|去除|删除|移除|不要了)"
    r"|\b(?:remove|delete|erase)\b[^.!?\n]{0,70}\b(?:person|people|woman|man|female|male|model|wearer)\b"
    r"|\b(?:keep|retain)\s+only\b[^.!?\n]{0,70}\b(?:person|woman|man|female|male|model|wearer)\b",
    re.IGNORECASE,
)
_NEGATED_EDIT = re.compile(r"(?:不(?:要|许|能|再|得)?|禁止|别)\s*(?:去掉|去除|删除|移除|拿掉)[^，。；;\n]*|\b(?:do not|don't|never)\s+(?:remove|delete|erase)\b[^.!?\n]*", re.IGNORECASE)
_BACKGROUND_EDIT = re.compile(
    r"(?:更换|替换|改成|换成|改变|调整|修改|移除|去掉|重做)[^，,。；;\n]{0,16}(?:背景|场景|白底)"
    r"|(?:背景|场景|底色)[^，,。；;\n]{0,12}(?:更换|替换|改成|换成|变成|改为)"
    r"|\b(?:replace|change|remove)\b[^,.!?;\n]{0,30}\b(?:background|backdrop|scene)\b",
    re.IGNORECASE,
)


def edits_people(requirement: str) -> bool:
    return bool(_PERSON_EDIT.search(_NEGATED_EDIT.sub("", requirement or "")))


def person_selection(requirement: str) -> str:
    """Persist only the actual person-selection edit, not old color/background requests."""
    text = _NEGATED_EDIT.sub("", requirement or "")
    return "；".join(dict.fromkeys(match.group(0) for match in _PERSON_EDIT.finditer(text)))


def edits_background(requirement: str) -> bool:
    clauses = re.split(r"[，,。；;\n]", requirement or "")
    return any(_BACKGROUND_EDIT.search(clause) for clause in clauses
               if not re.search(r"(?:不要|不许|禁止|不能|别|不再).*(?:更换|替换|改变|调整|修改|去掉|移除|换成)|\b(?:do not|don't|never)\s+(?:change|replace|remove)\b", clause, re.IGNORECASE))


def apply_policy(prompt: str, negative: str, *, subject: str, requirement: str,
                 person_head: str, auto_head: str, composition: str, old_composition: str,
                 keep_subject: str) -> tuple[str, str]:
    """Apply after the default subject/composition rules, at the final API boundary."""
    if subject == "garment":
        return prompt, negative  # an explicit no-person output remains authoritative
    text = prompt
    neg = negative
    if edits_people(requirement):
        for fragment in (person_head, auto_head, composition, old_composition, keep_subject):
            text = text.replace(fragment, " ")
        # These are full known reference-role fragments, not arbitrary prose.
        text = re.sub(r"This original also controls wearer count,.*?must never erase or replace that person\.", " ", text)
        text = re.sub(r"This original also controls the actual wearer presence,.*?from this original\.", " ", text)
        text = re.sub(r"IMAGE 2 is also authoritative for the original wearer count,.*?Later related uploads do not add extra wearers\.", " ", text)
        text = re.sub(r"IMAGE 2 determines the ORIGINAL subject presentation,.*?Later related uploads do not add wearers or merge scenes\.", " ", text)
        replacements = {
            "Do not remove, erase, replace, duplicate or invent the original wearer;":
                "Do not alter or invent retained people;",
            "Do not retain a current/master omission when an attached original shows that person.":
                "Do not restore an explicitly removed person from an attached original.",
            "Do not change the original upload's wearer presence, worn/flat-lay presentation, pose or background.":
                "Do not alter the retained people's worn presentation or pose unless requested.",
            "Do not remove a covered, back-facing or cropped wearer because no skin is visible.":
                "Do not accidentally remove any retained covered, back-facing or cropped wearer.",
            "Keep the exact same person, face, hair, skin, hands, jewelry, watch, pose, body proportions, pants, shoes, background, bedding, wall, props, camera angle, framing, crop, lighting, depth of field, resolution, and image quality.":
                "Preserve each retained person's identity, all visible anatomy, clothes and accessories outside the edited garment, photographic detail and original framing except the explicit requested changes.",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
            neg = neg.replace(old, new)
        # Preserve views/artwork belonging to retained subjects, not a deleted carrier's garment.
        text = text.replace("Keep ALL original garment views and garments", "Keep all retained subjects' garment views and garments")
        text = text.replace("Recover missing original views", "Recover only retained subjects' missing original views")
        scoped = {
            "ALL original garments/views": "retained subjects' original garments/views",
            "ALL original garment views": "retained subjects' original garment views",
            "ALL views from the ORIGINAL UPLOAD": "all retained subjects' views from the ORIGINAL UPLOAD",
            "ALL original garments": "all retained subjects' garments",
            "ALL views already inside that canvas": "all retained subjects' views inside that canvas",
            "original garment/view count": "retained subjects' garment/view count",
            "original views": "retained subjects' views",
            "garment/view count": "retained garment/view count",
        }
        for old, new in scoped.items():
            text = text.replace(old, new)
            neg = neg.replace(old, new)
        neg += " Explicitly removed people and their garments are excluded from every view/count preservation constraint."
        header = (
            "TARGETED PERSON EDIT — HIGHEST-PRIORITY OPERATOR OUTPUT RULE: "
            f'"{person_selection(requirement)}". Remove ONLY the specified original person(s) and their belongings; '
            "never delete the head, neck, hair, hands, legs, trousers or feet of a retained person. "
            "Keep each retained person's original identity, pose, body proportions, entire visible "
            "anatomy and printed artwork. Naturally reconstruct only areas exposed by the requested "
            "removal. Do not reintroduce a deliberately removed person from any original, reference "
            "or earlier pass. An approved master already implementing this edit defines the final "
            "wearer count; attached originals supply retained identity, anatomy and artwork only. "
            "Keep original canvas/framing and retained front/back views unless explicitly changed. "
        )
        text = header + text
        neg = "Do not restore the person explicitly removed by the operator, remove a retained person's body parts, or transfer a removed person's artwork onto a retained person. " + neg
    if edits_background(requirement):
        # Only explicit change language unlocks background; '背景不动' does not.
        substitutions = {
            "the same backdrop": "the operator-requested backdrop",
            "background, bedding, wall, props": "unedited background regions and props except the operator-requested change",
            "background and lighting exactly as they are": "lighting and background except explicitly requested scene changes",
            "framing, background and lighting exactly as they are": "framing and lighting, with the operator-requested background change",
            "same camera, framing, crop, background, lighting, shadows and grain": "same camera, framing, crop, lighting and grain, with only the requested background change",
            "and nothing about it may move": "and retain all properties except explicitly requested edits",
            "the same background, props and bedding": "background and props except the explicit requested scene changes",
            "original background": "background except explicitly requested changes",
            "original subject or backdrop": "original subject or unedited scene regions",
            "original wearer or scene": "original wearer or unedited scene regions",
            "pose and background": "pose and unedited background regions",
            "pose and backdrop": "pose and unedited backdrop regions",
            "Do not change the original upload's wearer presence, worn/flat-lay presentation, pose or background.":
                "Preserve original wearer presence, presentation and pose; apply only the explicit background edit.",
            "white studio backdrop": "unrequested studio backdrop",
            "white product background": "unrequested product background",
        }
        for old, new in substitutions.items():
            text = text.replace(old, new)
            neg = neg.replace(old, new)
        text = ("EXPLICIT BACKGROUND EDIT — apply the operator's requested scene change only; "
                "all retained subject/anatomy/artwork locks still apply. " + text)
    return " ".join(text.split()), " ".join(neg.split())