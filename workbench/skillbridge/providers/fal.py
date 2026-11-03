"""fal.ai adapter (flux-kontext / qwen-image-edit / seedream on fal)."""

from __future__ import annotations

import httpx

from .base import EditRequest, Provider, ProviderError, data_url, http_error


class FalProvider(Provider):
    kind = "fal"
    label = "fal.ai 图片编辑"
    notes = "同步 `fal.run` 接口，image_url 传 data URL；多图模型用 image_urls 字段。"

    def __init__(self, name: str, cfg: dict, *, env: dict | None = None) -> None:
        super().__init__(name, cfg, env=env)
        self.max_images = int(self.cfg.get("max_images", 1))
        self.supports_negative = False
        self.label = self.cfg.get("label") or self.label

    def endpoint_for(self, request: EditRequest | None) -> str:
        base = str(self.cfg.get("base_url") or "https://fal.run").rstrip("/")
        model = self.model_name(request) or "fal-ai/flux-kontext/dev"
        return f"{base}/{model.lstrip('/')}"

    @property
    def endpoint(self) -> str:
        return self.endpoint_for(None)

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        images = self.images_for(request)
        if not images:
            raise ProviderError("没有可用的输入图片", provider=self.name)

        urls = [data_url(path) for path in images]
        body: dict = {
            "prompt": request.prompt,
            "num_images": 1,
            "output_format": str(self.cfg.get("output_format", "png")),
            "sync_mode": bool(self.cfg.get("sync_mode", True)),
        }
        if len(urls) > 1:
            body[str(self.cfg.get("images_field", "image_urls"))] = urls
        else:
            body["image_url"] = urls[0]
        if request.seed is not None:
            body["seed"] = request.seed
        body.update({str(k): v for k, v in (self.cfg.get("extra_body") or {}).items()})

        headers = {"Authorization": f"Key {self.api_key()}"}
        headers.update({str(k): str(v) for k, v in (self.cfg.get("extra_headers") or {}).items()})

        try:
            response = await self.client.post(
                self.endpoint_for(request), json=body, headers=headers,
                timeout=self.timeout_of(timeout)
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
        candidates = payload.get("images") or payload.get("data") or []
        for item in candidates:
            if isinstance(item, dict) and item.get("url"):
                return await self._download(str(item["url"]), timeout)
            if isinstance(item, dict) and item.get("b64_json"):
                from .base import decode_b64_image

                return decode_b64_image(str(item["b64_json"]))
        raise ProviderError(
            f"{self.name} 返回结构无法识别: {str(payload)[:300]}", provider=self.name
        )
