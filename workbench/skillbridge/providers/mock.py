"""Offline mock provider: validates the whole pipeline without an API key.

It does not pretend to be a good image model, but it does return a real image of
the right size and color, so planning, concurrency, retry, file naming, the UI,
and ZIP packaging can all be exercised end to end (`provider = "mock"`).
"""

from __future__ import annotations

import asyncio
import io
import random
import re

from PIL import Image, ImageDraw, ImageOps

from .base import EditRequest, Provider, ProviderError


def _color_from_prompt(prompt: str) -> tuple[int, int, int]:
    match = re.search(r"#([0-9a-fA-F]{6})", prompt)
    if match:
        value = int(match.group(1), 16)
        return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF
    return (128, 128, 128)


class MockProvider(Provider):
    kind = "mock"
    label = "本地模拟（不发请求）"
    notes = "无需 API Key，用于验证并发、命名、打包流程；输出仅为占位图。"

    def __init__(self, name: str, cfg: dict, *, env: dict | None = None) -> None:
        super().__init__(name, cfg, env=env)
        self.max_images = 4
        self.supports_negative = False
        self.label = self.cfg.get("label") or self.label
        self.active = 0
        self.peak = 0

    def ready(self) -> tuple[bool, str]:
        return True, "本地模拟"

    async def list_models(self, timeout: float = 30.0) -> list[dict]:
        return [{"id": "mock", "owned_by": "local"}]

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            return await self._render(request)
        finally:
            self.active -= 1

    async def _render(self, request: EditRequest) -> bytes:
        delay = float(self.cfg.get("mock_delay_sec", 0.4))
        await asyncio.sleep(delay * (0.6 + random.random()))

        fail_rate = float(self.cfg.get("mock_fail_rate", 0.0))
        if fail_rate and random.random() < fail_rate:
            raise ProviderError(
                f"{self.name} 模拟 429 限流", status=429, retryable=True, provider=self.name
            )

        if not request.images:
            raise ProviderError("没有可用的输入图片", provider=self.name)

        with Image.open(request.images[0]) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        tint = _color_from_prompt(request.prompt)
        overlay = Image.new("RGB", image.size, tint)
        image = Image.blend(image, overlay, 0.28)

        draw = ImageDraw.Draw(image)
        label = next(
            (line for line in request.prompt.split() if line.startswith("#")),
            "#??????",
        )
        text = f"MOCK {label}"
        draw.rectangle([0, 0, min(image.width, 12 + 9 * len(text)), 26], fill=(0, 0, 0))
        draw.text((6, 7), text, fill=(255, 255, 255))

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
