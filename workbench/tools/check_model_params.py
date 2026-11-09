"""模型参数收敛回归：grok 系列只接受 low / medium / auto 的 quality。

背景（2026-11-09）：备用接口（65535）用 grok 生图时报
`upstream_error: upstream rejected the request: This model only supports the following quality
value(s): low, medium, auto.` —— 工作台默认发 `quality=high`，一次就白跑还可能计费。

现在：`config.json → defaults.quality_rules` 里按模型前缀声明允许值，
`Provider.quality_for()` 在建单时收敛（界面/预估/任务参数一致），
`openai_compat._form()` 在发请求前再兜一次（旧任务/重试也安全）。

用法：
    python tools/check_model_params.py            # 离线断言
    python tools/check_model_params.py --show     # 打印每条判定
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge.providers.base import EditRequest  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []

GROK_MODELS = ("grok-imagine-image", "grok-imagine-image-2.0", "grok-imagine-image-quality",
               "grok-imagine-video")


def check(ok: bool, label: str, show: bool) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


def main() -> int:
    show = "--show" in sys.argv
    wb = Workbench()
    terra = wb.get_provider("terra")

    print("① 配置里有 grok 的 quality 规则")
    rules = [r for r in terra.quality_rules() if "grok" in str(r.get("match", "")).lower()]
    check(bool(rules), "备用接口能读到 grok 的 quality_rules", show)
    if rules:
        allowed = [str(x).lower() for x in rules[0].get("allowed") or []]
        check("high" not in allowed, "规则里不包含 high", show)
        check({"low", "medium", "auto"} <= set(allowed), "规则里含 low/medium/auto", show)

    print("② 收敛逻辑")
    for model in GROK_MODELS:
        check(terra.quality_for(model, "high") == "auto", f"{model}: high → auto", show)
        check(terra.quality_for(model, "medium") == "medium", f"{model}: medium 保留", show)
        check(terra.quality_for(model, "low") == "low", f"{model}: low 保留", show)
        check(terra.quality_for(model, "") == "auto", f"{model}: 空值 → auto", show)
    for model in ("gpt-image-2.6-sunburst", "gpt-image-2.5-sunburst"):
        check(terra.quality_for(model, "high") == "high", f"{model}: high 不受影响", show)
    a6api = wb.get_provider("a6api")
    check(a6api.quality_for("gpt-image-2.5-sunburst", "high") == "high", "主接口不受影响", show)

    print("③ 真正发出的表单")
    for model, wanted, expect in (
        ("grok-imagine-image-2.0", "high", None),      # 收敛成 auto → 不发送该字段
        ("grok-imagine-image-2.0", "medium", "medium"),
        ("gpt-image-2.6-sunburst", "high", "high"),
    ):
        request = EditRequest(images=[], prompt="x", negative="", size="auto",
                              quality=wanted, model=model)
        form = terra._form(request)
        got = form.get("quality")
        check(got == expect, f"{model} requested={wanted} → 表单 quality={got!r}（期望 {expect!r}）", show)

    print("④ 建单时就把参数改好（不是等到失败）")
    source = ROOT / "library" / "effects" / "23-sorona-tee" / "WH[0].jpg"
    if source.is_file():
        if True:
            wb2 = Workbench()
            job = wb2.create_job(
                [("item-01.png", source.read_bytes())],
                garment_type="23-sorona-tee",
                colors=["black"],
                provider="terra",
                model="grok-imagine-image-2.0",
                quality="high",
                subject="garment",
            )
            check(job.params.get("quality") == "auto", "任务参数记录的是 auto", show)
            # 这个任务只是用来验证参数，不留在正式历史里
            import shutil

            shutil.rmtree(wb2.job_dir(job.id), ignore_errors=True)
    else:
        print("  [skip] 找不到 23-sorona-tee 的效果图，跳过建单检查")

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("全部通过：grok 不会再收到 quality=high")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
