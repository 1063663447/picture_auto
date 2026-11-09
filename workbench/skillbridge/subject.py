"""Output-subject choices and original-upload lineage.

Skin pixels can suggest a visible wearer but their absence cannot establish an
empty garment: back views, hoods, gloves, crops and grayscale images may expose
no skin. Auto therefore preserves the original presentation when uncertain;
only an explicit garment choice can require removing a wearer. The lightweight
skin heuristic remains useful for diagnostics and optional output warnings.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

GARMENT = "garment"
PERSON = "person"
AUTO = "auto"
KINDS = (AUTO, GARMENT, PERSON)
AUTO_POLICY = "preserve-original-v2"
LABELS = {
    AUTO: "自动判断",
    GARMENT: "只有衣服（平铺/挂拍）",
    PERSON: "真人上身 / 不确定",
}

#: 判定前缩到这个边长：2.5 万个像素以内，纯 Python 也够快
WORK_EDGE = 160

# Skin evidence is only a hint for keeping a visible wearer, never proof that
# an image without skin contains no person.
SKIN_ABSENT = 0.004
BLOB_ABSENT = 0.002

#: 成品复查：原图是空衣服，成品里却有大片「原背景区域变成皮肤色」→ 疑似又画了个人
ALERT_SKIN = 0.02

#: 皮肤色相（度）——深酒红/正红在 350~5°，不在区间内
HUE_RANGE = (4.0, 52.0)
SAT_RANGE = (0.14, 0.78)
VAL_RANGE = (0.20, 0.99)
#: 皮肤在 YCbCr 上的经典区间
CB_RANGE = (77, 132)
CR_RANGE = (130, 178)


def normalise(value: object) -> str:
    """把外部传入的 subject 归一到 garment / person / auto。"""
    text = str(value or "").strip().lower()
    if text in {GARMENT, PERSON}:
        return text
    if text in {"cloth", "flat", "flatlay", "empty", "no_person", "noperson", "衣服", "平铺"}:
        return GARMENT
    if text in {"model", "human", "worn", "on_model", "真人", "模特"}:
        return PERSON
    return AUTO


def describe(subject: str) -> str:
    return LABELS.get(normalise(subject), LABELS[AUTO])


def is_garment(subject: str) -> bool:
    return normalise(subject) == GARMENT


def _is_skin(r: int, g: int, b: int) -> bool:
    """HSV + YCbCr 双条件：暖色、不太艳、不太亮也不太暗。"""
    mx, mn = max(r, g, b), min(r, g, b)
    val = mx / 255.0
    if val < VAL_RANGE[0] or val > VAL_RANGE[1]:
        return False
    delta = mx - mn
    sat = 0.0 if mx == 0 else delta / mx
    if sat < SAT_RANGE[0] or sat > SAT_RANGE[1]:
        return False
    if delta == 0:
        hue = 0.0
    elif mx == r:
        hue = (60.0 * ((g - b) / delta)) % 360.0
    elif mx == g:
        hue = 60.0 * ((b - r) / delta) + 120.0
    else:
        hue = 60.0 * ((r - g) / delta) + 240.0
    if not (HUE_RANGE[0] <= hue <= HUE_RANGE[1]):
        return False
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return CB_RANGE[0] <= cb <= CB_RANGE[1] and CR_RANGE[0] <= cr <= CR_RANGE[1]


def _thumb(image: Image.Image, edge: int = WORK_EDGE) -> Image.Image:
    thumb = image.convert("RGB")
    width, height = thumb.size
    scale = min(1.0, edge / max(1, max(width, height)))
    if scale < 1.0:
        thumb = thumb.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))), Image.BILINEAR
        )
    return thumb


def _skin_grid(image: Image.Image) -> tuple[int, int, list[list[bool]]]:
    thumb = _thumb(image)
    width, height = thumb.size
    pixels = list(thumb.getdata())
    rows: list[list[bool]] = []
    for y in range(height):
        offset = y * width
        rows.append([_is_skin(*pixels[offset + x][:3]) for x in range(width)])
    return width, height, rows


def _largest_blob(width: int, height: int, rows: list[list[bool]]) -> float:
    """最大 4 邻域连通块占整图的比例。"""
    seen = [[False] * width for _ in range(height)]
    best = 0
    total = max(1, width * height)
    for y in range(height):
        for x in range(width):
            if not rows[y][x] or seen[y][x]:
                continue
            stack = [(x, y)]
            seen[y][x] = True
            size = 0
            while stack:
                cx, cy = stack.pop()
                size += 1
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if 0 <= nx < width and 0 <= ny < height and rows[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            if size > best:
                best = size
    return best / total


def _open(source: "str | Path | Image.Image") -> Image.Image | None:
    if isinstance(source, Image.Image):
        return source.convert("RGB")
    try:
        path = Path(source)
        if not path.is_file():
            return None
        with Image.open(path) as handle:
            return handle.convert("RGB")
    except Exception:  # noqa: BLE001 —— 判定失败不该让整单挂掉
        return None


def measure(source: "str | Path | Image.Image") -> dict:
    """量一张图的皮肤占比。返回 {skin, blob, size}；读不了就是 0。"""
    image = _open(source)
    if image is None:
        return {"skin": 0.0, "blob": 0.0, "size": (0, 0)}
    try:
        width, height, rows = _skin_grid(image)
        total = max(1, width * height)
        skin = sum(sum(1 for flag in row if flag) for row in rows) / total
        return {
            "skin": round(skin, 4),
            "blob": round(_largest_blob(width, height, rows), 4),
            "size": image.size,
        }
    except Exception:  # noqa: BLE001
        return {"skin": 0.0, "blob": 0.0, "size": image.size}


def detect(source: "str | Path | Image.Image", override: str = "") -> str:
    """Explicit choices win; absent skin leaves the source-preserving auto rule."""
    selected = normalise(override)
    if selected != AUTO:
        return selected
    stats = measure(source)
    if stats["skin"] > SKIN_ABSENT or stats["blob"] > BLOB_ABSENT:
        return PERSON
    return AUTO


def choice(job) -> str:
    """Read the operator choice separately from a resolved batch summary.

    Old planned jobs did not retain this distinction. Their aggregate is not
    evidence of an explicit override; retain their per-task decisions instead.
    """
    params = job.params
    if "subject_choice" in params:
        return normalise(params["subject_choice"])
    if job.tasks or "subjects" in params:
        return AUTO
    return normalise(params.get("subject"))


def load_lineage_job(job, job_id):
    """Load a sibling saved job for offline runner lineage; reject path escapes."""
    from .job import Job

    name = str(job_id or "")
    if not job.root or not name or any(c in name for c in ("/", "\\", ":", "\x00")) or name in {".", ".."}:
        raise KeyError(name)
    base = Path(job.root).parent.resolve()
    path = (base / name / "job.json").resolve()
    if path.parent.parent != base:
        raise KeyError(name)
    return Job.load(path)


def task_kind(job, task, get_job=None, *, max_depth: int = 16,
              _seen: frozenset = frozenset()) -> str:
    """Resolve stable task/ancestor evidence before looking at original pixels.

    Rework inputs are generated results and must never become subject evidence.
    Missing or ambiguous lineage falls back to a saved task decision, then auto.
    The existing conservative detector is used only on a true original upload.
    """
    from .sources import job_image_path, original_uploads

    key = (job.id, task.id)
    if max_depth <= 0 or key in _seen:
        return AUTO
    seen = _seen | {key}
    forced = choice(job)
    if forced != AUTO:
        return forced
    params = job.params
    decisions = params.get("task_subjects") or {}
    saved = normalise(decisions.get(task.id)) if isinstance(decisions, dict) else AUTO
    trusted = params.get("subject_policy") == AUTO_POLICY
    rework = params.get("kind") == "rework" or params.get("mode") == "rework" or params.get("source_job")
    if trusted and isinstance(decisions, dict) and task.id in decisions:
        return saved
    if not rework and saved != AUTO and (saved != GARMENT or trusted):
        return saved

    def lookup(job_id):
        if get_job is not None:
            try:
                return get_job(job_id)
            except Exception as exc:  # missing upstream jobs are optional lineage
                raise KeyError(job_id) from exc
        return load_lineage_job(job, job_id)

    rework = params.get("kind") == "rework" or params.get("mode") == "rework" or params.get("source_job")
    if rework:
        try:
            parent = lookup(params.get("source_job"))
            mapping = params.get("source_task_map") or {}
            exact = mapping.get(task.id) if isinstance(mapping, dict) else None
            candidates = [t for t in parent.tasks if t.id == exact] if exact else []
            if not exact:
                def base_color(color):
                    while color.endswith("-v2"):
                        color = color[:-3]
                    return color
                permitted = params.get("source_tasks") or []
                candidates = [t for t in parent.tasks if t.id in permitted and t.item == task.item
                              and base_color(t.color) == base_color(task.color)
                              and (not task.view or t.view == task.view)
                              and (not task.garment or t.garment == task.garment)]
            if len(candidates) == 1:
                inherited = task_kind(parent, candidates[0], lookup, max_depth=max_depth - 1, _seen=seen)
                if inherited != AUTO:
                    return inherited
            if len(candidates) > 1:
                inherited = {task_kind(parent, t, lookup, max_depth=max_depth - 1, _seen=seen)
                             for t in candidates}
                if len(inherited) == 1 and AUTO not in inherited:
                    return inherited.pop()
                # Conflicting possible parents cannot establish this task's subject.
                saved = normalise(task.subject)
                return saved if saved != GARMENT or trusted else AUTO
        except (KeyError, OSError, ValueError, TypeError):
            pass
        # Local original copies survive removal of upstream jobs. The first is
        # this task's canvas; later related views must not override its subject.
        originals = original_uploads(job, task, lookup, max_depth=max_depth)
        if originals:
            upload = originals[0]
            try:
                origin = lookup(upload.job_id)
                original_task = next(t for t in origin.tasks if t.id == upload.task_id)
                inherited = task_kind(origin, original_task, lookup, max_depth=max_depth - 1, _seen=seen)
                if inherited != AUTO:
                    return inherited
            except (KeyError, OSError, ValueError, TypeError, StopIteration):
                pass
            return detect(upload.path)
    saved = saved if saved != AUTO else normalise(task.subject)
    if saved != AUTO and (saved != GARMENT or trusted):
        return saved
    if not rework:
        original = job_image_path(job, task.source)
        if original is not None:
            return detect(original)
    return AUTO


def _border_color(image: Image.Image) -> tuple[int, int, int]:
    """取四条边的中位色当背景色（和 local_recolor 里的思路一致）。"""
    thumb = image.convert("RGB")
    width, height = thumb.size
    pixels = list(thumb.getdata())
    samples: list[tuple[int, int, int]] = []
    for x in range(width):
        samples.append(pixels[x][:3])
        samples.append(pixels[(height - 1) * width + x][:3])
    for y in range(height):
        samples.append(pixels[y * width][:3])
        samples.append(pixels[y * width + (width - 1)][:3])
    if not samples:
        return (255, 255, 255)
    channels = []
    for index in range(3):
        values = sorted(pixel[index] for pixel in samples)
        channels.append(values[len(values) // 2])
    return (channels[0], channels[1], channels[2])


def person_alert(source: "str | Path | Image.Image", output: "str | Path | bytes") -> dict:
    """成品复查：原图是空衣服，成品里**原背景区域**是否冒出大片皮肤色。

    只在「原来是背景、现在像皮肤」的位置统计，所以把衣服改成杏色/粉色不会误报
    （那些像素原本就在衣服上）。命中即 ``alert=True``，由 runner 记一条警告。
    """
    src = _open(source)
    if src is None:
        return {"alert": False, "skin": 0.0}
    # 原图本来就有真人 → 这条复查不适用（人物是原图的一部分）
    if detect(src) == PERSON:
        return {"alert": False, "skin": 0.0, "reason": "source-has-person"}
    if isinstance(output, (bytes, bytearray)):
        import io

        try:
            with Image.open(io.BytesIO(output)) as handle:
                out = handle.convert("RGB")
        except Exception:  # noqa: BLE001
            return {"alert": False, "skin": 0.0}
    else:
        out = _open(output)
        if out is None:
            return {"alert": False, "skin": 0.0}

    edge = WORK_EDGE
    src_small = _thumb(src, edge)
    out_small = _thumb(out, edge).resize(src_small.size, Image.BILINEAR)
    background = _border_color(src_small)
    src_pixels = list(src_small.getdata())
    out_pixels = list(out_small.getdata())
    total = max(1, len(src_pixels))
    hits = 0
    for index, pixel in enumerate(src_pixels):
        near_bg = all(abs(int(pixel[c]) - background[c]) <= 26 for c in range(3))
        if not near_bg:
            continue
        if _is_skin(*out_pixels[index][:3]):
            hits += 1
    ratio = hits / total
    return {"alert": ratio >= ALERT_SKIN, "skin": round(ratio, 4)}
