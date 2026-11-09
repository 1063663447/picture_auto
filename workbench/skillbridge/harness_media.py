"""Labeled original/result pairs for harness vision and factual image inspection."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from .job import Job, Task
from .sources import job_image_path, original_uploads


@dataclass(frozen=True)
class HarnessImage:
    path: Path
    role: str
    label: str


def rework_images(
    job: Job, tasks: list[Task], get_job: Callable[[str], Job], *, limit: int = 6,
) -> tuple[list[HarnessImage], list[str]]:
    """Budget whole primary pairs first, then extra views, with explicit omissions."""
    selected: list[HarnessImage] = []
    extra: list[HarnessImage] = []
    notes: list[str] = []
    seen: set[tuple[str, Path]] = set()
    omitted = 0
    for task in tasks:
        item = str(getattr(task, "item", "") or "未命名 item")
        view = str(getattr(task, "view", ""))
        tag = f"{item}{' / '+view if view else ''} · 任务 {task.id}"
        current = job_image_path(job, task.out)
        originals = original_uploads(job, task, get_job)
        candidates = [HarnessImage(upload.path, "original",
                                  f"原始上传图 · {upload.item or item}{' / '+upload.view if upload.view else ''} · {upload.name}")
                      for upload in originals]
        if not candidates:
            notes.append(f"{tag}：原始上传图缺失或来源无法追溯，本轮不能假定已看到原图。")
        primary = candidates[:1]
        if current:
            primary.append(HarnessImage(current, "current", f"待改成品 · {tag} · {current.name}"))
        else:
            notes.append(f"{tag}：待改成品文件缺失。")
        new = [row for row in primary if (row.role, row.path) not in seen]
        if len(selected) + len(new) > limit:
            omitted += 1
            continue
        selected.extend(new)
        seen.update((row.role, row.path) for row in new)
        extra.extend(candidates[1:])
    missing_views = 0
    for row in extra:
        key = (row.role, row.path)
        if key in seen:
            continue
        if len(selected) >= limit:
            missing_views += 1
            continue
        selected.append(row)
        seen.add(key)
    if omitted or missing_views:
        notes.append(f"本轮附图上限 {limit} 个文件；还有 {omitted} 个成品对照组、{missing_views} 张额外原始视角未附带，不能声称已逐张看完。可缩小所选范围继续对照。")
    return selected, notes


def inspect_images(images: list[HarnessImage], notes: list[str] | None = None) -> str:
    """Measure real file sizes; never infer view count or repeat a generic warning."""
    from .providers.local_recolor import LocalRecolorProvider, _border_color

    helper = LocalRecolorProvider("inspect", {})
    lines = ["按角色对照以下图片文件；一张文件可能含正反面／多视角，文件数量不是画面中的服装数量："]
    low: list[str] = []
    readable = 0
    for index, row in enumerate(images, start=1):
        try:
            with Image.open(row.path) as handle:
                width, height = handle.size
                rgb = handle.convert("RGB")
            tone = helper._estimate_garment(rgb, _border_color(rgb))
            line = f"图{index} — {row.label}：原文件 {width}×{height}（横:竖 ≈ {round(width/max(1,height),3)}）面料主色 RGB{tone}"
            if max(width, height) < 1200:
                line += f" · 仅此文件长边 {max(width,height)}px < 1200px"
                low.append(f"图{index}")
            else:
                line += f" · 长边 {max(width,height)}px ≥ 1200px"
            lines.append(line)
            readable += 1
        except (OSError, ValueError):
            lines.append(f"图{index} — {row.label}：读取失败，不能声称已看过。")
    if low:
        lines.append("仅 " + "、".join(low) + " 低于 1200px；若目标输出需放大，图案细字存在补细节风险。其他文件不能据此判为低分辨率。")
    elif readable:
        lines.append("本轮已读文件的长边均不低于 1200px，不适用‘不足1200px’提示。")
    else:
        lines.append("没有可读取的图片，不能确认原图或成品内容。")
    lines.extend(notes or [])
    return "\n".join(lines)
