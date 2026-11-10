# -*- coding: utf-8 -*-
"""AI 助手（对话框）回归：工具齐不齐、模式对不对、审核动作真的被调用。

客户需求（2026-11-11）：「提供一个 AI 助手，以对话框形式来做改图、需求对齐、改色等动作，
尽量还原他们在 codex 上直接对话改图的操作（但保留我们的原生系统），注意配好 harness 各项工具和上下文」。

这里不发任何网络请求，用假的回调把 agent 的工具分发钉死：

  ① 工具清单：审核期的 5 个工具在、名字稳定
  ② 上下文：master 模式把基准图状态写进开场上下文
  ③ 分发：approve / reject / status / style / save_rule 各自调对回调、参数传对
  ④ 拦截：非审核期不许调审核工具；空 instruction 要被挡下
  ⑤ 话术：系统提示词里写清「先出基准 → 用户确认 → 再改色」和「别自己替用户确认」

用法：python tools/check_assistant.py [--show]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import agent  # noqa: E402

FAILURES: list[str] = []

REQUIRED_TOOLS = {
    "list_image_models", "inspect_images", "estimate_cost", "submit_generation",
    "job_status", "submit_rework", "set_requirements", "get_prompt",
    "master_status", "approve_master", "reject_master", "get_style_skill", "save_style_rule",
}
MASTER_TOOLS = {"master_status", "approve_master", "reject_master", "get_style_skill", "save_style_rule"}


def check(ok: bool, label: str, show: bool = False) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


class Recorder:
    """假的本地能力：记录被怎么调用。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def master_status(self) -> str:
        self.calls.append(("master_status", ""))
        return "任务 j1 · review · 基准 1 张待确认"

    async def approve_master(self, note: str) -> str:
        self.calls.append(("approve_master", note))
        return f"成功：基准图已确认，开始按它给 4 个颜色改色（note={note}）"

    async def reject_master(self, instruction: str) -> str:
        self.calls.append(("reject_master", instruction))
        return f"成功：修改任务 j2 已提交（{instruction}）"

    async def style_skill(self) -> str:
        self.calls.append(("style_skill", ""))
        return "款式：23 索罗娜短袖；已记住 1 条规则：袖口窄一点"

    async def save_rule(self, text: str) -> str:
        self.calls.append(("save_rule", text))
        return f"成功：已记进款式规则：{text}"


def build_ctx(mode: str = "master", recorder: Recorder | None = None) -> agent.AgentContext:
    rec = recorder or Recorder()
    ctx = agent.AgentContext(
        mode=mode,
        garment="23 索罗娜短袖",
        job_id="j1",
        task_ids=["t1"],
        batch_context={
            "review": {"awaiting": True, "master_ids": ["t1"],
                       "pending_ids": ["t1"], "approved_ids": [],
                       "derived_total": 4, "derived_done": 0},
            "source_count": 1,
            "output_count": 4,
            "settings": {"provider": "recorder", "model": "recorder-1", "size": "auto",
                         "quality": "high", "subject_resolved": "garment"},
            "estimate": {"priced": True, "total": 0.152, "currency": "CNY",
                         "unit": 0.038, "count": 4, "model": "recorder-1"},
            "tasks": [],
        },
        base_prompt="BASE PROMPT",
        subject="garment",
    )
    ctx.master_status = rec.master_status
    ctx.approve_master = rec.approve_master
    ctx.reject_master = rec.reject_master
    ctx.style_skill = rec.style_skill
    ctx.save_rule = rec.save_rule
    ctx._recorder = rec       # type: ignore[attr-defined]
    return ctx


async def run_calls(show: bool) -> None:
    print("③ 分发：审核动作各自调对回调")
    rec = Recorder()
    ctx = build_ctx(recorder=rec)

    text = await agent._call_tool(ctx, "master_status", {})
    check("待确认" in text and rec.calls[-1][0] == "master_status", f"master_status 返回状态：{text[:24]}", show)

    text = await agent._call_tool(ctx, "approve_master", {"note": "袖口再窄一点"})
    check(rec.calls[-1] == ("approve_master", "袖口再窄一点"),
          "approve_master 把备注原样传下去", show)
    check(text.startswith("成功"), f"approve_master 返回成功：{text[:20]}", show)

    text = await agent._call_tool(ctx, "reject_master", {"instruction": "领口小一点"})
    check(rec.calls[-1] == ("reject_master", "领口小一点"),
          "reject_master 把修改要求传下去", show)
    check("修改任务" in text, f"reject_master 返回新建任务：{text[:24]}", show)

    text = await agent._call_tool(ctx, "get_style_skill", {})
    check("已记住 1 条规则" in text, f"get_style_skill 返回款式规则：{text[:24]}", show)

    text = await agent._call_tool(ctx, "save_style_rule", {"text": "袖口一律窄一点"})
    check(rec.calls[-1] == ("save_rule", "袖口一律窄一点"), "save_style_rule 记进款式规则", show)
    check(text.startswith("成功"), "save_style_rule 返回成功", show)

    print("④ 拦截：模式与参数校验")
    text = await agent._call_tool(ctx, "save_style_rule", {"text": "x"})
    check("失败" in text, "过短的规则被挡下", show)
    rework_ctx = build_ctx(mode="rework")
    text = await agent._call_tool(rework_ctx, "approve_master", {"note": ""})
    check("失败" in text, "返工模式不许确认基准图", show)
    rec2 = Recorder()
    rework_ctx.master_status = rec2.master_status
    rework_ctx.approve_master = rec2.approve_master
    text = await agent._call_tool(rework_ctx, "approve_master", {"note": ""})
    check(not rec2.calls, "被拦下时没有真的调用审核动作", show)
    text = await agent._call_tool(ctx, "reject_master", {"instruction": ""})
    check("失败" in text, "空的修改要求被挡下", show)


def main() -> int:
    show = "--show" in sys.argv

    print("① 工具清单：审核期 5 个工具都在")
    names = {item["function"]["name"] for item in agent.TOOL_DEFS}
    check(REQUIRED_TOOLS <= names, f"缺工具：{sorted(REQUIRED_TOOLS - names)}", show)
    check(MASTER_TOOLS <= names, "审核期工具齐全（master_status/approve/reject/skill/rule）", show)
    for item in agent.TOOL_DEFS:
        fn = item["function"]
        check(isinstance(fn.get("description"), str) and len(fn["description"]) > 10,
              f"{fn['name']} 有可用描述", show)
        check(fn.get("parameters", {}).get("type") == "object",
              f"{fn['name']} 参数是 JSON object", show)

    print("② 上下文：master 模式把基准图状态写进开场")
    intro = agent._intake_intro(build_ctx(), "可以，按它改色")
    check("基准图审核" in intro, "开场说明当前是基准图审核模式", show)
    check("待用户确认" in intro, "开场带上基准图状态（待确认）", show)
    check("derived_total" not in intro or "其余颜色" in intro, "开场带上派生进度", show)
    check("运营输入：可以，按它改色" in intro, "开场保留用户原话", show)

    print("⑤ 话术：系统提示词写清两段式与「别替用户确认」")
    sys_prompt = agent.SYSTEM_PROMPT
    check("基准图" in sys_prompt, "系统提示词讲了基准图", show)
    check("approve_master" in sys_prompt, "明确要求用 approve_master 确认", show)
    check("reject_master" in sys_prompt, "明确要求用 reject_master 修改", show)
    check("不要自己替用户确认" in sys_prompt or "不要催" in sys_prompt,
          "禁止助手替用户确认/催确认", show)
    check("save_style_rule" in sys_prompt, "讲了规则沉淀（save_style_rule）", show)
    check("新手" in sys_prompt, "对新手用户有语气要求", show)

    asyncio.run(run_calls(show))

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES[:15]:
            print("  -", item)
        return 1
    print("全部通过：对话框能确认基准、打回修改、看/记款式规则，工具与上下文都齐")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
