"""OpenAI-compatible `/v1/images/edits` adapter.

This is the workhorse adapter: almost every third-party image gateway /
中转 API (OpenAI, Azure gateways, one-api / new-api, 硅基流动, 云雾, 302.ai ...)
exposes the same multipart shape, and `gpt-image-1` / `imagen` /
`nano-banana` / `seedream` are frequently re-exported through it.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from .base import (
    EditRequest,
    Provider,
    ProviderError,
    decode_b64_image,
    http_error,
    mime_for,
)


class OpenAICompatProvider(Provider):
    kind = "openai_compat"
    label = "OpenAI 兼容 /v1/images/edits"
    notes = (
        "multipart 上传：image[] + prompt。gpt-image-1 / 各类 one-api 中转站通用；"
        "若网关只接受单个 image 字段，把 config 里 image_field 改为 image。"
    )

    def __init__(self, name: str, cfg: dict, *, env: dict | None = None) -> None:
        super().__init__(name, cfg, env=env)
        self.max_images = int(self.cfg.get("max_images", 4))
        self.supports_negative = bool(self.cfg.get("supports_negative", False))
        self.label = self.cfg.get("label") or self.label
        presets = self.cfg.get("auto_size_presets")
        self.size_presets = [str(p) for p in presets] if presets else None

    @property
    def base(self) -> str:
        base = str(self.cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        for suffix in ("/images/edits", "/images/generations"):
            if base.endswith(suffix):
                return base[: -len(suffix)]
        return base

    @property
    def endpoint(self) -> str:
        base = str(self.cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        if base.endswith("/images/edits") or base.endswith("/images/generations"):
            return base
        return f"{base}/images/edits"

    def models_url(self) -> str | None:
        if self.cfg.get("models_url"):
            return str(self.cfg["models_url"])
        if self.cfg.get("no_model_list"):
            return None
        return f"{self.base}/models"

    def _form(self, request: EditRequest) -> dict[str, str]:
        cfg = self.cfg
        form: dict[str, str] = {
            "model": self.model_name(request) or "gpt-image-1",
            "prompt": request.prompt,
            "n": "1",
        }
        if request.size and request.size != "auto":
            form["size"] = request.size
        elif cfg.get("send_size_auto"):
            form["size"] = "auto"
        # 上游只收某些 quality 的模型（grok 系列只认 low/medium/auto）：
        # 这里按模型收敛一次，避免 "upstream rejected the request" 直接失败。
        quality = self.quality_for(self.model_name(request), request.quality)
        if quality and quality != "auto":
            form["quality"] = quality
        response_format = cfg.get("response_format", "b64_json")
        if response_format:
            form["response_format"] = str(response_format)
        if cfg.get("background"):
            form["background"] = str(cfg["background"])
        if cfg.get("output_format"):
            form["output_format"] = str(cfg["output_format"])
        if request.seed is not None and cfg.get("send_seed", True):
            form["seed"] = str(request.seed)
        fidelity = request.input_fidelity or cfg.get("input_fidelity")
        if fidelity:
            form["input_fidelity"] = str(fidelity)
        if request.negative and self.supports_negative:
            form["negative_prompt"] = request.negative
        for key, value in (cfg.get("extra_form") or {}).items():
            form[str(key)] = str(value)
        for key, value in (request.extra or {}).items():
            form[str(key)] = str(value)
        return form

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        images = self.images_for(request)
        if not images:
            raise ProviderError("没有可用的输入图片", provider=self.name)

        field = str(self.cfg.get("image_field", "image[]")) or "image[]"
        files = [
            (field, (path.name, path.read_bytes(), mime_for(path))) for path in images
        ]
        headers = {"Authorization": f"Bearer {self.api_key()}"}
        headers.update({str(k): str(v) for k, v in (self.cfg.get("extra_headers") or {}).items()})

        try:
            response = await self.client.post(
                self.endpoint,
                data=self._form(request),
                files=files,
                headers=headers,
                timeout=self.timeout_of(timeout),
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

        try:
            payload = response.json()
        except Exception as exc:
            raise ProviderError(
                f"{self.name} 返回非 JSON 内容", provider=self.name, body=response.text[:400]
            ) from exc

        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            raise ProviderError(
                f"{self.name} 返回中缺少 data: {str(payload)[:400]}", provider=self.name
            )
        first = items[0]
        if isinstance(first, dict) and first.get("b64_json"):
            return decode_b64_image(str(first["b64_json"]))
        if isinstance(first, dict) and first.get("url"):
            return await self._download(str(first["url"]), timeout)
        raise ProviderError(
            f"{self.name} 返回结构无法识别: {str(first)[:400]}", provider=self.name
        )
