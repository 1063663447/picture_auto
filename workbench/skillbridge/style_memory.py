"""款式记忆：把「审核/对齐时确认过的要求」沉淀成该款式的 lessons，下次直接带上。

为什么要这个（2026-11-11 客户需求）：
生图流程改成「先出基准图 → 运营在对话框里确认或修改 → 按基准图改色」。
每次确认/修改其实都在告诉系统「这个款式到底要什么样」。这些结论如果只留在那一次任务里，
下次同款还要重新说一遍；沉淀到款式级之后，同一句话只说一次。

存储：就放在款式专属提示词文件里（`library/prompts/<slug>.json` 的 `lessons` 数组），
一个款式一份文件，运营打开就能看到它「记住了什么」。

每条 lesson：
    {
      "id": "20261111-a1b2c3",          # 稳定 id，删除/去重用
      "clause": "…English one-liner…",   # 直接注入提示词的那句
      "zh": "领口再小一点",               # 运营原话（人能对上）
      "source": {"job_id": "...", "task_id": "..."},
      "created_at": 1762...,
      "hits": 1                          # 被确认过几次（重复出现就累加，不重复注入）
    }
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import httpx

from .style_prompts import StylePrompt, load_style_prompt, prompt_path

MAX_LESSONS = 12
#: 一次注入提示词最多带几条（越新越靠前，太多会稀释重点）
INJECT_LIMIT = 6
MIN_CHARS = 3
MAX_CLAUSE_CHARS = 260

_DISTILL_SYSTEM = (
    "你在为服装电商生图工作台维护「款式级长期规则」。用户会给你一句运营在这次改图里"
    "确认过的要求（中文）。请把它变成一条可以直接写进英文生图提示词的约束句："
    "英文、祈使句、写清「这条款式以后都要这样」、不要提具体颜色/批次/图片编号。"
    "只输出这一句英文，不要解释，不要引号，不超过 40 个单词。"
)


def _root(library_or_skill) -> Path:
    """接受 Skill / Workbench.library_dir / Path，统一拿到 library 目录。"""
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


def load_lessons(library_or_skill, slug: str) -> list[dict]:
    """读一个款式记住的规则（按时间从新到旧）。"""
    prompt = load_style_prompt(_root(library_or_skill), slug)
    return list(prompt.lessons) if prompt is not None else []


def clauses(library_or_skill, slug: str, limit: int = INJECT_LIMIT) -> list[str]:
    """要注入提示词的英文约束句（新的在前，去重）。"""
    out: list[str] = []
    for item in load_lessons(library_or_skill, slug):
        clause = " ".join(str(item.get("clause") or "").split())
        if not clause or clause in out:
            continue
        out.append(clause)
        if len(out) >= max(1, limit):
            break
    return out


def _save_lessons(library_or_skill, slug: str, lessons: list[dict]) -> Path:
    root = _root(library_or_skill)
    prompt = load_style_prompt(root, slug)
    if prompt is None:
        prompt = StylePrompt(slug=slug)
    prompt.lessons = lessons[:MAX_LESSONS]
    path = prompt_path(root, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "slug": prompt.slug,
        "notes": prompt.notes,
        "positive": prompt.positive,
        "negative": prompt.negative,
    }
    if prompt.lessons:
        payload["lessons"] = prompt.lessons
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def distill_online(text: str, cfg: dict | None = None, timeout: float = 25.0) -> str:
    """用对话模型把中文要求翻成一句英文约束；失败返回空串（调用方回落）。"""
    cfg = cfg or {}
    base_url = str(cfg.get("base_url") or "").strip()
    model = str(cfg.get("model") or "").strip()
    key = str(cfg.get("api_key") or "").strip()
    if not (base_url and model and key):
        return ""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": _DISTILL_SYSTEM},
                        {"role": "user", "content": text.strip()},
                    ],
                },
            )
            if resp.status_code >= 400:
                return ""
            data = resp.json()
        content = str(
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        ).strip()
    except Exception:  # noqa: BLE001 - 沉淀是加分项，绝不阻塞审核
        return ""
    content = " ".join(content.strip().strip('"“”').split())
    if not content or len(content) > MAX_CLAUSE_CHARS:
        return ""
    return content


def remember(
    library_or_skill,
    slug: str,
    text: str,
    *,
    source: dict | None = None,
    llm_cfg: dict | None = None,
    online: bool | None = None,
) -> dict | None:
    """记一条款式规则：重复的只累加 hits，不重复注入。"""
    slug = str(slug or "").strip()
    zh = " ".join(str(text or "").split())
    if not slug or len(_normalise(zh)) < MIN_CHARS:
        return None
    lessons = load_lessons(library_or_skill, slug)
    key = _normalise(zh)
    for item in lessons:
        if _normalise(str(item.get("zh") or "")) == key or _normalise(
            str(item.get("clause") or "")
        ) == key:
            item["hits"] = int(item.get("hits") or 1) + 1
            item["last_confirmed_at"] = time.time()
            _save_lessons(library_or_skill, slug, lessons)
            return {**item, "slug": slug, "duplicate": True}
    use_online = True if online is None else bool(online)
    clause = distill_online(zh, llm_cfg) if use_online else ""
    if not clause:
        clause = f"Operator-confirmed rule for this style (apply it by default): {zh}"
    entry = {
        "id": _lesson_id(clause, zh),
        "clause": clause[:MAX_CLAUSE_CHARS],
        "zh": zh[:200],
        "source": dict(source or {}),
        "created_at": time.time(),
        "hits": 1,
    }
    lessons.insert(0, entry)
    _save_lessons(library_or_skill, slug, lessons)
    return {**entry, "slug": slug, "duplicate": False}


def forget(library_or_skill, slug: str, lesson_id: str) -> bool:
    lessons = load_lessons(library_or_skill, slug)
    kept = [item for item in lessons if str(item.get("id")) != str(lesson_id)]
    if len(kept) == len(lessons):
        return False
    _save_lessons(library_or_skill, slug, kept)
    return True


def rewrite_lessons(library_or_skill, slug: str, lessons: list[dict]) -> list[dict]:
    """整体替换（前端编辑/批量清理用）。"""
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
                "zh": zh[:200],
                "source": item.get("source") if isinstance(item.get("source"), dict) else {},
                "created_at": float(item.get("created_at") or time.time()),
                "hits": max(1, int(item.get("hits") or 1)),
            }
        )
    _save_lessons(library_or_skill, slug, clean)
    return clean
