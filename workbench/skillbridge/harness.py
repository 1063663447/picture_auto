"""Harness: the dialog + streaming brain in front of the image API.

Two jobs:

1. **Intake（开工前）** — ask the operator whether this batch needs anything special.
   No requirement → the skill default prompt is used unchanged. With a requirement →
   the requirement is analysed, matched against the skill's hard constraints, and merged
   into one short "operator requirements" clause that rides along with the default prompt.

2. **Rework（返工时）** — read the operator's revision request together with the original
   production prompt of the selected image, explain what will change and what is locked,
   and emit the exact prompt that will be submitted.

Everything streams: `stream()` yields event dicts that the API turns into SSE frames, so
the UI can print the analysis token by token instead of jumping from spinner to result.

If `defaults.harness.llm` is configured with a reachable chat model, its tokens are
streamed verbatim; otherwise a deterministic local analyst produces the same structure
(`refiner = "local"`), so the workbench never blocks on an unavailable model.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

from . import prompts, refine
from .harness_context import describe_context, target_requirements

#: 用户明确要改图案时，这个话题的结论要换一套说法（不再说"照抄原图"）
ARTWORK_TOPIC = "图案/文字保真"
ARTWORK_NOTE_LOCKED = "图案锁已生效：字形、字距、大小、位置、材质浮雕都按原图照抄"
ARTWORK_NOTE_UNLOCKED = (
    "图案按你的要求重新设计：不用照抄原图，但风格要保持同一家族，且必须是印在面料上的照片质感"
)
TOPIC_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("图案", "印花", "logo", "标志", "字", "刺绣", "贴布", "print", "letter"),
     ARTWORK_TOPIC, ARTWORK_NOTE_LOCKED),
    (("颜色", "色号", "偏蓝", "偏黄", "色差", "colour", "color"),
     "颜色准确度", "以参考图色值为准（色值已写入提示词），只改面料底色"),
    (("亮", "暗", "曝光", "光", "bright", "light"),
     "光线/亮度", "只做整体明暗微调，人物皮肤与背景高光不许溢出"),
    (("背景", "场景", "背景不", "background"),
     "背景保持", "背景、道具、机位、构图全部锁定不变"),
    (("去掉", "删除", "不要", "移除", "remove", "delete"),
     "删除元素", "删除类要求只作用于所指元素，不牵连印花与版型"),
    (("加", "增加", "添加", "add", "换", "改成"),
     "新增/替换元素", "只新增所指元素，其余像素不动；避免模型顺手重画印花"),
    (("比例", "拉长", "宽", "窄", "合身", "版型", "oversize", "fit", "silhouette"),
     "版型/比例", "版型只跟随原图姿势，不要凭空改剪裁"),
    (("清晰", "高清", "糊", "锐", "sharp", "blur", "resolution"),
     "清晰度", "不得放大重绘，保持原分辨率与噪点水平"),
    (("角度", "侧面", "正面", "背面", "angle", "view"),
     "角度/视角", "机位与构图锁定，不做视角重拍"),
]

QUESTION_HINTS = (
    "吗", "多少", "能不能", "可不可以", "会不会", "怎么", "如何", "是不是",
    "行不行", "有没有", "？", "?", "how much", "can you", "is it", "would",
)


def looks_like_question(text: str) -> bool:
    """提问 ≠ 要求：这类句子不能被当成写进提示词的约束。"""
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    return any(hint in lowered for hint in QUESTION_HINTS)


LOCKS = [
    "服装款式与剪裁",
    "未被明确要求编辑的人物身份、脸部、身体、表情、姿势、手部",
    "未被明确要求修改的背景、道具、机位、构图、裁切（指定删除人物处自然补全背景）",
    "光线方向、噪点与分辨率",
]
ARTWORK_LOCK = "印花/文字/logo 的字形、字距、大小、位置与浮雕材质"

#: 用户明确要改印花时才这样说
ARTWORK_UNLOCKED = "印花/图案（按你的要求重新设计，默认的「逐像素保留」不再适用）"


@dataclass
class HarnessResult:
    analysis: str
    requirements: str
    prompt: str
    refiner: str = "local"
    topics: list[str] = field(default_factory=list)
    pattern_mode: str = "preserve"


def analyze_local(
    *,
    mode: str,
    instruction: str,
    base_prompt: str,
    garment: str = "",
    color: str = "",
    count: int = 0,
    batch_context: dict | None = None,
) -> HarnessResult:
    """Deterministic analyst: readable analysis + merged requirement clause."""
    instruction = " ".join((instruction or "").split())
    lowered = instruction.lower()

    hits: list[tuple[str, str]] = []
    for words, label, note in TOPIC_RULES:
        if any(word.lower() in lowered for word in words):
            hits.append((label, note))

    # 用户要求改印花时，默认的「印花锁死」必须明确撤销，否则分析结论会自相矛盾
    pattern_mode = prompts.detect_pattern_mode(instruction) or ((batch_context or {}).get("settings") or {}).get("pattern_mode") or "preserve"
    artwork_unlocked = pattern_mode != "preserve"
    if artwork_unlocked and instruction:
        hits = [
            (label, ARTWORK_NOTE_UNLOCKED if label == ARTWORK_TOPIC else note)
            for label, note in hits
        ]
        if not any(label == ARTWORK_TOPIC for label, _ in hits):
            hits.insert(0, (ARTWORK_TOPIC, ARTWORK_NOTE_UNLOCKED))

    lines: list[str] = []
    if mode == "intake":
        if not instruction:
            lines.append(
                "这次没有额外要求，就按 skill 默认规范出图：改款 + 换色一次完成，"
                "图案与版型按原图照抄。"
            )
        else:
            lines.append(
                f"收到要求，共识别到 {len(hits) or 1} 类关注点。先把它翻译成生图模型能执行的约束，"
                "再和 skill 默认规范合并。"
            )
    else:
        lines.append(
            f"这次要返工 {count} 张，需求是「{instruction or '未填写'}」。"
            + (
                "我按“只动你提到的那一处、其余全部锁死”来拆。"
                if not artwork_unlocked
                else "我按“只动你提到的那一处（图案会重新设计）、其余锁死”来拆。"
            )
        )

    if hits:
        lines.append("")
        for label, note in hits:
            lines.append(f"· {label}：{note}")
    if batch_context:
        lines.extend(["", describe_context(batch_context)])
        lines.append("· 原图颜色与目标色不冲突；按每款全部已选颜色分别生成，不将首色用于整批。")
        lines.append("· 人物保留是默认值；如明确指定只保留男人/删除另一人，仅编辑所指人物，保留其余脸、身体及背景。")
    elif garment or color:
        lines.extend(["", f"· 目标：{garment} / {color}，套用该款式的内置规范与色值。"])
    lines.append("")
    if artwork_unlocked:
        lines.append(
            "锁定不动的部分：" + "、".join(LOCKS) + "。"
        )
        lines.append(
            f"按你的要求放开：{ARTWORK_UNLOCKED}——"
            + (
                "每个颜色各生成一套图案，不会复用同一张图。"
                if pattern_mode == "per_color_variant"
                else "按你给的新图案重新出图。"
            )
        )
    else:
        lines.append("锁定不动的部分：" + "、".join(LOCKS + [ARTWORK_LOCK]) + "。")

    if mode == "intake" and not instruction:
        requirements = ""
        prompt = base_prompt
        lines.append("")
        lines.append("→ 结论：不加任何额外约束，直接使用 skill 默认提示词。")
    elif mode == "intake" and looks_like_question(instruction):
        requirements = ""
        prompt = base_prompt
        lines.append("")
        lines.append(
            "→ 这看起来是提问，不是要求，所以我不把它写进提示词。"
            "要加约束请直接说\"图案必须原样\"这类要求，或点「按默认生成」。"
        )
    elif mode == "intake":
        requirements = instruction
        prompt = prompts.apply_requirement(
            base_prompt, instruction, pattern_mode=pattern_mode
        )
        lines.append("")
        if artwork_unlocked:
            lines.append(
                "→ 结论：这条要求与默认的「图案逐像素保留」冲突，按你的要求执行——"
                "图案锁已撤销，其余约束合并进提示词主体（不是贴一句在末尾）。"
            )
        else:
            lines.append(
                "→ 结论：要求已写进提示词的对应位置；只编辑指定对象，未指定的人物/背景/光线保持，"
                "不是把原话贴在末尾。"
            )
    else:
        requirements = instruction
        prompt = refine.compose_template(
            instruction, base_prompt, pattern_mode=pattern_mode
        )
        lines.append("")
        lines.append("→ 结论：以这张成品为参考图重跑，只应用上面这条需求。")

    return HarnessResult(
        analysis="\n".join(lines),
        requirements=requirements,
        prompt=prompt,
        refiner="local",
        topics=[label for label, _ in hits],
        pattern_mode=pattern_mode,
    )


async def stream(
    *,
    mode: str,
    instruction: str,
    base_prompt: str,
    garment: str = "",
    color: str = "",
    count: int = 0,
    llm_cfg: dict | None = None,
    env: dict | None = None,
    chunk_size: int = 14,
    delay: float = 0.012,
    batch_context: dict | None = None,
    target_prompts: list[dict] | None = None,
) -> AsyncIterator[dict]:
    """Yield {event, data} frames: meta → delta* → prompt → done."""
    yield {"event": "meta", "data": {"mode": mode, "count": count, "batch_context": batch_context or {}}}

    cfg = llm_cfg or {}
    model = str(cfg.get("model") or "").strip()
    key = str((env or {}).get(str(cfg.get("api_key_env") or ""), "")).strip()
    use_llm = bool(cfg.get("enabled")) and bool(model) and bool(key)

    result: HarnessResult | None = None
    if use_llm:
        try:
            async for chunk in _stream_llm(
                mode=mode,
                instruction=instruction,
                base_prompt=base_prompt,
                garment=garment,
                color=color,
                count=count,
                base_url=str(cfg.get("base_url") or ""),
                model=model,
                api_key=key,
                batch_context=batch_context,
            ):
                yield {"event": "delta", "data": {"text": chunk}}
            result = analyze_local(
                mode=mode,
                instruction=instruction,
                base_prompt=base_prompt,
                garment=garment,
                color=color,
                count=count,
                batch_context=batch_context,
            )
            result.refiner = "llm"
        except Exception as exc:  # noqa: BLE001 - degrade to the local analyst
            yield {
                "event": "delta",
                "data": {"text": f"（在线模型不可用，改用本地分析：{type(exc).__name__}）\n\n"},
            }
            result = None

    if result is None:
        result = analyze_local(
            mode=mode,
            instruction=instruction,
            base_prompt=base_prompt,
            garment=garment,
            color=color,
            count=count,
            batch_context=batch_context,
        )
        text = result.analysis
        for start in range(0, len(text), chunk_size):
            yield {"event": "delta", "data": {"text": text[start : start + chunk_size]}}
            if delay:
                await asyncio.sleep(delay)

    yield {
        "event": "prompt",
        "data": {
            "prompt": result.prompt,
            "requirements": result.requirements,
            "refiner": result.refiner,
            "topics": result.topics,
            "batch_context": batch_context or {},
            "target_prompts": target_requirements(target_prompts or [], result.requirements,
                                                   pattern_mode=result.pattern_mode,
                                                   subject=((batch_context or {}).get("settings") or {}).get("subject_resolved", "")),
        },
    }
    yield {"event": "done", "data": {"ok": True}}


async def _stream_llm(
    *,
    mode: str,
    instruction: str,
    base_prompt: str,
    garment: str,
    color: str,
    count: int,
    base_url: str,
    model: str,
    api_key: str,
    batch_context: dict | None = None,
) -> AsyncIterator[str]:
    system = (
        "你是服装电商改图工作台的助理。用中文、简短、口语化地说明你会怎么改："
        "先复述需求，再列出这次会动的点，最后列出锁死不动的点。不要写完整提示词，不要用 markdown 列表符号以外的格式。"
    )
    user = (
        f"模式：{'开工前需求确认' if mode == 'intake' else '成品返工'}\n"
        f"目标：{garment} / {color}\n"
        f"本次图片数量：{count}\n"
        f"操作员需求：{instruction or '（无）'}\n"
        f"原始规范提示词（节选）：{base_prompt[:400]}"
        f"\n{describe_context(batch_context or {})}"
    )
    body = {
        "model": model,
        "stream": True,
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(60.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST", f"{base_url.rstrip('/')}/chat/completions", json=body, headers=headers
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                line = (line or "").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload in {"", "[DONE]"}:
                    continue
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    yield text
