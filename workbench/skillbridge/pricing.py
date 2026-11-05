"""Pricing and the usage ledger.

Facts that shaped this module:

* the gateway exposes **no** pricing endpoint (`/api/pricing` → 404), and
* its console prices images **per call**, some models **per output resolution tier**
  (1K / 2K / 4K) — e.g. `gpt-image-2-auto`: 1K $0.045, 2K $0.065, 4K $0.095.

So `pricing.json` holds, per model, either a flat number (per call), a `{tiers, other}`
object, or `null` for "未定价". A `null` is never estimated — the UI shows 未定价 and the
run is logged without cost — because a wrong number is worse than no number.

`data/usage.json` keeps one entry per finished job (idempotent per job id).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

WORKBENCH_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PRICING = WORKBENCH_DIR / "pricing.json"
DEFAULT_TIER_MP = {"1K": 1.6, "2K": 4.2}
TIERS = ("1K", "2K", "4K")


def tier_for(size: str | None, tier_mp: dict[str, float] | None = None) -> str:
    """Map a requested size (e.g. "1024x1536", "auto") to a pricing tier."""
    limits = dict(DEFAULT_TIER_MP)
    limits.update(tier_mp or {})
    pixels = 0
    if size and "x" in str(size).lower():
        try:
            width, height = (int(part) for part in str(size).lower().split("x", 1))
            pixels = width * height
        except ValueError:
            pixels = 0
    if pixels <= 0:  # "auto": the model follows the source, assume the cheapest tier
        return "1K"
    megapixels = pixels / 1_000_000
    if megapixels <= float(limits.get("1K", 1.6)):
        return "1K"
    if megapixels <= float(limits.get("2K", 4.2)):
        return "2K"
    return "4K"


@dataclass
class Price:
    model: str
    unit: float | None
    currency: str = "USD"
    tier: str = ""
    tiers: dict[str, float] | None = None
    fallback: bool = False

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "unit": self.unit,
            "currency": self.currency,
            "priced": self.unit is not None,
            "tier": self.tier,
            "tiers": self.tiers,
            "fallback": self.fallback,
            "label": self.label(),
        }

    def label(self) -> str:
        if self.unit is None:
            return "未定价"
        symbol = "$" if self.currency == "USD" else ("¥" if self.currency == "CNY" else "")
        if self.tiers:
            suffix = "/其他" if self.fallback else f"/{self.tier}"
        else:
            suffix = "/张"
        return f"{symbol}{self.unit:.3f}{suffix}"


class Pricer:
    def __init__(self, path: Path | None = None, currency: str = "") -> None:
        self.path = path or DEFAULT_PRICING
        self.currency = "USD"
        self.currency_override = (currency or "").strip().upper()
        self.note = ""
        self.updated = ""
        self.tier_mp = dict(DEFAULT_TIER_MP)
        self.raw: dict[str, object] = {}
        self.source = "file"
        self._feishu_at = 0.0
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            self.raw = {}
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.raw = {}
            return
        self.currency = self.currency_override or str(payload.get("currency") or "USD")
        self.note = str(payload.get("note") or "")
        self.updated = str(payload.get("updated") or "")
        if isinstance(payload.get("tier_mp"), dict):
            self.tier_mp = {str(k): float(v) for k, v in payload["tier_mp"].items()}
        self.raw = dict(payload.get("models") or {})

    # --------------------------------------------------------- 飞书定价表 --
    def refresh_from_feishu(self, cfg, ttl: float = 60.0, force: bool = False) -> bool:
        """以飞书「模型定价」表为准覆盖本地价目（可人工改）。失败保留原值。"""
        table_id = str(getattr(cfg, "price_table_id", "") or "")
        if cfg is None or not table_id or not getattr(cfg, "enabled", False):
            return False
        if not force and time.time() - self._feishu_at < ttl:
            return self.source == "feishu"
        ok, _ = cfg.ready()
        if not ok:
            return False
        try:
            from .feishu import FeishuBitable

            items = FeishuBitable(cfg).search_table(table_id)
        except Exception:  # noqa: BLE001 - 定价表读不到就用 pricing.json
            self._feishu_at = time.time()
            return False
        models = self.parse_feishu_rows(items)
        if models:
            self.raw = models
            self.currency = self.currency_override or "CNY"
            self.source = "feishu"
            self.note = "价格取自飞书「模型定价」表（可人工修改）"
        self._feishu_at = time.time()
        return self.source == "feishu"

    @staticmethod
    def parse_feishu_rows(items: list[dict]) -> dict[str, object]:
        def text(value) -> str:
            if value is None:
                return ""
            if isinstance(value, list):
                return "".join(
                    str(x.get("text") or "") if isinstance(x, dict) else str(x) for x in value
                )
            return str(value)

        def num(value) -> float | None:
            if value is None or value == "":
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        models: dict[str, object] = {}
        for item in items:
            fields = item.get("fields") or {}
            name = text(fields.get("模型")).strip()
            if not name:
                continue
            enabled_text = text(fields.get("启用")).strip().lower()
            if enabled_text in {"否", "no", "false", "0", "禁用", "off"}:
                continue
            tiers = {
                tier: num(fields.get(f"{tier}档"))
                for tier in TIERS
                if num(fields.get(f"{tier}档")) is not None
            }
            other = num(fields.get("其他档"))
            flat = num(fields.get("单价元"))
            if tiers:
                models[name] = {"tiers": tiers, "other": other}
            elif flat is not None:
                models[name] = flat
            else:
                models[name] = None
        return models

    def price(self, model: str, size: str | None = None) -> Price:
        entry = self.raw.get(model)
        tier = tier_for(size, self.tier_mp)
        if entry is None or entry is False or entry == "":
            return Price(model=model, unit=None, currency=self.currency, tier=tier)
        if isinstance(entry, (int, float)):
            return Price(model=model, unit=float(entry), currency=self.currency, tier=tier)
        if isinstance(entry, dict):
            tiers = entry.get("tiers") or {}
            tiers = {str(k): float(v) for k, v in tiers.items() if v is not None}
            unit = tiers.get(tier)
            fallback = False
            if unit is None:
                other = entry.get("other")
                unit = float(other) if other is not None else None
                fallback = unit is not None
            return Price(
                model=model,
                unit=unit,
                currency=self.currency,
                tier=tier if tiers else "",
                tiers=tiers or None,
                fallback=fallback,
            )
        return Price(model=model, unit=None, currency=self.currency, tier=tier)

    def estimate(self, model: str, count: int, size: str | None = None) -> dict:
        price = self.price(model, size)
        count = max(0, int(count))
        if price.unit is None:
            return {
                "model": model,
                "count": count,
                "unit": None,
                "total": None,
                "currency": price.currency,
                "priced": False,
                "tier": price.tier,
                "size": size or "",
            }
        return {
            "model": model,
            "count": count,
            "unit": price.unit,
            "total": round(price.unit * count, 4),
            "currency": price.currency,
            "priced": True,
            "tier": price.tier,
            "size": size or "",
        }

    def table(self) -> dict:
        return {
            "currency": self.currency,
            "unit": "per_call",
            "note": self.note,
            "updated": self.updated,
            "source": self.source,
            "tier_mp": self.tier_mp,
            "models": [
                self.price(name).as_dict() for name in sorted(self.raw)
            ],
        }


class UsageLedger:
    """Append-only spend log: data/usage.json."""

    def __init__(self, data_dir: Path, pricer: Pricer) -> None:
        self.path = Path(data_dir) / "usage.json"
        self.pricer = pricer
        self.entries: list[dict] = []
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            self.entries = []
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.entries = []
            return
        self.entries = list(payload.get("entries") or [])

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {"currency": self.pricer.currency, "entries": self.entries[-5000:]},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def record(
        self,
        *,
        job_id: str,
        model: str,
        images: int,
        kind: str = "generate",
        size: str | None = None,
        account: str = "",
        detail: str = "",
        garment: str = "",
        color: str = "",
    ) -> dict | None:
        if images <= 0:
            return None
        exists = next((e for e in self.entries if e.get("job_id") == job_id), None)
        if exists:  # idempotent: a retried job must not be billed twice
            return exists
        price = self.pricer.price(model, size)
        entry = {
            "job_id": job_id,
            "account": account or "anonymous",
            "model": model,
            "kind": kind,
            "images": int(images),
            "unit": price.unit,
            "tier": price.tier,
            "cost": (round(price.unit * images, 4) if price.unit is not None else None),
            "currency": price.currency,
            "detail": detail[:200],
            "garment": garment[:200],
            "color": color[:200],
            "at": time.time(),
        }
        self.entries.append(entry)
        self._save()
        return entry

    def by_account(self) -> list[dict]:
        """按账号聚合：做了多少张、花了多少钱、最近生成了什么。"""
        buckets: dict[str, dict] = {}
        for entry in self.entries:
            name = str(entry.get("account") or "anonymous")
            bucket = buckets.setdefault(
                name,
                {
                    "account": name,
                    "jobs": 0,
                    "images": 0,
                    "cost": 0.0,
                    "unpriced_images": 0,
                    "models": {},
                    "last_at": 0,
                    "recent": [],
                },
            )
            images = int(entry.get("images") or 0)
            cost = entry.get("cost")
            bucket["jobs"] += 1
            bucket["images"] += images
            if cost is None:
                bucket["unpriced_images"] += images
            else:
                bucket["cost"] += float(cost)
            model = str(entry.get("model") or "")
            bucket["models"][model] = bucket["models"].get(model, 0) + images
            at = float(entry.get("at") or 0)
            bucket["last_at"] = max(bucket["last_at"], at)
            bucket["recent"].append(
                {
                    "job_id": entry.get("job_id"),
                    "model": model,
                    "images": images,
                    "cost": cost,
                    "detail": entry.get("detail") or "",
                    "at": at,
                }
            )
        rows = []
        for bucket in buckets.values():
            bucket["cost"] = round(bucket["cost"], 4)
            bucket["recent"] = list(reversed(bucket["recent"][-10:]))
            bucket["models"] = sorted(
                ({"model": k, "images": v} for k, v in bucket["models"].items()),
                key=lambda item: -item["images"],
            )
            rows.append(bucket)
        return sorted(rows, key=lambda item: -item["images"])

    def summary(self) -> dict:
        today = time.strftime("%Y-%m-%d")
        total_images = 0
        total_cost = 0.0
        unpriced = 0
        day_images = 0
        day_cost = 0.0
        for entry in self.entries:
            images = int(entry.get("images") or 0)
            cost = entry.get("cost")
            total_images += images
            if cost is None:
                unpriced += images
            else:
                total_cost += float(cost)
            if time.strftime("%Y-%m-%d", time.localtime(entry.get("at") or 0)) == today:
                day_images += images
                if cost is not None:
                    day_cost += float(cost)
        return {
            "currency": self.pricer.currency,
            "today": {"images": day_images, "cost": round(day_cost, 4)},
            "total": {"images": total_images, "cost": round(total_cost, 4)},
            "unpriced_images": unpriced,
            "jobs": len(self.entries),
            "accounts": self.by_account(),
            "recent": list(reversed(self.entries[-20:])),
        }
