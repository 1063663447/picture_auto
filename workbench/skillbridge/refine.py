"""Rework ("改图") prompt composition + the batch-level requirement rewrite.

两层用途：

1. **改图**（rework）：拿一张已认可的成品当参考图，只应用运营这次的一条改动。
2. **需求整合**（intake）：开工前把运营那句话"翻译成约束并合并进提示词"，
   而不是简单地在末尾追加一句「OPERATOR REQUIREMENTS: 原话」。

设计原则（2026-11 调整）：**用户要求优先于 skill 的默认硬规则**。
当用户明确要改图案（例如"每个颜色一套不同图案"）时，默认的「图案逐像素保留」
会被显式撤销，改成正向的图案策略；其余未提及的约束继续锁死。
"""

from __future__ import annotations

import json

import httpx

from . import prompts

REWORK_TEMPLATE = (
    "REVISION REQUEST — the attached image is the current accepted result and must be adjusted, "
    "not regenerated from scratch. {change} {keep} Keep the image photographic and unchanged "
    "everywhere the request does not touch: {subject_keep} same background, props, camera angle, "
    "framing, crop, lighting, grain and resolution. Do not beautify, upscale, denoise or re-render "
    "the photo. Original production brief for context only — do not re-execute what is already "
    "correct: {original_prompt}"
)

#: 主体那一段：以前写死 "same person, face, hair, skin, hands"，纯衣服平铺图会被带出人。
SUBJECT_KEEP_NEUTRAL = (
    "the same subject the reference image shows — the same person if it shows a person, an empty "
    "garment if it shows the garment on its own, and never a person, body part or prop that the "
    "reference does not already contain;"
)
SUBJECT_KEEP_GARMENT = (
    "the same empty garment exactly as it is, with nobody in the frame — no person, no face, no "
    "head, no hair, no neck, no hands, no arms, no legs and no skin anywhere, and nothing holding "
    "or wearing it;"
)

#: 换模型改图时追加：明确「这张图是参考图/上一步成品」，别让新模型整张重画
SWITCH_MODEL_CLAUSE = (
    " CROSS-MODEL HANDOFF — the attached image was rendered by a different model and is the "
    "reference to edit: reproduce its garment, printed artwork registration and scale, fabric "
    "colour, subject (the same person if the image shows one, an empty garment if it does not), "
    "pose or folds, background and framing exactly as they are, then apply only the requested "
    "revision. Do not reinterpret the photo, do not re-invent the artwork, do not change the crop "
    "or colour balance, and do not add a person or body part that the reference does not show."
)

LLM_SYSTEM = (
    "You are a prompt engineer for an image-editing model used by an apparel e-commerce team. "
    "Rewrite the operator's revision request into ONE precise English image-editing prompt.\n"
    "Rules:\n"
    "1. The operator's instruction is AUTHORITATIVE. If it conflicts with the default 'keep the "
    "printed artwork pixel-identical' rule (e.g. they want a new or different pattern), the "
    "instruction wins — allow the artwork to be redesigned and say so explicitly.\n"
    "2. Everything the instruction does NOT mention must stay identical: garment type/cut/seams, "
    "fabric colour, the subject (the same person if the reference shows one; an empty garment with "
    "nobody added if it does not), pose or folds, background, camera angle, framing, lighting, "
    "grain, resolution.\n"
    "3. Never describe the whole photo again, never add unrequested changes, no lists, no markdown "
    "— output only the prompt text."
)


def _intent_sentences(intents: list[str], pattern_mode: str) -> list[str]:
    mapping = {
        "pattern_variant": "give this colourway its own new artwork instead of reusing the source one",
        "pattern_replace": "replace the existing artwork with the new design the operator describes",
        "pattern_keep": "keep the existing artwork exactly as it is",
        "background": "change the background only as described",
        "light": "adjust brightness / exposure only as described",
        "remove": "remove only the element the operator names",
        "add": "add only the element the operator names",
        "size": "change only the size / scale the operator describes",
        "spacing": "change only the letter spacing / kerning described",
        "color": "change only the colour the operator describes",
        "text": "change only the wording / typeface the operator describes",
        "resolution": "improve sharpness only; do not redraw content",
    }
    lines = [mapping[key] for key in intents if key in mapping]
    if (pattern_mode or "preserve") != "preserve" and not any(
        key in intents for key in ("pattern_variant", "pattern_replace")
    ):
        lines.append("the artwork may be redesigned where the request requires it")
    return lines


def _sanitize_brief(text: str) -> str:
    """把「原始生产提示词」里的图片角色描述清掉。

    改图只附 1 张参考图，如果照抄上一轮的「CONVERT / REFERENCE ROLES — IMAGE 2 是官方效果图…」，
    模型会以为还挂着第二张图，反而更乱。这里：
    1. 去掉款式转换段（从标记到句末）；
    2. 把 IMAGE 1/2 的说法改成「源图/参考图」。
    """
    out = " ".join(str(text or "").split())
    marker = "CONVERT THE GARMENT TYPE"
    end_marker = "any text overlays are preserved."
    while marker in out:
        start = out.find(marker)
        end = out.find(end_marker, start)
        if end == -1:
            out = out[:start].strip()
            break
        out = (out[:start] + " " + out[end + len(end_marker):]).strip()
    out = out.replace("IMAGE 2", "the reference photo").replace("IMAGE 1", "the source photo")
    # 上一轮如果是「只有衣服」批次，brief 里会带着「把人物整个去掉」的段落 ——
    # 抄进真人改图里就自相矛盾（"保留同一个人" vs "REMOVE the original wearer"），必须删掉
    for clause in (prompts.NO_PERSON_CLAUSE, prompts._LEGACY_NO_PERSON_CLAUSE):
        if clause and clause in out:
            out = (out.replace(clause, " ")).strip()
    out = out.replace(
        "If the source or approved master has a wearer, extract the garment and REMOVE the original "
        "wearer completely; do not preserve that person or mannequin.",
        " ",
    )
    return " ".join(out.split())


def compose_template(
    instruction: str,
    original_prompt: str,
    *,
    pattern_mode: str = "",
    subject: str = "",
    garment=None,
) -> str:
    """把一条改图需求合并成结构化提示词（不再"原话贴尾"）。"""
    instruction = " ".join((instruction or "").split())
    original_prompt = _sanitize_brief(original_prompt)
    intents = prompts.classify_requirement(instruction)
    mode = (pattern_mode or "").strip().lower()
    if mode not in prompts.PATTERN_MODES:
        mode = prompts.detect_pattern_mode(instruction) or "preserve"

    change = [f'WHAT TO CHANGE (authoritative): "{instruction}"']
    sentences = _intent_sentences(intents, mode)
    if sentences:
        change.append("Apply it as: " + "; ".join(sentences) + ".")
    if mode != "preserve":
        change.append(
            "The default 'artwork must stay pixel-identical' rule is LIFTED for this pass: "
            + prompts.pattern_clause(mode, brief=instruction)
        )
    # 运营在「改图」里要求换款式（例：把卫衣改成蓝色长袖童装）时：
    # 必须把「保持原服装类型/剪裁/罗纹袖口」这条撤掉，并显式给出目标款式的构造约束，
    # 否则改图提示词会亲手把卫衣的罗纹收口保留下来（2026-11-09 童装长袖实测）。
    garment_change = "garment_change" in intents and garment is not None
    keep = prompts.keep_clause(
        intents, pattern_mode=mode, subject=subject, garment_change=garment_change
    )
    if not keep:
        keep = (
            "KEEP UNCHANGED — everything the request does not mention stays identical."
        )
    # 注意：参数 garment 是款式对象，这里另起名字，别把它覆盖掉
    garment_subject = str(subject or "").strip().lower() == "garment"
    prompt = REWORK_TEMPLATE.format(
        change=" ".join(change),
        keep=keep,
        subject_keep=SUBJECT_KEEP_GARMENT if garment_subject else SUBJECT_KEEP_NEUTRAL,
        original_prompt=original_prompt or "(none)",
    )
    if garment_change:
        # 目标款式的构造约束（长袖/短袖、平口/罗纹、不许帽兜）放在最前面，优先于「别动」类条款
        prompt = (
            prompts.convert_clause(garment, reference_image=False)
            + " "
            + prompts.sleeve_clause(garment)
            + " "
            + prompts.cuff_clause(garment)
            + " "
            + prompt
        )
    # Attachment roles are rebound by service/runner once the available originals are known.
    return prompts.build_rework_reference_prompt(prompt, subject, original_count=0)


def signature(text: str) -> str:
    """给测试/前端用：返回需求是否真的改变了提示词（长度 + 首 120 字）。"""
    text = " ".join((text or "").split())
    return f"{len(text)}|{text[:120]}"


def format_brief(
    *,
    instruction: str,
    original_prompt: str,
    reference_image: str,
    task_id: str,
    garment: str = "",
    color: str = "",
    pattern_mode: str = "",
    subject: str = "",
) -> str:
    """Structured brief for an external harness / human reviewer."""
    intents = prompts.classify_requirement(instruction)
    mode = (pattern_mode or "").strip().lower() or prompts.detect_pattern_mode(instruction) or "preserve"
    keep = prompts.keep_clause(intents, pattern_mode=mode, subject=subject)
    return "\n".join(
        [
            "# 改图需求（rework brief）",
            f"- 参考图（当前成品）：{reference_image}",
            f"- 来源任务：{task_id}",
            f"- 款式 / 颜色：{garment} / {color}",
            f"- 图案策略：{prompts.PATTERN_MODES.get(mode, mode)}",
            f"- 主体：{subject or 'auto'}",
            "",
            "## 用户本次需求（优先级最高）",
            (instruction or "").strip() or "(未填写)",
            "",
            "## 识别到的改动点",
            "、".join(intents) if intents else "（未识别到具体类别，按原话执行）",
            "",
            "## 其余保持不变",
            keep or "（用户要求整体重做，无额外锁定）",
            "",
            "## 原始生产提示词（仅作上下文）",
            (original_prompt or "").strip() or "(无)",
        ]
    )


async def compose_llm(
    instruction: str,
    original_prompt: str,
    *,
    base_url: str,
    model: str,
    api_key: str,
    timeout: float = 60.0,
    pattern_mode: str = "",
) -> str:
    mode = (pattern_mode or "").strip().lower() or prompts.detect_pattern_mode(instruction) or "preserve"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": LLM_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Operator instruction: {instruction}\n"
                    f"Pattern policy for this pass: {mode}"
                    f"{' (artwork may be redesigned)' if mode != 'preserve' else ' (artwork must stay identical)'}\n\n"
                    f"Original production prompt: {original_prompt}"
                ),
            },
        ],
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{base_url.rstrip('/')}/chat/completions"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=body, headers=headers)
        response.raise_for_status()
        payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError(f"chat 接口未返回结果: {json.dumps(payload, ensure_ascii=False)[:200]}")
    text = (choices[0].get("message") or {}).get("content") or ""
    text = " ".join(text.split())
    if len(text) < 20:
        raise ValueError("chat 接口返回内容过短")
    return text


async def compose(
    instruction: str,
    original_prompt: str,
    *,
    refiner: str = "template",
    cfg: dict | None = None,
    env: dict | None = None,
    pattern_mode: str = "",
    subject: str = "",
    garment=None,
) -> tuple[str, str]:
    """Return (prompt, refiner_used)."""
    refiner = (refiner or "template").strip().lower()
    if refiner == "llm" and cfg:
        model = str(cfg.get("model") or "").strip()
        key = str((env or {}).get(str(cfg.get("api_key_env") or ""), "")).strip()
        if cfg.get("enabled") and model and key:
            try:
                prompt = await compose_llm(
                    instruction,
                    original_prompt,
                    base_url=str(cfg.get("base_url") or ""),
                    model=model,
                    api_key=key,
                    pattern_mode=pattern_mode,
                )
                return prompts.build_rework_reference_prompt(prompt, subject, original_count=0), "llm"
            except Exception:  # noqa: BLE001 - fall back, never block a rework
                return compose_template(
                    instruction, original_prompt, pattern_mode=pattern_mode,
                    subject=subject, garment=garment
                ), "template(降级)"
        return compose_template(
            instruction, original_prompt, pattern_mode=pattern_mode,
            subject=subject, garment=garment
        ), "template(未配置 LLM)"
    return (
        compose_template(
            instruction, original_prompt, pattern_mode=pattern_mode,
            subject=subject, garment=garment
        ),
        "template",
    )
