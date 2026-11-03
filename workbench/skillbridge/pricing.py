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

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "unit": self.unit,
            "currency": self.currency,
            "priced": self.unit is not None,
            "tier": self.tier,
            "tiers": self.tiers,
        }

    def label(self) -> str:
        if self.unit is None:
            return "未定价"
        symbol = "$" if self.currency == "USD" else ("¥" if self.currency == "CNY" else "")
        suffix = f"/{self.tier}" if self.tiers else "/张"
        return f"{symbol}{self.unit:g}{suffix}"


class Pricer:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_PRICING
        self.currency = "USD"
        self.note = ""
        self.updated = ""
        self.tier_mp = dict(DEFAULT_TIER_MP)
        self.raw: dict[str, object] = {}
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
        self.currency = str(payload.get("currency") or "USD")
        self.note = str(payload.get("note") or "")
        self.updated = str(payload.get("updated") or "")
        if isinstance(payload.get("tier_mp"), dict):
            self.tier_mp = {str(k): float(v) for k, v in payload["tier_mp"].items()}
        self.raw = dict(payload.get("models") or {})

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
            if unit is None:
                other = entry.get("other")
                unit = float(other) if other is not None else None
            return Price(
                model=model,
                unit=unit,
                currency=self.currency,
                tier=tier if tiers else "",
                tiers=tiers or None,
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
    ) -> dict | None:
        if images <= 0:
            return None
        exists = next((e for e in self.entries if e.get("job_id") == job_id), None)
        if exists:  # idempotent: a retried job must not be billed twice
            return exists
        price = self.pricer.price(model, size)
        entry = {
            "job_id": job_id,
            "model": model,
            "kind": kind,
            "images": int(images),
            "unit": price.unit,
            "tier": price.tier,
            "cost": (round(price.unit * images, 4) if price.unit is not None else None),
            "currency": price.currency,
            "at": time.time(),
        }
        self.entries.append(entry)
        self._save()
        return entry

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
            "recent": list(reversed(self.entries[-20:])),
        }
