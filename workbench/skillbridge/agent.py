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
from .harness_context import describe_context, target_requirements

MAX_IMAGE_SIDE = 1200
MAX_IMAGES = 6

SYSTEM_PROMPT = """你是服装电商改图工作台的 AI 助手，直接和运营对话，并可以调用工具完成操作。

**最高原则：用户的要求优先于 skill 的默认规则。**
默认规范是「图案逐像素保留、只换面料底色」——那只是默认值，不是铁律。
用户如果说"每个颜色换不同的图案""把图案换成 XX""重画印花"，就按用户说的做：
不要用"跟硬规则冲突"来拒绝、不要劝他改需求，最多用一句话说明代价
（例如图案会被模型重新生成、原图分辨率低时字容易走样），然后照常开工。
需要时调用 set_requirements 时把 pattern_mode 一起设上
（preserve=照抄图案 / per_color_variant=每色一套新图案 / replace=换成新图案 / free=随便重画）。

你的职责：
1. 开工前问清楚这一批有没有特殊要求；没有就明确说"按默认规范"，不要自己加戏。
2. 有要求时：先调用 inspect_images 看清这批图片，再把要求翻译成生图模型能执行的约束。
   画面主体设定优先于参考图：garment 默认只出衣服；person 默认保留原图人物。保留是默认值，不禁止运营明确指定的人物编辑：两人合照要求“只保留男人／移除指定人物”时，只移除指定对象，保留其余人物的脸、身体、姿势和背景，缺失背景自然补全，不得误删全部人物或强制保留被指定删除者。图案锁定由 pattern_mode 决定。
   改图时必须对照“原始上传图”和“待改成品”，按图像角色与 item/view 标签逐张看完整画布。一张图片文件可能同时包含正面、背面或多件衣服，文件数量绝不等于画面视角数量。原图有两面而成品缺一面时，应恢复原图中缺失的那一面及其独有图案，不要说“只改正面，背面保持不动”，也不要让用户重传已经附带的背面。看不到／未发送／读取失败的图要明确说明，不能假装读过。
   分辨率只按 inspect_images 给出的原文件尺寸判断；1254×1254 的长边大于 1200px，不得说成不足 1200px。条件式风险提示不能当成当前图片的测量结果。
3. 需要报价就调用 list_image_models / estimate_cost，不要自己编价格。
4. **用户说「开工 / 跑 / 确认 / 开始 / 提交 / 就这样 / 直接做」等明确同意时，立刻调用
   submit_generation（返工则 submit_rework），不要再多问一遍。**只有确实缺少关键信息
   （比如要改哪里都说不清）时才追问一句。
   **提问不是要求**：像"这图能改吗""大概多少钱""会不会糊"只是询问，不要写进提示词。
   需要用户点头时，先用 set_requirements 记下提炼后的要求（没有就留空），再等确认。
5. 只在用户还没表态时给建议，不要替用户做决定；用户已表态就执行。
6. 说"不能做"之前先想一遍：能不能用参数做到？能不能换个说法做到？
   只有技术上真的做不到（比如要求模型输出 4K 但接口上限 1024）才拒绝，并给出替代方案。

**当前批次选择是权威数据：**
- 必须逐款使用全部已选颜色（有序名称、色号与色值）；单款十色就是十色，绝不能只报第一色或将第一色写成整批统一颜色。
- 原图衣服颜色只是来源观察，已选颜色是目标任务；原图红色、目标黑色或多色不构成冲突，不要追问“黑还是红”。
- 特殊要求只记录用户明确要求的修改，默认对每个款式/颜色分别应用；不要在全局要求中硬编码第一款或第一色。每色目标由该色独立的任务提示词决定。
- 选择、接口、模型、尺寸、质量和输出格式以本轮已验证上下文为准，优先于历史回复和单图示例。现有工具不能修改款式/颜色清单；用户明确要求改选时，请他在工作台改选后再提交，不能声称已经改选。
- 图片文件数、画面人数/视角数和预计输出数不可混淆。报价按完整预计调用数与当前接口/模型/尺寸；未定价的单价和总价必须说未知，不能沿用其他模型的报价。

**这是同一段连续对话，不是每轮重新开始：**
- 上面已经问过、用户已经回答过的内容（题材、方向、保留什么、改成什么）都算数，
  **不要再问第二遍**，也不要重复已经说过的提醒（同一句话只提醒一次）。
- 用户说「是的 / 对 / 开始 / 就这样 / 按你说的 / 可以」= 确认，直接调用工具执行，
  **不要再让他描述一遍**，也不要回头问"你想改哪儿"。
- 上一轮你已经分析出方向（例如"换成冬日冰霜"），这一轮用户只是确认时，
  直接把那个方向当作要求提交（返工 → submit_rework 的 instruction 里写清楚的完整要求）。
- 每轮最多问一个问题；问过了就别再问。信息已经够了就直接做。

**基准图优先的工作方式（很重要）**
现在出图分两段：先出一张「基准图」，用户看过、确认之后，系统才拿这张基准图去给其它颜色改色。
所以：
- 用户说「先出一张看看 / 先出基准」→ 照常提交生成（submit_generation），出来的就是基准图。
- 基准图出来后：**不要催、不要自己替用户确认**。先说清「这张是基准图，确认后才会给其它颜色改色」，
  再问一句哪里要不要调（最多一句）。
- 用户说「可以 / 没问题 / 就这样 / 确定」→ 调用 approve_master（note 里带上他顺口说的要求）。
- 用户说「领口再小点 / 颜色太亮 / 印花别动」这类修改 → 调用 reject_master（instruction 写清楚），
  不要用 submit_generation 重开一批。
- 用户是新手：他讲到什么程度就做到什么程度，专业名词先解释半句再用；
  拿不准的地方给一个推荐选项（「我建议先这样，行不行？」），别一次问三四个问题。
- **沉淀规则是默认行为，不需要用户开口**：用户确认基准图后，后台会**静默评估**这次对齐的
  重要性——判成「这个款式的通病」的会写进款式规则（其中一部分直接升级成款式级硬规则），
  只针对这一张的微调则只留档、不注入提示词。你不用问「要不要记住」，也不要让用户重复说一遍。
  save_style_rule 只在两种情况下主动调用：① 用户这次没走确认流程（比如只是聊天）但明确要求记住；
  ② 你想把一条跨批次都成立的规则写得更清楚。一次性要求（这一张的临时调整）不要记。
- **别和已经沉淀的规则打架**：上下文里给了这个款式已生效的规则时，按它来；
  用户这次说的和旧规则冲突时，以这次为准，并提醒他这条会覆盖旧规则。
- 报价前先 master_status 看还剩几张要出，别把整批价格说成单张。

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
            "description": "按当前上传的图片、款式、颜色开工生成（可能一次包含多个款式，每个款式各自全色）。只有用户明确同意后才调用。",
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
                "如果用户要改图案（每色一套 / 换成新图案），把 pattern_mode 一起设好。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "提炼后的要求，中文一句话"},
                    "pattern_mode": {
                        "type": "string",
                        "enum": ["preserve", "per_color_variant", "replace", "free"],
                        "description": (
                            "图案策略：preserve=图案逐像素照抄（默认）；"
                            "per_color_variant=每个颜色一套不同图案；"
                            "replace=整批换成新图案；free=图案可自由重绘"
                        ),
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "master_status",
            "description": (
                "看当前任务的基准图状态：基准出来了吗、确认了吗、其余颜色改到哪了。"
                "用户问「到哪一步了 / 好了没 / 还要多久」时调用。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_master",
            "description": (
                "确认这张基准图 OK，并按它给其余所有颜色改色（会产生真实费用）。"
                "只有用户明确说「可以 / 就这样 / 确定 / 继续」时才调用。"
                "note 填用户这次同意的附加要求（没有就留空），会一起记进这个款式的长期规则。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "description": "确认时用户额外交代的一句要求（中文，没有就留空）",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_master",
            "description": (
                "基准图不满意，按用户说的改一次（以这张基准图为参考图重新生成，会产生真实费用）。"
                "instruction 写清楚要改哪里；改完后等用户看新图再决定是否确认。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string", "description": "这次要改什么（中文）"}
                },
                "required": ["instruction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_style_skill",
            "description": "看这个款式现在的专属规则（提示词要点 + 已经记住的长期规则），判断要不要补。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_style_rule",
            "description": (
                "把一条「这个款式以后都要遵守」的要求记进款式规则（下次同款自动带上）。"
                "注意：确认基准图时系统会自动沉淀，不需要你手动记；这个工具用于"
                "① 用户明确要求记住 ② 需要补一条跨批次都成立的规则。"
                "只记长期规则，不要记这一张图的一次性要求。text 用中文写清规则。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要记住的规则（中文一句话）"}
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
    # 多款式：每人一项 {garment_type, garment_name, colors:[...]}
    selections: list[dict] = field(default_factory=list)
    batch_context: dict = field(default_factory=dict)
    target_prompts: list[dict] = field(default_factory=list)
    model: str = ""
    subject: str = "auto"
    base_prompt: str = ""
    requirements: str = ""
    pattern_mode: str = ""
    pattern_modes: list[str] = field(default_factory=list)
    draft_id: str = ""
    job_id: str = ""
    task_ids: list[str] = field(default_factory=list)
    #: 运营在基准图上画好的框选标注（相对任务目录的路径）；改图时会一起发给生图接口
    annotation_id: str = ""
    #: 框信息：[{n, x, y, w, h, note}]，坐标 0~1 相对值（前端画框时生成）
    annotations: list[dict] = field(default_factory=list)
    # callables → 返回纯文本给模型看
    list_models: Callable[[], Awaitable[str]] | None = None
    inspect: Callable[[], Awaitable[str]] | None = None
    estimate: Callable[[str, int], Awaitable[str]] | None = None
    submit_generation: Callable[[str], Awaitable[str]] | None = None
    submit_rework: Callable[[str], Awaitable[str]] | None = None
    job_status: Callable[[str], Awaitable[str]] | None = None
    #: 基准图审核期（mode=master）：确认 / 打回 / 看款式规则 / 记款式规则
    master_status: Callable[[], Awaitable[str]] | None = None
    approve_master: Callable[[str], Awaitable[str]] | None = None
    reject_master: Callable[[str], Awaitable[str]] | None = None
    style_skill: Callable[[], Awaitable[str]] | None = None
    save_rule: Callable[[str], Awaitable[str]] | None = None
    # 图片（base64 data url）随首轮请求一起发，供模型真正"看"
    images: list[str] = field(default_factory=list)
    image_labels: list[str] = field(default_factory=list)
    image_notes: str = ""


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


def _user_content(text: str, images: list[str], labels: list[str] | None = None) -> Any:
    if not images:
        return text
    parts: list[dict] = [{"type": "text", "text": text}]
    for index, data_url in enumerate(images[:MAX_IMAGES]):
        label = labels[index] if labels and index < len(labels) else f"图片 {index+1}"
        parts.append({"type": "text", "text": f"图 {index+1} — {label}"})
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts


def _validate_tool_args(name: str, args: dict) -> str:
    definition = next((item["function"] for item in TOOL_DEFS if item["function"]["name"] == name), None)
    if definition is None:
        return f"工具 {name} 不可用"
    if not isinstance(args, dict):
        return "工具参数必须是 JSON 对象"
    schema = definition["parameters"]
    properties = schema.get("properties") or {}
    if set(args) - set(properties):
        return "工具不支持这些参数，不能修改已选款式/颜色/接口/模型：" + ", ".join(sorted(set(args) - set(properties)))
    if any(key not in args for key in schema.get("required") or []):
        return "缺少必填工具参数"
    for key, value in args.items():
        rule = properties[key]
        if rule.get("type") == "string" and not isinstance(value, str):
            return f"{key} 必须是字符串"
        if rule.get("type") == "integer" and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            return f"{key} 必须是非负整数"
        if "enum" in rule and value not in rule["enum"]:
            return f"{key} 不是有效选项"
    return ""


async def _call_tool(ctx: AgentContext, name: str, args: dict) -> str:
    try:
        error = _validate_tool_args(name, args)
        if error:
            return f"工具 {name} 执行失败：{error}"
        if name == "list_image_models" and ctx.list_models:
            return await ctx.list_models()
        if name == "inspect_images" and ctx.inspect:
            return await ctx.inspect()
        if name == "estimate_cost" and ctx.estimate:
            model = str(args.get("model") or ctx.model or "")
            count = int(args.get("count") or ctx.count or 0)
            return await ctx.estimate(model, count)
        if name == "set_requirements":
            if args.get("pattern_mode") and ctx.pattern_modes and args["pattern_mode"] not in ctx.pattern_modes:
                return "失败：当前不支持该图案策略"
            ctx.requirements = " ".join(str(args.get("text") or "").split())
            mode = str(args.get("pattern_mode") or "").strip().lower()
            allowed = ctx.pattern_modes or ["preserve", "per_color_variant", "replace", "free"]
            if mode in allowed:
                ctx.pattern_mode = mode
            elif not mode:
                from .prompts import detect_pattern_mode

                detected = detect_pattern_mode(ctx.requirements)
                if detected:
                    ctx.pattern_mode = detected
            if ctx.batch_context:
                ctx.batch_context["settings"]["pattern_mode"] = ctx.pattern_mode or "preserve"
            note = {
                "preserve": "图案逐像素照抄",
                "per_color_variant": "每个颜色一套不同图案",
                "replace": "换成新图案",
                "free": "图案可自由重绘",
            }.get(ctx.pattern_mode, "默认（图案照抄）")
            return (
                f"已记录要求：{ctx.requirements or '（无，按默认规范）'}；"
                f"图案策略：{note}"
            )
        if name == "submit_generation" and ctx.submit_generation:
            if ctx.mode != "intake":
                return "失败：返工模式不能调用 submit_generation"
            extra = str(args.get("extra_requirements") or ctx.requirements or "")
            result = await ctx.submit_generation(extra)
            if result.startswith("成功"):
                ctx.requirements = extra
            return result
        if name == "submit_rework" and ctx.submit_rework:
            if ctx.mode != "rework":
                return "失败：开工前模式不能调用 submit_rework"
            text = str(args.get("instruction") or ctx.requirements or "")
            result = await ctx.submit_rework(text)
            if result.startswith("成功"):
                ctx.requirements = text
            return result
        if name == "job_status" and ctx.job_status:
            return await ctx.job_status(str(args.get("job_id") or ""))
        if name == "master_status" and ctx.master_status:
            return await ctx.master_status()
        if name == "approve_master" and ctx.approve_master:
            if ctx.mode not in {"master", "intake"}:
                return "失败：现在不是基准图审核阶段"
            return await ctx.approve_master(str(args.get("note") or ""))
        if name == "reject_master" and ctx.reject_master:
            if ctx.mode not in {"master", "intake"}:
                return "失败：现在不是基准图审核阶段"
            text = str(args.get("instruction") or "").strip()
            if not text:
                return "失败：要写清楚改哪里"
            return await ctx.reject_master(text)
        if name == "get_style_skill" and ctx.style_skill:
            return await ctx.style_skill()
        if name == "save_style_rule" and ctx.save_rule:
            text = str(args.get("text") or "").strip()
            if len(text) < 3:
                return "失败：规则太短"
            return await ctx.save_rule(text)
        if name == "get_prompt":
            if ctx.batch_context:
                return describe_context(ctx.batch_context) + "\n特殊要求逐目标应用：" + (ctx.requirements or "无")
            return ctx.base_prompt[:1500] if ctx.base_prompt else "（暂无提示词）"
        return f"工具 {name} 不可用"
    except Exception as exc:  # noqa: BLE001
        return f"工具 {name} 执行失败：{exc}"


async def run_agent(
    *,
    user_message: str,
    ctx: AgentContext,
    history: list[dict] | None = None,
    base_url: str,
    model: str,
    api_key: str,
    max_steps: int = 6,
    timeout: float = 90.0,
    temperature: float = 0.3,
) -> AsyncIterator[dict]:
    """Yield frames: delta / tool / result / prompt / done. Raises on unrecoverable errors."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    # 连续对话：把之前几轮的问答带上，否则模型每轮都从零开始（会反复问同样的问题）
    for item in (history or [])[-14:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        text = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not text:
            continue
        messages.append({"role": role, "content": text[:1500]})
    messages.append(
        {
            "role": "user",
            "content": _user_content(_intake_intro(ctx, user_message), ctx.images, ctx.image_labels),
        }
    )
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
                    args = None
                yield {"event": "tool", "data": {"name": name, "args": args}}
                result = await _call_tool(ctx, name, args)
                if name in {"submit_generation", "submit_rework"} and "成功" in result:
                    submitted = True
                yield {"event": "result", "data": {"name": name, "text": result[:800]}}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": slot["id"] or f"call_{index}",
                        "content": result if name == "get_prompt" else result[:2000],
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
            "batch_context": ctx.batch_context,
            "target_prompts": target_requirements(ctx.target_prompts, ctx.requirements,
                                                   pattern_mode=ctx.pattern_mode, subject=ctx.subject),
        },
    }
    yield {"event": "done", "data": {"ok": True}}


def _intake_intro(ctx: AgentContext, user_message: str) -> str:
    mode_label = {
        "rework": "返工",
        "master": "基准图审核（先出基准，用户确认后才按基准改色）",
    }.get(ctx.mode, "开工前沟通")
    parts = [f"当前模式：{mode_label}"]
    if ctx.annotations:
        lines = []
        for box in ctx.annotations[:8]:
            note = " ".join(str(box.get("note") or "").split())
            if not note:
                continue
            x, y = float(box.get("x") or 0), float(box.get("y") or 0)
            w, h = float(box.get("w") or 0), float(box.get("h") or 0)
            zone = []
            if y + h / 2 < 0.34:
                zone.append("上")
            elif y + h / 2 > 0.66:
                zone.append("下")
            if x + w / 2 < 0.34:
                zone.append("左")
            elif x + w / 2 > 0.66:
                zone.append("右")
            where = "".join(zone) or "中间"
            lines.append(f"框{box.get('n') or '?'}（{where}区域，约 {w*100:.0f}%×{h*100:.0f}%）：{note}")
        if lines:
            parts.append(
                "用户已经在基准图上画框标注了要改的地方（标注图会随改图一起发给生图接口）："
                + "；".join(lines)
                + "。请把这些框的要求合成一条可执行的改图指令，别扩大到框外。"
            )
    if ctx.mode == "master" and ctx.batch_context:
        memory = ctx.batch_context.get("style_memory") or {}
        if memory:
            promoted = list(memory.get("promoted") or [])
            learned = list(memory.get("learned") or [])
            stats = memory.get("stats") or {}
            note = (
                f"这个款式已沉淀规则：硬规则 {len(promoted)} 条、学到的经验 {len(learned)} 条"
                + (f"；历史基准返工 {stats.get('review_rounds')} 轮" if stats.get("review_rounds") else "")
                + "。这些默认已生效，别再问用户「要不要记住」，也别重复记同一句。"
            )
            parts.append(note)
        review = ctx.batch_context.get("review") or {}
        if review:
            parts.append(
                "基准图状态："
                + ("待用户确认" if review.get("awaiting") else "已确认 / 未开始")
                + f"；基准 {len(review.get('master_ids') or [])} 张，"
                f"其余颜色 {review.get('derived_done') or 0}/{review.get('derived_total') or 0} 已出"
            )
    if ctx.subject == "garment":
        parts.append("画面主体=garment：默认只有衣服；如运营明确要求指定人物编辑，遵循该次明确要求，不误删其余人物")
    elif ctx.subject == "person":
        parts.append("画面主体=person：默认保留原图人物；允许明确指定只保留男人/删除另一人，保留其余脸、身体、姿势与背景")
    else:
        parts.append("画面主体=auto：按原图判断，效果图中的人物不决定输出主体")
    if ctx.batch_context:
        parts.append(describe_context(ctx.batch_context))
    elif ctx.selections:
        parts.append(f"本批 {len(ctx.selections)} 款、{sum(len(item.get('colors') or []) for item in ctx.selections)} 色；逐款完整选择："
                     + json.dumps(ctx.selections, ensure_ascii=False))
    else:
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
    if ctx.image_labels:
        parts.append("本轮实际附图（文件数不等于服装视角数）：" + "；".join(f"图{i+1}={label}" for i, label in enumerate(ctx.image_labels)))
    if ctx.image_notes:
        parts.append(ctx.image_notes)
    header = "；".join(parts)
    message = user_message.strip() or "（无特殊要求）"
    return f"{header}\n\n运营输入：{message}\n\n请按你的职责回应；需要报价或看图就调用工具。"
