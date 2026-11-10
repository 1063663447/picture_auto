# -*- coding: utf-8 -*-
"""款式记忆升级：区分「款式级硬规则（promote）」与「一次性微调（不注入）」。

字段（每条 lesson）：
    id / clause / zh / source / created_at / hits
    scope      style | one_off      —— 是不是这个款式的通病
    kind       garment | artwork | colour | material | scene | other
    importance 0~1                  —— 由 align_judge 评判
    promote    bool                 —— true = 升级成款式级硬规则（写进 skill 段）
    judge      str                  —— 判定来源 local | llm
    reason     str                  —— 人看的一句话依据

另外每个款式维护 `stats`：{batches, approves, review_rounds, promoted, learned}
用来观察「基准图返工率」有没有往下走。
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from .style_prompts import StylePrompt, load_style_prompt, prompt_path

MAX_LESSONS = 12
#: 注入提示词的条数上限（promote 的硬规则单独算一组）
INJECT_LIMIT = 6
PROMOTE_LIMIT = 4
MIN_CHARS = 3
MAX_CLAUSE_CHARS = 260


def _root(library_or_skill) -> Path:
    for attr in ("library_dir", "root"):
        value = getattr(library_or_skill, attr, None)
        if isinstance(value, (str, Path)):
            path = Path(value)
            if (path / "prompts").is_dir() or path.name == "library":
                return path
    if isinstance(library_or_skill, (str, Path)):
        return Path(library_or_skill)
    raise TypeError("无法确定 library 目录")


def _normalise(text: str) -> str:
    return re.sub(r"[\s，。,.、;；!！?？\"'“”‘’()（）]+", "", str(text or "")).strip()


def _lesson_id(clause: str, zh: str) -> str:
    digest = hashlib.sha1(f"{_normalise(clause)}|{_normalise(zh)}".encode("utf-8")).hexdigest()
    return f"{time.strftime('%Y%m%d')}-{digest[:8]}"


def load_style(library_or_skill, slug: str) -> dict:
    """读一个款式的记忆文件（含 lessons 与 stats）。"""
    root = _root(library_or_skill)
    prompt = load_style_prompt(root, slug)
    if prompt is None:
        return {"lessons": [], "stats": {}}
    return {"lessons": list(prompt.lessons), "stats": dict(getattr(prompt, "stats", {}) or {})}


def load_lessons(library_or_skill, slug: str) -> list[dict]:
    return load_style(library_or_skill, slug)["lessons"]


def load_stats(library_or_skill, slug: str) -> dict:
    return load_style(library_or_skill, slug)["stats"]


def clauses(library_or_skill, slug: str, *, promoted: bool = False, limit: int = 0) -> list[str]:
    """要注入提示词的英文约束句。

    ``promoted=True`` 只取升级成款式级硬规则的那些（写在提示词前面），
    ``promoted=False`` 取普通的「学到的规则」（写在后面）。
    """
    out: list[str] = []
    cap = limit or (PROMOTE_LIMIT if promoted else INJECT_LIMIT)
    for item in load_lessons(library_or_skill, slug):
        if bool(item.get("promote")) is not bool(promoted):
            continue
        if str(item.get("scope") or "style") == "one_off":
            continue
        clause = " ".join(str(item.get("clause") or "").split())
        if not clause or clause in out:
            continue
        out.append(clause)
        if len(out) >= max(1, cap):
            break
    return out


def _save(library_or_skill, slug: str, lessons: list[dict], stats: dict) -> Path:
    root = _root(library_or_skill)
    prompt = load_style_prompt(root, slug) or StylePrompt(slug=slug)
    prompt.lessons = lessons[:MAX_LESSONS]
    prompt.stats = dict(stats or {})
    path = prompt_path(root, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "slug": prompt.slug,
        "notes": prompt.notes,
        "positive": prompt.positive,
        "negative": prompt.negative,
    }
    if prompt.lessons:
        payload["lessons"] = prompt.lessons
    if prompt.stats:
        payload["stats"] = prompt.stats
    path.write_text(
        __import__("json").dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def bump_stats(library_or_skill, slug: str, **fields) -> dict:
    """累加款式统计（批次 / 确认次数 / 返工轮次 / 沉淀条数）。"""
    data = load_style(library_or_skill, slug)
    stats = dict(data["stats"] or {})
    for key in ("batches", "approves", "review_rounds", "promoted", "learned"):
        stats.setdefault(key, 0)
    for key, value in fields.items():
        try:
            stats[key] = int(stats.get(key) or 0) + int(value)
        except (TypeError, ValueError):
            stats[key] = value
    stats["updated_at"] = time.strftime("%Y-%m-%d %H:%M")
    _save(library_or_skill, slug, data["lessons"], stats)
    return stats


def remember(
    library_or_skill,
    slug: str,
    text: str,
    *,
    verdict=None,
    source: dict | None = None,
    llm_cfg: dict | None = None,
    online: bool | None = None,
) -> dict | None:
    """记一条款式规则。

    ``verdict``（align_judge.Verdict）给了就以它为准：
      * scope=one_off → 仍然写盘（留档），但 `promote=False / scope=one_off`，不注入提示词；
      * scope=style  → 注入；`promote=True` 的再升级成款式级硬规则。
    没给 verdict 的老调用方式保持不变（按款式级、不升级处理）。
    """
    slug = str(slug or "").strip()
    zh = " ".join(str(text or "").split())
    if not slug or len(_normalise(zh)) < MIN_CHARS:
        return None
    data = load_style(library_or_skill, slug)
    lessons = list(data["lessons"])
    key = _normalise(zh)
    for item in lessons:
        if _normalise(str(item.get("zh") or "")) == key or _normalise(
            str(item.get("clause") or "")
        ) == key:
            item["hits"] = int(item.get("hits") or 1) + 1
            item["last_confirmed_at"] = time.time()
            if verdict is not None and verdict.promote:
                item["promote"] = True
                if str(verdict.clause or "").strip():
                    item["clause"] = str(verdict.clause)[:MAX_CLAUSE_CHARS]
            _save(library_or_skill, slug, lessons, data["stats"])
            return {**item, "slug": slug, "duplicate": True}

    clause = ""
    negative = ""
    scope = "style"
    kind = "other"
    importance = 0.6
    promote = False
    judge = "manual"
    reason = ""
    if verdict is not None:
        clause = " ".join(str(verdict.clause or "").split())
        negative = " ".join(str(verdict.negative or "").split())
        scope = verdict.scope
        kind = verdict.kind
        importance = float(verdict.importance or 0)
        promote = bool(verdict.promote)
        judge = verdict.refiner
        reason = verdict.reason
    if not clause:
        clause = f"Operator-confirmed rule for this style (apply it by default): {zh}"
    entry = {
        "id": _lesson_id(clause, zh),
        "clause": clause[:MAX_CLAUSE_CHARS],
        "negative": negative[:200],
        "zh": zh[:200],
        "scope": scope,
        "kind": kind,
        "importance": round(importance, 2),
        "promote": promote,
        "judge": judge,
        "reason": reason,
        "source": dict(source or {}),
        "created_at": time.time(),
        "hits": 1,
    }
    # 升级成硬规则的排前面，其余按时间倒序
    lessons.insert(0, entry)
    if promote:
        lessons.sort(key=lambda item: (0 if item.get("promote") else 1, -float(item.get("created_at") or 0)))
    _save(library_or_skill, slug, lessons, data["stats"])
    return {**entry, "slug": slug, "duplicate": False}


def forget(library_or_skill, slug: str, lesson_id: str) -> bool:
    data = load_style(library_or_skill, slug)
    lessons = data["lessons"]
    kept = [item for item in lessons if str(item.get("id")) != str(lesson_id)]
    if len(kept) == len(lessons):
        return False
    _save(library_or_skill, slug, kept, data["stats"])
    return True


def rewrite_lessons(library_or_skill, slug: str, lessons: list[dict]) -> list[dict]:
    data = load_style(library_or_skill, slug)
    clean: list[dict] = []
    for item in lessons or []:
        if not isinstance(item, dict):
            continue
        clause = " ".join(str(item.get("clause") or "").split())
        zh = " ".join(str(item.get("zh") or "").split())
        if not clause and not zh:
            continue
        clean.append(
            {
                "id": str(item.get("id") or _lesson_id(clause, zh)),
                "clause": (clause or f"Operator-confirmed rule: {zh}")[:MAX_CLAUSE_CHARS],
                "negative": str(item.get("negative") or "")[:200],
                "zh": zh[:200],
                "scope": str(item.get("scope") or "style"),
                "kind": str(item.get("kind") or "other"),
                "importance": float(item.get("importance") or 0.6),
                "promote": bool(item.get("promote")),
                "judge": str(item.get("judge") or "manual"),
                "reason": str(item.get("reason") or "")[:80],
                "source": item.get("source") if isinstance(item.get("source"), dict) else {},
                "created_at": float(item.get("created_at") or time.time()),
                "hits": max(1, int(item.get("hits") or 1)),
            }
        )
    _save(library_or_skill, slug, clean, data["stats"])
    return clean


def distill_online(text: str, cfg: dict | None = None, timeout: float = 25.0) -> str:
    """兼容老接口：把中文要求翻成一句英文约束（不做重要性判断）。"""
    from .align_judge import judge_online

    verdict = judge_online(text, cfg=cfg, timeout=timeout)
    return verdict.clause if verdict is not None else ""
