"""Provider abstraction: one `edit()` call -> raw image bytes."""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

import httpx

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524}


class ProviderError(Exception):
    """An API call failed.

    ``retryable`` drives the runner's backoff loop; ``status`` keeps the HTTP code so
    the UI can show something actionable (401 = key wrong, 429 = too fast, ...).
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        provider: str | None = None,
        body: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.retryable = retryable
        self.provider = provider
        self.body = body
        self.retry_after = retry_after

    def as_dict(self) -> dict:
        return {
            "message": self.message,
            "status": self.status,
            "retryable": self.retryable,
            "provider": self.provider,
        }


@dataclass
class EditRequest:
    """One image-API edit call."""

    images: list[Path]
    prompt: str
    negative: str | None = None
    size: str = "auto"
    quality: str = "high"
    seed: int | None = None
    model: str | None = None          # per-job model override
    input_fidelity: str | None = None  # gpt-image family: "high" keeps logos/faces intact
    extra: dict = field(default_factory=dict)


def mime_for(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0]
    if guessed and guessed.startswith("image/"):
        return guessed
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix, "image/png")


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def data_url(path: Path) -> str:
    return f"data:{mime_for(path)};base64,{base64.b64encode(read_bytes(path)).decode()}"


def b64(path: Path) -> str:
    return base64.b64encode(read_bytes(path)).decode()


def sniff_extension(data: bytes) -> str:
    """Detect the real container of a returned image so files get sensible names."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return ".gif"
    return ".png"


def decode_b64_image(payload: str) -> bytes:
    """Accept both raw base64 and `data:image/...;base64,` payloads."""
    if not payload:
        raise ProviderError("接口返回的图片数据为空", retryable=False)
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    try:
        return base64.b64decode(payload, validate=False)
    except Exception as exc:  # pragma: no cover - defensive
        raise ProviderError(f"接口返回的 base64 无法解码: {exc}") from exc


def http_error(provider: str, response: httpx.Response) -> ProviderError:
    body = response.text[:800] if response.text else ""
    message = f"{provider} 返回 HTTP {response.status_code}"
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            message = f"{provider} HTTP {response.status_code}: {err['message']}"
        elif isinstance(err, str):
            message = f"{provider} HTTP {response.status_code}: {err}"
        elif payload.get("message"):
            message = f"{provider} HTTP {response.status_code}: {payload['message']}"

    retry_after: float | None = None
    header = response.headers.get("retry-after") or ""
    try:
        if header:
            retry_after = max(0.5, min(60.0, float(header)))
    except ValueError:
        retry_after = None

    return ProviderError(
        message,
        status=response.status_code,
        retryable=response.status_code in RETRYABLE_STATUS,
        provider=provider,
        body=body,
        retry_after=retry_after,
    )


class Provider:
    """Base class for every image API adapter."""

    name = "base"
    kind = "base"
    label = "Base"
    #: how many input images the API accepts in one call (source first, then refs)
    max_images = 1
    #: whether the API has a real negative-prompt field
    supports_negative = False
    #: concurrency the provider is comfortable with
    default_concurrency = 4
    #: request-level docs shown in the UI
    notes = ""
    #: sizes the provider accepts; when set, `size=auto` is resolved from the source aspect
    size_presets: list[str] | None = None

    def __init__(self, name: str, cfg: dict, *, env: dict | None = None) -> None:
        self.name = name
        self.cfg = dict(cfg or {})
        self.env = env or {}
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle ---------------------------------------------------------- #
    async def open(self) -> None:
        if self._client is None:
            kwargs: dict = {
                "follow_redirects": True,
                "limits": httpx.Limits(max_connections=64, max_keepalive_connections=32),
                "headers": {"User-Agent": "apparel-recolor-workbench/1.0"},
            }
            proxy = self.cfg.get("proxy")
            if proxy:
                kwargs["proxy"] = str(proxy)
            if self.cfg.get("trust_env") is not None:
                kwargs["trust_env"] = bool(self.cfg.get("trust_env"))
            self._client = httpx.AsyncClient(**kwargs)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("provider 未初始化，请先 await provider.open()")
        return self._client

    # -- config helpers ----------------------------------------------------- #
    def setting(self, key: str, default=None):
        value = self.cfg.get(key)
        if value in (None, "") and key.endswith("_env"):
            value = self.env.get(str(self.cfg.get(key) or ""))
        return default if value in (None, "") else value

    def api_key(self) -> str:
        direct = self.cfg.get("api_key")
        if direct:
            return str(direct).strip()
        env_name = self.cfg.get("api_key_env")
        if env_name:
            return str(self.env.get(str(env_name), "")).strip()
        return ""

    def ready(self) -> tuple[bool, str]:
        if not self.cfg.get("enabled", True):
            return False, "未启用"
        if not self.api_key() and not self.cfg.get("allow_anonymous"):
            env_name = self.cfg.get("api_key_env") or "API_KEY"
            return False, f"缺少 API Key（请设置 {env_name} 或 config.json 里的 api_key）"
        return True, "就绪"

    def info(self) -> dict:
        ok, reason = self.ready()
        unlimited = bool(self.setting("unlimited", False)) or bool(self.setting("skip_gate", False))
        return {
            "name": self.name,
            "kind": self.kind,
            "label": self.cfg.get("label") or self.label,
            "model": self.cfg.get("model", ""),
            "ready": ok,
            "reason": reason,
            "max_images": self.max_images,
            "supports_negative": self.supports_negative,
            "default_concurrency": int(self.setting("concurrency", self.default_concurrency)),
            # unlimited = 该接口不排队、并发不限（实测能扛的接口才开）：上限报 0
            "max_concurrency": 0 if unlimited else int(self.setting("max_concurrency", 20) or 20),
            "unlimited": unlimited,
            "skip_gate": unlimited,
            "default_rpm": int(self.setting("rpm", 0) or 0),
            "notes": self.notes,
        }

    # -- the one method adapters implement ---------------------------------- #
    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        raise NotImplementedError

    # -- model discovery ---------------------------------------------------- #
    #: 有些模型只接受部分 quality 取值（例如 grok 系列只认 low / medium / auto）。
    #: 规则写在 config 的 defaults.quality_rules 或 provider.quality_rules：
    #:   [{"match": "grok-imagine", "allowed": ["low", "medium", "auto"], "default": "auto"}]
    #: match 按模型 id 前缀匹配（不区分大小写），先命中的生效。
    def quality_for(self, model: str, quality: str) -> str:
        wanted = str(quality or "").strip().lower()
        for rule in self.quality_rules():
            match = str(rule.get("match") or "").strip().lower()
            if not match:
                continue
            target = str(model or "").strip().lower()
            if not (target.startswith(match) or match in target):
                continue
            allowed = [str(x).strip().lower() for x in (rule.get("allowed") or []) if str(x).strip()]
            if not allowed or not wanted or wanted in allowed:
                return wanted or str(rule.get("default") or allowed[0])
            return str(rule.get("default") or allowed[0])
        return wanted

    def quality_rules(self) -> list[dict]:
        rules: list[dict] = []
        for source in (self.cfg.get("quality_rules"), (self.cfg.get("_global") or {}).get("quality_rules")):
            if isinstance(source, list):
                rules.extend(r for r in source if isinstance(r, dict))
        return rules

    def model_name(self, request: EditRequest | None = None) -> str:
        override = getattr(request, "model", None) if request is not None else None
        return str(override or self.cfg.get("model") or "")

    def models_url(self) -> str | None:
        """Where to list models; None means the provider has no model catalogue."""
        return None

    def auth_headers(self) -> dict:
        key = self.api_key()
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def list_models(self, timeout: float | None = None) -> list[dict]:
        timeout = float(timeout or self.cfg.get("models_timeout") or 45.0)
        url = self.models_url()
        if not url:
            raise ProviderError(f"接口 `{self.name}` 不支持模型列表", provider=self.name)
        await self.open()
        try:
            response = await self.client.get(url, headers=self.auth_headers(), timeout=timeout)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{self.name} 拉取模型超时", retryable=True, provider=self.name) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} 拉取模型失败: {exc}", retryable=True, provider=self.name) from exc
        if response.status_code >= 400:
            raise http_error(self.name, response)
        try:
            payload = response.json()
        except Exception as exc:
            raise ProviderError(f"{self.name} 模型列表不是 JSON", provider=self.name) from exc
        raw = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(raw, list):
            raise ProviderError(f"{self.name} 模型列表结构无法识别", provider=self.name)
        models: list[dict] = []
        for item in raw:
            if isinstance(item, str):
                models.append({"id": item})
            elif isinstance(item, dict):
                model_id = item.get("id") or item.get("name") or item.get("model")
                if model_id:
                    models.append({"id": str(model_id), "owned_by": item.get("owned_by")})
        return models

    # -- shared helpers ----------------------------------------------------- #
    def images_for(self, request: EditRequest) -> list[Path]:
        return list(request.images)[: self.max_images]

    def timeout_of(self, timeout: float) -> httpx.Timeout:
        return httpx.Timeout(timeout, connect=min(30.0, timeout))

    async def _download(self, url: str, timeout: float) -> bytes:
        response = await self.client.get(url, timeout=self.timeout_of(timeout))
        if response.status_code >= 400:
            raise http_error(self.name, response)
        return response.content
