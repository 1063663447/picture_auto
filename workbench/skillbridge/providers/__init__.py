"""Provider registry: build adapter instances from config.json."""

from __future__ import annotations

from .ark import ArkProvider
from .base import Provider
from .custom_json import CustomJsonProvider
from .fal import FalProvider
from .gemini import GeminiProvider
from .local_recolor import LocalRecolorProvider
from .mock import MockProvider
from .openai_compat import OpenAICompatProvider

KINDS = {
    "openai_compat": OpenAICompatProvider,
    "openai": OpenAICompatProvider,
    "gemini": GeminiProvider,
    "ark": ArkProvider,
    "volcengine": ArkProvider,
    "fal": FalProvider,
    "custom_json": CustomJsonProvider,
    "custom": CustomJsonProvider,
    "local_recolor": LocalRecolorProvider,
    "recolor_local": LocalRecolorProvider,
    "mock": MockProvider,
}


def build_providers(config: dict, env: dict) -> dict[str, Provider]:
    """Instantiate every configured provider, keyed by its config name."""
    providers: dict[str, Provider] = {}
    for name, cfg in (config.get("providers") or {}).items():
        if not isinstance(cfg, dict):
            continue
        kind = str(cfg.get("type") or cfg.get("kind") or name).lower()
        cls = KINDS.get(kind)
        if cls is None:
            raise KeyError(f"未知 provider 类型 `{kind}`（可用：{sorted(set(KINDS))}）")
        providers[name] = cls(name, cfg, env=env)
    if "mock" not in providers:
        providers["mock"] = MockProvider("mock", {"enabled": True}, env=env)
    return providers


__all__ = ["KINDS", "build_providers", "Provider"]
