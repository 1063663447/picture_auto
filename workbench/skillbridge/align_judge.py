"""对齐需求的「重要性评判」：决定这条要求该记多久、记到哪一层。

背景（2026-11-11 客户要求）：
> 对齐的需求拍板后（用户确定基准图没问题、可以改色），harness 后台静默评估本次需求对齐的
> 重要性（有的只是针对当前衣服微调，有的是原 skill 有问题导致款式/图案跑偏）来沉淀自身记忆
> 和 skill；沉淀后每次生图有对应款式时，要在对应款式的生图 skill、上下文、记忆里运用，
> 尽量降低基准图的返工率。

所以这里做三件事：
  1. **分类**：这条要求是「这一张的一次性微调」还是「这个款式本身的规则缺陷」；
  2. **定级**：给出 importance（0~1）与类别（版型/印花/颜色/面料/画面/其它）；
  3. **给结论**：`promote=True` 的升级成款式级硬规则（写进该款式的 skill 段），
     否则只作为「记录/软提示」，不注入提示词。

判定分两步：先用启发式（免费、毫秒级、结果稳定），
再可选让 harness 的对话模型复核（`online=True`，失败自动回落启发式）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

#: 类别：决定这条要求落在生图提示词的哪一段
KINDS = {
    "garment": "版型/构造（袖长、领口、下摆、肩线、帽绳、衣长）",
    "artwork": "图案/文字（字形、位置、大小、风格、是否可重画）",
    "colour": "颜色（色相、明度、饱和度、撞色件）",
    "material": "面料质感（厚薄、垂坠、光泽、纹理）",
    "scene": "画面（背景、光、构图、裁切、机位）",
    "other": "其它",
}

#: 一眼就能看出「这个款式都该这样」的词（款式级缺陷）
STYLE_HINTS: list[tuple[tuple[str, ...], str, float]] = [
    (("袖长", "长袖", "短袖", "袖子", "罗纹", "袖口", "领口", "领形", "领型", "下摆", "衣长",
      "版型", "剪裁", "肩线", "落肩", "收口", "帽绳", "帽子", "帽兜", "口袋", "开襟", "门襟"),
     "garment", 0.82),
    (("印花", "图案", "文字", "logo", "标", "图案跑", "重画", "错位", "风格", "水墨", "扎染",
      "满铺", "配色"),
     "artwork", 0.78),
    (("颜色", "色差", "偏色", "偏亮", "偏暗", "太亮", "太暗", "发灰", "串色", "同色系"),
     "colour", 0.66),
    (("面料", "材质", "厚", "薄", "垂坠", "光泽", "丝", "棉", "卫衣布", "抓绒"),
     "material", 0.7),
    (("背景", "光", "机位", "裁切", "构图", "比例", "留白", "边缘"),
     "scene", 0.4),
]

#: 明显是「这一次」的词：不升级成款式规则
ONE_OFF_HINTS = (
    "这张", "这一张", "这版", "这个版本", "本次", "这批图里", "刚才那张", "上面那张",
    "刚才那版", "这一批的这张", "临时", "先这样", "这次先",
)

#: 明显是「通用/款式」的词：加强升级
GENERAL_HINTS = ("所有", "每次", "以后", "统一", "一律", "所有颜色", "所有款", "固定", "标准",
                 "总是", "老是", "一直", "别再", "不要再")

#: 影响 importance 的强否定/纠错词：说明是「跑偏」而不是「口味」
DRIFT_HINTS = ("不是这个", "跑偏", "又错", "还是错", "总是", "老是", "根本不是", "画错",
               "不对", "串了", "变成了", "变成别的", "漏了", "多出来")

_JUDGE_SYSTEM = (
    "你在维护服装电商生图工作台的「款式级长期规则」。给你一条运营在本次改图里确认过的要求，"
    "你要判断它是不是这个款式本身的通病（而不是这一张的一次性微调）。\n"
    "只输出一个 JSON 对象，字段：\n"
    '  "scope": "style" 或 "one_off"\n'
    '  "kind": "garment" | "artwork" | "colour" | "material" | "scene" | "other"\n'
    '  "importance": 0~1 的数字（越大越应该写进款式规则）\n'
    '  "promote": true 或 false（true = 以后这个款式都按这条约束）\n'
    '  "clause": 一句英文祈使句约束（≤40 词，具体、可执行，不提具体颜色/批次/图片编号）；'
    'scope=one_off 时给空字符串\n'
    '  "negative": 一句英文否定约束（≤30 词，可选，没有就给空字符串）\n'
    '  "reason": 一句中文说明你的判断依据（≤30 字）\n'
    "判断原则：像「袖长不对/领口太大/罗纹收口画成了平口/印花总是跑偏/材质画成棉」这类"
    "是这个款式的通病 → style + promote；像「这张裁切再往下一点/这张的图更亮一点」"
    "只是这一张的微调 → one_off。拿不准时偏向 one_off（噪声小比记错好）。"
)


@dataclass
class Verdict:
    """一条对齐要求的评判结果。"""

    text: str
    scope: str = "one_off"          # style | one_off
    kind: str = "other"
    importance: float = 0.0
    promote: bool = False
    clause: str = ""
    negative: str = ""
    reason: str = ""
    refiner: str = "local"          # local | llm

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "scope": self.scope,
            "kind": self.kind,
            "importance": round(self.importance, 2),
            "promote": self.promote,
            "clause": self.clause,
            "negative": self.negative,
            "reason": self.reason,
            "refiner": self.refiner,
        }


def _hits(text: str, words: tuple[str, ...]) -> int:
    low = text.lower()
    return sum(1 for word in words if word.lower() in low)


def judge_local(text: str, *, garment_phrase: str = "") -> Verdict:
    """启发式评判：不花钱、毫秒级、结果稳定。"""
    body = " ".join(str(text or "").split())
    if not body:
        return Verdict(text="", reason="空要求")
    kind, base = "other", 0.35
    for words, candidate, weight in STYLE_HINTS:
        score = _hits(body, words)
        if score and weight > base:
            kind, base = candidate, weight
    one_off = _hits(body, ONE_OFF_HINTS)
    general = _hits(body, GENERAL_HINTS)
    drift = _hits(body, DRIFT_HINTS)
    importance = base + 0.08 * min(2, general) + 0.06 * min(2, drift) - 0.35 * min(2, one_off)
    importance = max(0.05, min(0.98, importance))
    style_level = importance >= 0.55 and one_off == 0
    return Verdict(
        text=body,
        scope="style" if style_level else "one_off",
        kind=kind,
        importance=importance,
        promote=style_level and importance >= 0.7,
        clause="",                      # 本地不写英文，交给调用方回落成「原话」
        negative="",
        reason=(
            f"命中{'款式级'}线索，判为{'款式规则' if style_level else '一次性微调'}"
            if (general or drift or kind != "other")
            else "没有明显款式级线索，按一次性处理"
        ),
        refiner="local",
    )


def judge_online(
    text: str,
    *,
    cfg: dict | None = None,
    garment_phrase: str = "",
    timeout: float = 20.0,
) -> Verdict | None:
    """让 harness 的对话模型复核；返回 None 表示不可用（调用方回落本地判定）。"""
    cfg = cfg or {}
    base_url = str(cfg.get("base_url") or "").strip()
    model = str(cfg.get("model") or "").strip()
    key = str(cfg.get("api_key") or "").strip()
    if not (base_url and model and key):
        return None
    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "temperature": 0.1,
                    "messages": [
                        {"role": "system", "content": _JUDGE_SYSTEM},
                        {
                            "role": "user",
                            "content": f"目标款式：{garment_phrase or '（未给）'}\n本次确认的要求：{text}",
                        },
                    ],
                },
            )
            if resp.status_code >= 400:
                return None
            data = resp.json()
        content = str(
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        ).strip()
    except Exception:  # noqa: BLE001 - 评判失败就回落，绝不影响出图
        return None
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    scope = "style" if str(payload.get("scope") or "").lower() == "style" else "one_off"
    kind = str(payload.get("kind") or "other").lower()
    if kind not in KINDS:
        kind = "other"
    try:
        importance = float(payload.get("importance") or 0)
    except (TypeError, ValueError):
        importance = 0.5
    clause = " ".join(str(payload.get("clause") or "").split())[:260]
    negative = " ".join(str(payload.get("negative") or "").split())[:200]
    promote = bool(payload.get("promote")) and scope == "style"
    if scope == "style" and not clause:
        # 判成款式级却没给英文约束 → 当作不可用，回落本地
        return None
    return Verdict(
        text=" ".join(str(text or "").split()),
        scope=scope,
        kind=kind,
        importance=max(0.0, min(1.0, importance)),
        promote=promote,
        clause=clause,
        negative=negative,
        reason=" ".join(str(payload.get("reason") or "").split())[:80],
        refiner="llm",
    )


def judge(
    text: str,
    *,
    cfg: dict | None = None,
    garment_phrase: str = "",
    online: bool | None = None,
) -> Verdict:
    """先本地判，再（可选）让在线模型复核；任何失败都回落本地。"""
    local = judge_local(text, garment_phrase=garment_phrase)
    if online is False:
        return local
    remote = judge_online(text, cfg=cfg, garment_phrase=garment_phrase)
    if remote is None:
        return local
    # 在线模型说是一次性 → 尊重；说款式级但本地完全没有线索（scope=one_off 且无量级线索）
    # 时，要求它同时给出 clause，否则仍按本地（避免噪声）
    if remote.scope == "one_off":
        return remote
    if not remote.clause:
        return local
    return remote


@dataclass
class DistillResult:
    """一次「拍板」之后沉淀的汇总。"""

    verdicts: list[Verdict] = field(default_factory=list)

    @property
    def promoted(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.promote]

    @property
    def remembered(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.scope == "style"]

    @property
    def dropped(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.scope != "style"]

    def as_dict(self) -> dict:
        return {
            "total": len(self.verdicts),
            "promoted": len(self.promoted),
            "remembered": len(self.remembered),
            "dropped": len(self.dropped),
            "items": [v.as_dict() for v in self.verdicts],
        }
