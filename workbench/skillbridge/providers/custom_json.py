"""Template-driven JSON adapter for any other 生图接口.

Configure with a request body template and a result path; nothing else is hard
coded. Two shapes are supported and can be combined:

* synchronous  – the submit response already contains the image
* asynchronous – the submit response contains a task id, then a poll URL is queried
  until a status field reaches a done value

Placeholders available in the URL / body / poll URL templates:
``${model} ${prompt} ${negative} ${size} ${quality} ${seed} ${image_data_url}
${image_b64} ${image_count} ${api_key} ${ref_data_url} ${ref_b64}``
"""

from __future__ import annotations

import asyncio
import re
import time

import httpx

from .base import (
    EditRequest,
    Provider,
    ProviderError,
    b64,
    data_url,
    decode_b64_image,
    http_error,
)

PLACEHOLDER = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
INDEX = re.compile(r"\[(\d+)\]")


def resolve_path(payload, path: str):
    """Resolve `data[0].b64_json` / `data.0.url` style paths."""
    if not path:
        return None
    current = payload
    for raw in path.replace("[", ".").replace("]", "").split("."):
        token = raw.strip()
        if not token:
            continue
        if isinstance(current, (list, tuple)):
            if not token.isdigit():
                return None
            index = int(token)
            if index >= len(current):
                return None
            current = current[index]
        elif isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
        else:
            return None
    return current


def fill(template, values: dict):
    if isinstance(template, dict):
        return {key: fill(value, values) for key, value in template.items()}
    if isinstance(template, list):
        return [fill(value, values) for value in template]
    if isinstance(template, str):
        return PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "")), template)
    return template


class CustomJsonProvider(Provider):
    kind = "custom_json"
    label = "自定义 JSON 接口"
    notes = "按 config 里的 body 模板与 result 路径对接任意第三方接口，可配置异步轮询。"

    def __init__(self, name: str, cfg: dict, *, env: dict | None = None) -> None:
        super().__init__(name, cfg, env=env)
        self.multi_image = bool(self.cfg.get("multi_image", False))
        self.max_images = int(self.cfg.get("max_images", 2 if self.multi_image else 1))
        self.supports_negative = bool(self.cfg.get("supports_negative", False))
        self.label = self.cfg.get("label") or self.label

    def models_url(self) -> str | None:
        configured = self.cfg.get("models_url")
        return str(configured) if configured else None

    def _values(self, request: EditRequest) -> dict:
        images = self.images_for(request)
        source = images[0] if images else None
        reference = images[1] if len(images) > 1 else (images[0] if images else None)
        return {
            "model": self.model_name(request),
            "prompt": request.prompt,
            "negative": request.negative or "",
            "size": "" if request.size == "auto" else request.size,
            "quality": request.quality or "",
            "seed": "" if request.seed is None else request.seed,
            "image_data_url": data_url(source) if source else "",
            "image_b64": b64(source) if source else "",
            "ref_data_url": data_url(reference) if reference else "",
            "ref_b64": b64(reference) if reference else "",
            "image_count": len(images),
            "api_key": self.api_key(),
        }

    def _headers(self, values: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key():
            headers["Authorization"] = f"Bearer {self.api_key()}"
        headers.update(fill(self.cfg.get("headers") or {}, values))
        return {str(k): str(v) for k, v in headers.items()}

    async def _post(self, url: str, body, headers: dict, timeout: float) -> dict:
        try:
            response = await self.client.request(
                str(self.cfg.get("method", "POST")).upper(),
                url,
                json=body,
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
            return response.json()
        except Exception as exc:
            raise ProviderError(
                f"{self.name} 返回非 JSON: {response.text[:200]}", provider=self.name
            ) from exc

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        values = self._values(request)
        submit_url = fill(str(self.cfg.get("submit_url") or ""), values)
        if not submit_url:
            raise ProviderError("custom_json 缺少 submit_url", provider=self.name)
        body = fill(self.cfg.get("body") or {"prompt": "${prompt}"}, values)
        payload = await self._post(submit_url, body, self._headers(values), timeout)

        # synchronous result
        for key in ("result_b64_path",):
            path = str(self.cfg.get(key) or "")
            if path:
                found = resolve_path(payload, path)
                if found:
                    return decode_b64_image(str(found))
        for key in ("result_url_path",):
            path = str(self.cfg.get(key) or "")
            if path:
                found = resolve_path(payload, path)
                if found:
                    return await self._download(str(found), timeout)

        poll = self.cfg.get("poll") or {}
        if not poll:
            raise ProviderError(
                f"{self.name} 未配置结果路径: {str(payload)[:300]}", provider=self.name
            )

        task_id = resolve_path(payload, str(poll.get("id_path") or "id"))
        if not task_id:
            raise ProviderError(
                f"{self.name} 轮询模式下未取到任务 id: {str(payload)[:300]}", provider=self.name
            )
        values.update({"task_id": task_id, "id": task_id})
        poll_url = fill(str(poll.get("url") or ""), values)
        interval = float(poll.get("interval_sec", 2))
        deadline = time.monotonic() + float(poll.get("max_sec", 180))
        done_values = {str(v).lower() for v in (poll.get("done_values") or ["succeeded", "success", "completed", "done"])}
        fail_values = {str(v).lower() for v in (poll.get("fail_values") or ["failed", "error", "canceled"])}

        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            state = await self._post(poll_url, None, self._headers(values), timeout)
            status = str(resolve_path(state, str(poll.get("status_path") or "status")) or "").lower()
            if status in fail_values:
                raise ProviderError(
                    f"{self.name} 任务失败: {str(state)[:300]}", provider=self.name
                )
            if status in done_values:
                if poll.get("image_url_path"):
                    found = resolve_path(state, str(poll["image_url_path"]))
                    if found and str(found).startswith("http"):
                        return await self._download(str(found), timeout)
                    if found:
                        return decode_b64_image(str(found))
                if poll.get("image_b64_path"):
                    found = resolve_path(state, str(poll["image_b64_path"]))
                    if found:
                        return decode_b64_image(str(found))
                raise ProviderError(
                    f"{self.name} 轮询完成但未配置图片路径", provider=self.name
                )
        raise ProviderError(f"{self.name} 轮询超时", retryable=True, provider=self.name)
