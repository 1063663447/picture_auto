"""Google Gemini image (nano-banana) adapter: `:generateContent` with inline images."""

from __future__ import annotations

import httpx

from .base import EditRequest, Provider, ProviderError, b64, http_error, mime_for


class GeminiProvider(Provider):
    kind = "gemini"
    label = "Google Gemini 图片编辑"
    notes = (
        "一次请求可携带原图 + 颜色参考图（inline_data）。默认模型 gemini-2.5-flash-image；"
        "不支持 negative 参数，负面约束会合并到提示词末尾。"
    )

    def __init__(self, name: str, cfg: dict, *, env: dict | None = None) -> None:
        super().__init__(name, cfg, env=env)
        self.max_images = int(self.cfg.get("max_images", 6))
        self.supports_negative = False
        self.label = self.cfg.get("label") or self.label

    @property
    def endpoint(self) -> str:
        return self.endpoint_for(None)

    def endpoint_for(self, request: EditRequest | None) -> str:
        base = str(
            self.cfg.get("base_url") or "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")
        model = self.model_name(request) or "gemini-2.5-flash-image"
        action = str(self.cfg.get("action") or "generateContent")
        return f"{base}/models/{model}:{action}"

    def models_url(self) -> str | None:
        base = str(
            self.cfg.get("base_url") or "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")
        return f"{base}/models?pageSize=200"

    def auth_headers(self) -> dict:
        key = self.api_key()
        return {"x-goog-api-key": key} if key else {}

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        images = self.images_for(request)
        if not images:
            raise ProviderError("没有可用的输入图片", provider=self.name)

        parts: list[dict] = [{"text": request.prompt}]
        for path in images:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": mime_for(path),
                        "data": b64(path),
                    }
                }
            )

        modalities = self.cfg.get("response_modalities", ["TEXT", "IMAGE"])
        body: dict = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": list(modalities)},
        }
        if self.cfg.get("safety_off"):
            body["safetySettings"] = [
                {"category": category, "threshold": "BLOCK_NONE"}
                for category in (
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                )
            ]
        headers = {
            "x-goog-api-key": self.api_key(),
            "Content-Type": "application/json",
        }
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
        candidates = payload.get("candidates") or []
        if not candidates:
            blocked = (payload.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(
                f"{self.name} 未返回候选结果" + (f"（被拒原因 {blocked}）" if blocked else ""),
                provider=self.name,
                body=str(payload)[:400],
            )
        for part in candidates[0].get("content", {}).get("parts", []) or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                import base64

                try:
                    return base64.b64decode(inline["data"])
                except Exception as exc:  # pragma: no cover
                    raise ProviderError(f"{self.name} 图片解码失败: {exc}") from exc
        finish = candidates[0].get("finishReason")
        raise ProviderError(
            f"{self.name} 响应中没有图片（finishReason={finish}）",
            provider=self.name,
            body=str(payload)[:400],
        )
