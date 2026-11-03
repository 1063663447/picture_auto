"""Rework ("改图") prompt composition.

A rework takes an already produced image plus an operator instruction ("BOSS 字太小"
/ "把抽绳去掉" / "整体再亮一点") and turns it into one image-edit request:

* ``template`` refiner (default, offline): merge the instruction with the original
  production brief, so the skill's constraints are still in force and nothing else in
  the image may drift.
* ``llm`` refiner (optional): ask a chat model to rewrite the instruction into a precise
  image-editing prompt, then feed that to the image API. Falls back to ``template`` on
  any error, so a rework never blocks on the chat model.

``format_brief`` produces the structured hand-off an external harness (a coding agent
or a human reviewer) can read: the reference image path, the original prompt, the
operator instruction, and the constraints that must not change.
"""

from __future__ import annotations

import json

import httpx

REWORK_TEMPLATE = (
    "REVISION REQUEST — the attached image is the current accepted result and must be "
    "adjusted, not regenerated. Apply exactly this change and nothing else: {instruction}. "
    "Everything not mentioned stays pixel-identical to the attached image: the same garment "
    "type, cut, seams, ribbing, cuffs, hem, collar, hood, drawstrings and pockets as currently "
    "shown, the same fabric base colour, the same printed / embroidered artwork with its exact "
    "letterforms, spacing, scale, position and material relief, the same person, face, hair, "
    "skin, hands, pose and body proportions, the same background, props, camera angle, framing, "
    "crop, lighting, noise and resolution. Do not re-render the artwork, do not add, remove or "
    "move objects that the instruction does not mention, and do not beautify or upscale the "
    "image. Original production brief for context only — do not re-execute what is already "
    "correct: {original_prompt}"
)

LLM_SYSTEM = (
    "You are a prompt engineer for an image-editing model used by an apparel e-commerce team. "
    "Rewrite the operator's revision request into ONE precise English image-editing prompt. "
    "The attached image is the current result; only the requested change may happen. Explicitly "
    "require keeping the garment type, colour, printed artwork (same letterforms, spacing, scale, "
    "material), person, pose, background, camera angle, framing, lighting and resolution "
    "unchanged. Never describe the whole photo again, never add unrequested changes, no lists, "
    "no markdown — output only the prompt text."
)


def compose_template(instruction: str, original_prompt: str) -> str:
    instruction = " ".join((instruction or "").split())
    original_prompt = " ".join((original_prompt or "").split())
    return REWORK_TEMPLATE.format(instruction=instruction, original_prompt=original_prompt)


def format_brief(
    *,
    instruction: str,
    original_prompt: str,
    reference_image: str,
    task_id: str,
    garment: str = "",
    color: str = "",
) -> str:
    """Structured brief for an external harness / human reviewer."""
    return "\n".join(
        [
            "# 改图需求（rework brief）",
            f"- 参考图（当前成品）：{reference_image}",
            f"- 来源任务：{task_id}",
            f"- 款式 / 颜色：{garment} / {color}",
            "",
            "## 用户本次需求",
            (instruction or "").strip() or "(未填写)",
            "",
            "## 原始生产提示词（不得整体重写，仅作约束上下文）",
            (original_prompt or "").strip() or "(无)",
            "",
            "## 硬约束",
            "- 只做用户要求的那一项改动，其余像素保持不变。",
            "- 服装款式、颜色、印花字形/字距/大小/位置/材质浮雕、人物、姿势、背景、机位、构图、光线、噪点、分辨率都不得变化。",
            "- 不得重新渲染印花、不得新增或删除未提及的元素。",
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
) -> str:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": LLM_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Operator instruction: {instruction}\n\n"
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
                )
                return prompt, "llm"
            except Exception:  # noqa: BLE001 - fall back, never block a rework
                return compose_template(instruction, original_prompt), "template(降级)"
        return compose_template(instruction, original_prompt), "template(未配置 LLM)"
    return compose_template(instruction, original_prompt), "template"
