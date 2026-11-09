"""Validated, credential-free batch context shared by the two harness paths."""

from __future__ import annotations

import copy
import json


def validate_selections(skill, body: dict) -> list[dict]:
    """Keep UI order, resolve against the catalog, and match create_job deduplication.

    An explicitly empty selection list means nothing selected, never the first style.
    Legacy callers without ``selections`` retain the service's empty-colors=all rule.
    Explicit selection rows must contain at least one valid color.
    """
    explicit = "selections" in body
    rows = body.get("selections") if explicit else None
    if explicit and not isinstance(rows, list):
        raise ValueError("selections 必须是款式/颜色数组")
    if not explicit:
        slug = body.get("garment_type")
        if not slug:
            return []
        rows = [{"garment_type": slug, "colors": body.get("colors") or []}]
    selected: list[dict] = []
    seen: dict[str, dict] = {}
    for index, row in enumerate(rows or [], 1):
        if not isinstance(row, dict):
            raise ValueError(f"第 {index} 项选择必须是对象")
        slug = row.get("garment_type") or row.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            raise ValueError(f"第 {index} 项未选择款式")
        try:
            garment = skill.type_by_alias(slug)
        except KeyError as exc:
            raise ValueError(f"未知款式：{slug}") from exc
        keys = row.get("colors")
        if not isinstance(keys, list) or any(not isinstance(key, str) or not key.strip() for key in keys):
            raise ValueError(f"{garment.display_name} 的 colors 必须是非空颜色名称数组")
        if explicit and not keys:
            raise ValueError(f"{garment.display_name} 至少选择一个颜色")
        try:
            refs = garment.resolve_colors(keys)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        if not refs:
            raise ValueError(f"{garment.display_name} 没有可用颜色")
        target = seen.get(garment.slug)
        if target is None:
            target = {"garment_type": garment.slug, "garment_name": garment.display_name,
                      "colors": [], "color_details": []}
            seen[garment.slug] = target
            selected.append(target)
        for ref in refs:
            if ref.label in target["colors"]:
                continue
            target["colors"].append(ref.label)
            target["color_details"].append({
                "label": ref.label, "name_zh": ref.name_zh,
                "code": ref.code or ref.file_code, "hex": ref.hex, "rgb": list(ref.rgb),
            })
    return selected


def job_selections(selections: list[dict]) -> list[dict]:
    """Return a fresh service payload; the agent cannot mutate the validated plan."""
    return [{"garment_type": row["garment_type"], "garment_name": row["garment_name"],
             "colors": list(row["colors"])} for row in selections]


def batch_context(*, selections: list[dict], source_count: int, output_count: int,
                  settings: dict, estimate: dict, tasks: list[dict] | None = None) -> dict:
    return copy.deepcopy({
        "selections": selections, "style_count": len(selections),
        "total_colors": sum(len(row["colors"]) for row in selections),
        "source_count": source_count, "output_count": output_count,
        "count_rule": "返工=已选成品任务数" if tasks is not None else "原图文件数 × 各款式已选颜色数之和",
        "settings": settings, "estimate": estimate, "tasks": tasks or [],
        "selection_authority": "当前已验证选择是权威数据；现有 AI 工具不能改选，不能以首色覆盖整批",
        "source_color_rule": "原图颜色是来源观察，目标颜色逐项来自 selections；两者不同不是冲突",
        "resolution_evidence": "原图尺寸以 inspect_images 的原文件测量为准，附图压缩尺寸不是原图尺寸",
    })


def describe_context(context: dict) -> str:
    """Never truncate the tail colors or silently replace unknown prices with zero."""
    if not context:
        return ""
    estimate = context.get("estimate") or {}
    if estimate.get("priced") and estimate.get("unit") is not None:
        price = (f"单价 {estimate['unit']} {estimate.get('currency', '')}/张；"
                 f"预计总价 {estimate.get('total')} {estimate.get('currency', '')}")
    else:
        price = "单价未知；预计总价未知（当前接口/模型/尺寸未定价）"
    return ("当前已验证批次（优先于历史与单图示例）：\n"
            + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            + f"\n原图文件 {context['source_count']} 个；预计输出 {context['output_count']} 张；{price}。")


def batch_prompt(context: dict) -> str:
    """A batch preview, not the first target's production prompt applied globally."""
    return ("批次提示词预览：以下每个款式/颜色分别建立独立任务，按各自色号/色值生成；"
            "特殊要求逐任务应用，不能将任何首色硬编码为整批目标。\n"
            + describe_context(context))


def target_requirements(targets: list[dict], requirements: str, *, pattern_mode: str = "preserve",
                        subject: str = "") -> list[dict]:
    """Preview common operator edits separately against every target's own prompt."""
    from . import prompts

    result = []
    for target in targets:
        row = dict(target)
        row["requirements"] = requirements
        if requirements:
            row["prompt"] = prompts.apply_requirement(
                row["prompt"], requirements, pattern_mode=pattern_mode,
                subject=row.get("subject") or subject,
            )
        result.append(row)
    return result