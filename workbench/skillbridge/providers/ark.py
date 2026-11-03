"""Volcengine Ark (火山方舟) 图片生成 / 图片编辑 adapter.

Covers `doubao-seededit-*` (单图编辑) and `doubao-seedream-4-0-*` (多图参考 + 编辑).
"""

from __future__ import annotations

import httpx

from .base import (
    EditRequest,
    Provider,
    ProviderError,
    data_url,
    decode_b64_image,
    http_error,
)


class ArkProvider(Provider):
    kind = "ark"
    label = "火山方舟 Seedream / SeedEdit"
    notes = (
        "JSON 提交，image 传 data URL。Seedream 4.0 支持多图参考（multi_image=true 时传数组，"
        "原图在前、颜色参考在后）；SeedEdit 只接受单图，颜色改写由提示词中的 sRGB 值约束。"
    )

    def __init__(self, name: str, cfg: dict, *, env: dict | None = None) -> None:
        super().__init__(name, cfg, env=env)
        self.multi_image = bool(self.cfg.get("multi_image", True))
        self.max_images = int(self.cfg.get("max_images", 2 if self.multi_image else 1))
        self.supports_negative = False
        self.label = self.cfg.get("label") or self.label

    @property
    def endpoint(self) -> str:
        base = str(
            self.cfg.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3"
        ).rstrip("/")
        if base.endswith("/images/generations"):
            return base
        return f"{base}/images/generations"

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        images = self.images_for(request)
        if not images:
            raise ProviderError("没有可用的输入图片", provider=self.name)

        body: dict = {
            "model": self.model_name(request) or "doubao-seedream-4-0-250828",
            "prompt": request.prompt,
            "response_format": str(self.cfg.get("response_format", "b64_json")),
            "watermark": bool(self.cfg.get("watermark", False)),
        }
        urls = [data_url(path) for path in images]
        as_b64 = str(self.cfg.get("image_encoding", "data_url")).lower() == "base64"
        payload_images = [u.split(",", 1)[1] for u in urls] if as_b64 else urls
        if self.multi_image and len(payload_images) > 1:
            body["image"] = payload_images
        else:
            body["image"] = payload_images[0]

        size = request.size or str(self.cfg.get("size") or "adaptive")
        body["size"] = "adaptive" if size == "auto" else size
        if self.cfg.get("sequential_image_generation"):
            body["sequential_image_generation"] = str(self.cfg["sequential_image_generation"])
        if request.seed is not None:
            body["seed"] = request.seed
        body.update({str(k): v for k, v in (self.cfg.get("extra_body") or {}).items()})

        headers = {
            "Authorization": f"Bearer {self.api_key()}",
            "Content-Type": "application/json",
        }
        headers.update({str(k): str(v) for k, v in (self.cfg.get("extra_headers") or {}).items()})

        try:
            response = await self.client.post(
                self.endpoint, json=body, headers=headers, timeout=self.timeout_of(timeout)
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"{self.name} 请求超时（>{timeout:.0f}s）", retryable=True, provider=self.name
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"{self.name} 网络错误: {exc}", retryable=True, provider=self.name
            ) from exc

        if response.status_code >= 400:
            raise http_error(self.name, response)

        payload = response.json()
        data = payload.get("data") or []
        if not data:
            raise ProviderError(
                f"{self.name} 返回缺少 data: {str(payload)[:400]}", provider=self.name
            )
        first = data[0]
        if first.get("b64_json"):
            return decode_b64_image(str(first["b64_json"]))
        if first.get("url"):
            return await self._download(str(first["url"]), timeout)
        if first.get("image_url"):
            return await self._download(str(first["image_url"]), timeout)
        raise ProviderError(f"{self.name} 返回结构无法识别: {str(first)[:300]}", provider=self.name)
