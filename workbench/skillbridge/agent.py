"""DeepSeek 工具型 harness：看图 + 查价 + 直接提交生图任务。

不是"填模板"的脚本，而是一个带 function calling 的 agent 循环：

    system → (可选图片) → DeepSeek → tool_calls → 本地执行 → tool 结果回填 → 再推理 → 最终话术

工具清单（都在本地执行，agent 只负责决策）：
  list_image_models   模型 + 单价
  inspect_images      看当前这批图片：尺寸、比例、主色、面料色相、有没有检出图案
  estimate_cost       N 张 × 单价 = 预估
  submit_generation   正式开工（写进工作台任务队列）
  job_status          查进度
  submit_rework       以成品为参考图返工
  set_requirements    记下"提炼后的特殊要求"（等你确认时用）
  get_prompt          当前会提交的提示词（截断）

流式：assistant 文本逐块 delta；工具调用发 tool / result 事件，前端能看到它在干什么。
失败（网络/额度/格式）自动降级到本地分析师，保证对话框永远有可用结果。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx
from PIL import Image, ImageOps

MAX_IMAGE_SIDE = 900
MAX_IMAGES = 3

SYSTEM_PROMPT = """你是服装电商改图工作台的 AI 助手，直接和运营对话，并可以调用工具完成操作。

你的职责：
1. 开工前问清楚这一批有没有特殊要求；没有就明确说"按默认规范"，不要自己加戏。
2. 有要求时：先调用 inspect_images 看清这批图片，再判断要求是否与 skill 的硬约束冲突
   （图案必须逐像素保留、人物/背景/机位/光线不动、2MB 以内、保持原比例）。
3. 需要报价就调用 list_image_models / estimate_cost，不要自己编价格。
4. **用户说「开工 / 跑 / 确认 / 开始 / 提交 / 就这样 / 直接做」等明确同意时，立刻调用
   submit_generation（返工则 submit_rework），不要再多问一遍。**只有确实缺少关键信息
   （比如要改哪里都说不清）时才追问一句。
   **提问不是要求**：像"这图能改吗""大概多少钱""会不会糊"只是询问，不要写进提示词。
   需要用户点头时，先用 set_requirements 记下提炼后的要求（没有就留空），再等确认。
5. 只在用户还没表态时给建议，不要替用户做决定；用户已表态就执行。

风格：中文、口语、短句；不要 markdown 标题，不要复述整段提示词；
每次回复控制在 6 行以内，把"会动什么 / 锁死什么"讲清楚即可。
"""

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "list_image_models",
            "description": "列出这个网关可用的图片模型及每张单价，用来报价或推荐模型。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_images",
            "description": "查看当前这批图片的客观信息（尺寸、比例、主色、面料色相、是否检出图案）。调用前不需要用户允许。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_cost",
            "description": "按当前图片数量与模型估算总价。",
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "模型名，留空用当前选择"},
                    "count": {"type": "integer", "description": "调用次数，留空用当前批次的预计次数"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_generation",
            "description": "按当前上传的图片、款式、颜色开工生成。只有用户明确同意后才调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "extra_requirements": {
                        "type": "string",
                        "description": "这次的特殊要求（中文即可）。用户没提要求就留空。",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "job_status",
            "description": "查询任务进度，参数留空查当前任务。",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_rework",
            "description": "把选中的成品作为参考图返工。只有用户明确同意后才调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string", "description": "这次要改什么"},
                },
                "required": ["instruction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_requirements",
            "description": (
                "记下这次要写入提示词的特殊要求：必须是提炼后的一句话执行要求，"
                "绝不能原样抄用户的提问/寒暄。当你已理解要求、等用户点头开工时调用。"
                "用户没有任何特殊要求时不要调用（留空即默认规范）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "提炼后的要求，中文一句话"}
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_prompt",
            "description": "查看当前会提交给生图接口的提示词（会自动截断，前面是主要约束）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


@dataclass
class AgentContext:
    """agent 可用的本地能力，由 service/api 注入。"""

    mode: str = "intake"
    garment: str = ""
    color: str = ""
    count: int = 0
    model: str = ""
    base_prompt: str = ""
    requirements: str = ""
    draft_id: str = ""
    job_id: str = ""
    task_ids: list[str] = field(default_factory=list)
    # callables → 返回纯文本给模型看
    list_models: Callable[[], Awaitable[str]] | None = None
    inspect: Callable[[], Awaitable[str]] | None = None
    estimate: Callable[[str, int], Awaitable[str]] | None = None
    submit_generation: Callable[[str], Awaitable[str]] | None = None
    submit_rework: Callable[[str], Awaitable[str]] | None = None
    job_status: Callable[[str], Awaitable[str]] | None = None
    # 图片（base64 data url）随首轮请求一起发，供模型真正"看"
    images: list[str] = field(default_factory=list)


def encode_image(path: Path, max_side: int = MAX_IMAGE_SIDE) -> str | None:
    try:
        with Image.open(path) as handle:
            image = ImageOps.exif_transpose(handle).convert("RGB")
            image.thumbnail((max_side, max_side))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=82, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return None


def encode_bytes(data: bytes, max_side: int = MAX_IMAGE_SIDE) -> str | None:
    try:
        with Image.open(io.BytesIO(data)) as handle:
            image = ImageOps.exif_transpose(handle).convert("RGB")
            image.thumbnail((max_side, max_side))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=82, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return None


def _user_content(text: str, images: list[str]) -> Any:
    if not images:
        return text
    parts: list[dict] = [{"type": "text", "text": text}]
    for data_url in images[:MAX_IMAGES]:
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts


async def _call_tool(ctx: AgentContext, name: str, args: dict) -> str:
    try:
        if name == "list_image_models" and ctx.list_models:
            return await ctx.list_models()
        if name == "inspect_images" and ctx.inspect:
            return await ctx.inspect()
        if name == "estimate_cost" and ctx.estimate:
            model = str(args.get("model") or ctx.model or "")
            count = int(args.get("count") or ctx.count or 0)
            return await ctx.estimate(model, count)
        if name == "set_requirements":
            ctx.requirements = " ".join(str(args.get("text") or "").split())
            return f"已记录要求：{ctx.requirements or '（无，按默认规范）'}"
        if name == "submit_generation" and ctx.submit_generation:
            return await ctx.submit_generation(str(args.get("extra_requirements") or ""))
        if name == "submit_rework" and ctx.submit_rework:
            return await ctx.submit_rework(str(args.get("instruction") or ""))
        if name == "job_status" and ctx.job_status:
            return await ctx.job_status(str(args.get("job_id") or ""))
        if name == "get_prompt":
            return ctx.base_prompt[:1500] if ctx.base_prompt else "（暂无提示词）"
        return f"工具 {name} 不可用"
    except Exception as exc:  # noqa: BLE001
        return f"工具 {name} 执行失败：{exc}"


async def run_agent(
    *,
    user_message: str,
    ctx: AgentContext,
    base_url: str,
    model: str,
    api_key: str,
    max_steps: int = 6,
    timeout: float = 90.0,
    temperature: float = 0.3,
) -> AsyncIterator[dict]:
    """Yield frames: delta / tool / result / prompt / done. Raises on unrecoverable errors."""
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _user_content(_intake_intro(ctx, user_message), ctx.images),
        },
    ]
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    submitted = False

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
        for step in range(max(1, max_steps)):
            body = {
                "model": model,
                "messages": messages,
                "tools": TOOL_DEFS,
                "tool_choice": "auto",
                "temperature": temperature,
                "stream": True,
            }
            content_parts: list[str] = []
            tool_calls: dict[int, dict] = {}

            async with client.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode("utf-8", "replace")[:300]
                    raise RuntimeError(f"DeepSeek HTTP {response.status_code}: {detail}")
                async for line in response.aiter_lines():
                    line = (line or "").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload in {"", "[DONE]"}:
                        continue
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        content_parts.append(text)
                        yield {"event": "delta", "data": {"text": text}}
                    for call in delta.get("tool_calls") or []:
                        index = int(call.get("index") or 0)
                        slot = tool_calls.setdefault(
                            index, {"id": "", "name": "", "arguments": ""}
                        )
                        if call.get("id"):
                            slot["id"] = call["id"]
                        function = call.get("function") or {}
                        if function.get("name"):
                            slot["name"] = function["name"]
                        if function.get("arguments"):
                            slot["arguments"] += function["arguments"]

            if not tool_calls:
                yield {
                    "event": "message",
                    "data": {"text": "".join(content_parts)},
                }
                break

            assistant_message: dict = {
                "role": "assistant",
                "content": "".join(content_parts) or "",
                "tool_calls": [
                    {
                        "id": slot["id"] or f"call_{index}",
                        "type": "function",
                        "function": {
                            "name": slot["name"],
                            "arguments": slot["arguments"] or "{}",
                        },
                    }
                    for index, slot in sorted(tool_calls.items())
                ],
            }
            messages.append(assistant_message)

            for index, slot in sorted(tool_calls.items()):
                name = slot["name"]
                try:
                    args = json.loads(slot["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield {"event": "tool", "data": {"name": name, "args": args}}
                result = await _call_tool(ctx, name, args)
                if name in {"submit_generation", "submit_rework"} and "成功" in result:
                    submitted = True
                yield {"event": "result", "data": {"name": name, "text": result[:800]}}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": assistant_message["tool_calls"][0]["id"]
                        if len(tool_calls) == 1
                        else assistant_message["tool_calls"][index]["id"],
                        "content": result[:2000],
                    }
                )
            await asyncio.sleep(0)
        else:
            yield {"event": "delta", "data": {"text": "（已达最大工具轮数，先停在这里）"}}

    yield {
        "event": "prompt",
        "data": {
            "prompt": ctx.base_prompt,
            "requirements": ctx.requirements,
            "refiner": "deepseek",
            "submitted": submitted,
        },
    }
    yield {"event": "done", "data": {"ok": True}}


def _intake_intro(ctx: AgentContext, user_message: str) -> str:
    parts = [f"当前模式：{'返工' if ctx.mode == 'rework' else '开工前沟通'}"]
    if ctx.garment:
        parts.append(f"目标款式：{ctx.garment}")
    if ctx.color:
        parts.append(f"颜色：{ctx.color}")
    if ctx.count:
        parts.append(f"预计调用：{ctx.count} 次")
    if ctx.model:
        parts.append(f"当前模型：{ctx.model}")
    if ctx.mode == "rework" and ctx.task_ids:
        parts.append(f"要返工的成品：{len(ctx.task_ids)} 张")
    header = "；".join(parts)
    message = user_message.strip() or "（无特殊要求）"
    return f"{header}\n\n运营输入：{message}\n\n请按你的职责回应；需要报价或看图就调用工具。"
