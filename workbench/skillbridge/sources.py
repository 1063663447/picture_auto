"""Trace editable results back to uploads, including legacy/repeated rework jobs.

All returned files must stay inside their recorded job root. A copied rework
input is a current result, never an original upload merely because it is in inputs/.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .job import Job, Task


@dataclass(frozen=True)
class OriginalUpload:
    path: Path
    job_id: str
    task_id: str
    item: str
    view: str
    name: str


def job_image_path(job: Job, relative: str | None) -> Path | None:
    """An existing regular image inside this job; reject escape/absolute paths."""
    if not relative or not getattr(job, "root", None):
        return None
    value = Path(str(relative))
    if value.is_absolute():
        return None
    root = Path(job.root).resolve()
    try:
        path = (root / value).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
    except (OSError, ValueError):
        return None
    return path


def _base_color(value: str) -> str:
    while value.endswith("-v2"):
        value = value[:-3]
    return value


def original_uploads(
    job: Job,
    task: Task,
    get_job: Callable[[str], Job],
    *,
    max_depth: int = 16,
    _seen: frozenset[tuple[str, str]] = frozenset(),
) -> list[OriginalUpload]:
    """Find all uploaded views of this item, never other items.

    New reworks carry immutable local copies plus an exact parent-task map.
    Legacy jobs use their recorded source task IDs and unambiguous item/color
    association. Ambiguous/missing ancestry returns no originals, not a guess.
    """
    job_id = str(getattr(job, "id", ""))
    task_id = str(getattr(task, "id", ""))
    key = (job_id, task_id)
    if max_depth <= 0 or key in _seen:
        return []
    seen = _seen | {key}
    params = getattr(job, "params", {}) or {}
    item = str(getattr(task, "item", ""))
    view = str(getattr(task, "view", ""))
    found: list[OriginalUpload] = []
    paths = (params.get("original_sources") or {}).get(task_id) or []
    metadata = params.get("original_source_meta") or {}
    for relative in paths if isinstance(paths, list) else []:
        path = job_image_path(job, relative)
        if path is None:
            continue
        info = metadata.get(str(relative)) or {}
        found.append(OriginalUpload(
            path=path,
            job_id=str(info.get("job_id") or job_id),
            task_id=str(info.get("task_id") or task_id),
            item=str(info.get("item") or item),
            view=str(info.get("view") or view),
            name=str(info.get("name") or path.name),
        ))
    if found:
        return _unique(found)

    parent_id = str(params.get("source_job") or "")
    is_rework = params.get("kind") == "rework" or params.get("mode") == "rework"
    if not is_rework and not parent_id:
        candidates = [task]
        if item:
            candidates += [t for t in getattr(job, "tasks", []) if str(getattr(t, "item", "")) == item]
        for candidate in candidates:
            path = job_image_path(job, getattr(candidate, "source", ""))
            if path is not None:
                found.append(OriginalUpload(path, job_id, str(getattr(candidate, "id", "")),
                                            item, str(getattr(candidate, "view", "")), path.name))
        return _unique(found)
    if not parent_id:
        return []
    try:
        parent = get_job(parent_id)
    except Exception:  # a missing/removed source job is optional context, not another item
        return []
    parent_tasks = list(getattr(parent, "tasks", []))
    exact = (params.get("source_task_map") or {}).get(task_id)
    if exact:
        candidates = [t for t in parent_tasks if getattr(t, "id", "") == exact]
    else:
        permitted = set(params.get("source_tasks") or [])
        candidates = [t for t in parent_tasks if getattr(t, "id", "") in permitted]
        candidates = [t for t in candidates if str(getattr(t, "item", "")) == item]
        color = _base_color(str(getattr(task, "color", "")))
        candidates = [t for t in candidates if _base_color(str(getattr(t, "color", ""))) == color]
        # Legacy rework lost view/garment fields. Multiple source views of the same
        # item are valid; unrelated garments cannot be chosen by positional guess.
        garment = str(getattr(task, "garment", ""))
        if garment:
            candidates = [t for t in candidates if str(getattr(t, "garment", "")) == garment]
        if view:
            candidates = [t for t in candidates if str(getattr(t, "view", "")) == view]
        elif len({str(getattr(t, "garment", "")) for t in candidates}) > 1:
            return []
    for candidate in candidates:
        found.extend(original_uploads(parent, candidate, get_job, max_depth=max_depth - 1, _seen=seen))
    return _unique(found)


def _unique(rows: list[OriginalUpload]) -> list[OriginalUpload]:
    result: list[OriginalUpload] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.path.resolve()).casefold()
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result
