"""每个款式一套专属默认提示词（款式级提示词库）。

背景（2026-11-10）：之前所有款式共用同一套「款式无关」的拼装逻辑，靠 garment 属性（袖长/袖口/
帽兜）分支。款式一多就不好维护：改 06 连帽卫衣的话不想影响 03 圆领卫衣，也想给每个款写自己的
细节（例如 21 童装索拉娜的落肩、28 卫衣童装的罗纹宽度）。

所以拆成两层：
* **共享骨架**（代码里，所有款式一致）：图案锁、材质锁、效果图/色卡分工、主体规则、结构限制
* **款式专属核心**（`library/prompts/<slug>.json`，一款一份，可独立编辑）：
  - `positive`：这段衣服「怎么建/怎么拆」的正向描述（版型、袖长、袖口工艺、帽绳…）
  - `negative`：这个款式专属的否定词
  - `notes`：给人看的备注

没有 json 的款式自动回落到「按属性拼装」的老逻辑，所以新增款式可以先跑起来再慢慢写。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PROMPT_DIR = "prompts"
MANIFEST = "index.json"


@dataclass
class StylePrompt:
    slug: str
    positive: str = ""
    negative: str = ""
    notes: str = ""
    source: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.positive.strip())

    def as_dict(self) -> dict:
        return {
            "slug": self.slug,
            "positive": self.positive,
            "negative": self.negative,
            "notes": self.notes,
            "source": self.source,
        }


def prompt_path(library_dir: Path | str, slug: str) -> Path:
    return Path(library_dir) / PROMPT_DIR / f"{slug}.json"


def load_style_prompt(library_dir: Path | str, slug: str) -> StylePrompt | None:
    """读单个款式的专属提示词；没有就返回 None（调用方回落到通用拼装）。"""
    path = prompt_path(library_dir, slug)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    prompt = StylePrompt(
        slug=str(payload.get("slug") or slug),
        positive=" ".join(str(payload.get("positive") or "").split()),
        negative=" ".join(str(payload.get("negative") or "").split()),
        notes=str(payload.get("notes") or ""),
        source=str(path),
    )
    return prompt if prompt.ready else None


def load_all(library_dir: Path | str) -> dict[str, StylePrompt]:
    """读全部款式专属提示词，返回 {slug: StylePrompt}。"""
    root = Path(library_dir) / PROMPT_DIR
    result: dict[str, StylePrompt] = {}
    if not root.is_dir():
        return result
    for path in sorted(root.glob("*.json")):
        prompt = load_style_prompt(library_dir, path.stem)
        if prompt is not None:
            result[path.stem] = prompt
    return result


def save_style_prompt(library_dir: Path | str, prompt: StylePrompt) -> Path:
    path = prompt_path(library_dir, prompt.slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "slug": prompt.slug,
        "notes": prompt.notes,
        "positive": prompt.positive,
        "negative": prompt.negative,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_index(library_dir: Path | str, prompts: dict[str, StylePrompt]) -> Path:
    """写一份人看的索引：哪些款式已经有专属提示词、备注是什么。"""
    path = Path(library_dir) / PROMPT_DIR / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": "每个款式一套专属默认提示词；缺文件的款式回落到按属性拼装",
        "styles": [
            {
                "slug": slug,
                "file": f"{slug}.json",
                "notes": prompt.notes,
                "positive_chars": len(prompt.positive),
                "negative_chars": len(prompt.negative),
            }
            for slug, prompt in sorted(prompts.items())
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
