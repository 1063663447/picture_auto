"""跨机器的「全局并发阀门」。

为什么要它：同一个生图网关（同一把 Key）实测只稳定支持 20 并发。工作台打包给同事后，
每台电脑各自限流是没有意义的 —— 三个人各开 8 并发，网关实际就是 24 并发，照样被限。
所以并发额度必须放在一个**所有机器都能看到的地方**：Cloudflare Worker + 单个
Durable Object（actor 天然串行），只做一件事 —— 发牌（acquire）与还牌（release）。

协议（服务端实现见 deploy/cloudflare-gate/worker.js）：

    POST {url}  {"action":"acquire","holder":"机器名","job":"任务号","task":"任务项","limit":20}
      → 200 {"ok":true,"lease":"<id>","in_use":7,"limit":20}
      → 429 {"ok":false,"in_use":20,"limit":20,"retry_after_ms":800}

    POST {url}  {"action":"release","lease":"<id>","holder":"机器名"}
      → 200 {"ok":true,"in_use":19}

    POST {url}  {"action":"status"}
      → 200 {"ok":true,"in_use":3,"limit":20,"leases":3}

租约自带 TTL（服务端默认 180 秒，客户端每次 acquire 都会续），进程崩了不会永久占坑。
拿不到牌时客户端按 `retry_after_ms` 轮询排队，队列状态写进任务日志（"排队中：全局并发 20/20"）。

设计原则：**阀门挂了不能耽误出图**。网络错误/5xx/超时都按 `fail_open` 处理 ——
记一条 warn，退回「只用本机并发限制」，继续跑。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import socket
import time
from dataclasses import dataclass, field

import httpx


class GateBusy(RuntimeError):
    """闸门是好的，但额度一直被占满（排队超时）。

    这种情况**不能放行** —— 放行就等于没有闸门，直接去撞上游并发限制。
    抛出来让 runner 把这张图记成「被限流」，之后可以一键重试。
    """


def _holder_name() -> str:
    try:
        return socket.gethostname() or "workbench"
    except Exception:  # noqa: BLE001
        return "workbench"


@dataclass
class GateConfig:
    enabled: bool = False
    url: str = ""
    token: str = ""
    limit: int = 20
    poll_ms: int = 700
    wait_timeout_sec: float = 1800.0
    request_timeout_sec: float = 8.0
    fail_open: bool = True
    holder: str = ""
    # 飞书表格闸门（provider="feishu"）
    provider: str = "http"       # http=Cloudflare 阀门 | feishu=飞书表当队列
    table_id: str = ""
    settle_ms: int = 1200        # 写占位后等多久再数一遍（防飞书索引延迟）
    confirmations: int = 2       # 连续确认几次才放行（飞书索引延迟时靠它兜底）
    stale_sec: float = 600.0     # 占位超过这么久视为崩溃遗留

    @classmethod
    def from_config(cls, raw: dict | None, env: dict | None = None) -> "GateConfig":
        raw = raw or {}
        env = env or {}
        token = str(raw.get("token") or "").strip()
        if not token:
            token = str(env.get(str(raw.get("token_env") or "GATE_TOKEN"), "") or "").strip()
        # 打包给同事时，用 .env 覆盖最省事：GATE_URL / GATE_LIMIT / GATE_TOKEN
        url = str(env.get("GATE_URL") or raw.get("url") or "").strip()
        limit = env.get("GATE_LIMIT") or raw.get("limit") or 20
        provider = str(raw.get("provider") or "http").strip().lower()
        table_id = str(raw.get("table_id") or "").strip()
        if provider == "feishu":
            # 飞书闸门：只要启用 + 有表，就不需要 url
            return cls(
                enabled=bool(raw.get("enabled")) and bool(table_id),
                url="",
                token=token,
                limit=max(1, int(limit)),
                poll_ms=max(300, int(raw.get("poll_ms") or 2500)),
                wait_timeout_sec=float(raw.get("wait_timeout_sec") or 1800),
                request_timeout_sec=float(raw.get("request_timeout_sec") or 8),
                fail_open=bool(raw.get("fail_open", True)),
                holder=str(raw.get("holder") or "").strip() or _holder_name(),
                provider="feishu",
                table_id=table_id,
                settle_ms=max(0, int(raw.get("settle_ms") or 1200)),
                confirmations=max(1, int(raw.get("confirmations") or 2)),
                stale_sec=max(60.0, float(raw.get("stale_sec") or 600)),
            )
        enabled = bool(raw.get("enabled")) or bool(env.get("GATE_URL"))
        return cls(
            enabled=bool(enabled) and bool(url),
            url=url,
            token=token,
            limit=max(1, int(limit)),
            poll_ms=max(120, int(raw.get("poll_ms") or 700)),
            wait_timeout_sec=float(raw.get("wait_timeout_sec") or 1800),
            request_timeout_sec=float(raw.get("request_timeout_sec") or 8),
            fail_open=bool(raw.get("fail_open", True)),
            holder=str(raw.get("holder") or "").strip() or _holder_name(),
        )

    def public(self) -> dict:
        """给界面看的（不含 token）。"""
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "url": self.url,
            "table_id": self.table_id,
            "settle_ms": self.settle_ms,
            "confirmations": self.confirmations,
            "limit": self.limit,
            "holder": self.holder,
            "wait_timeout_sec": self.wait_timeout_sec,
            "fail_open": self.fail_open,
        }


@dataclass
class Lease:
    id: str
    limit: int = 0
    in_use: int = 0
    waited: float = 0.0
    holder: str = ""
    lease_token: str = ""       # 飞书闸门用来标记自己占的那一行

    def as_dict(self) -> dict:
        return {"lease": self.id, "limit": self.limit, "in_use": self.in_use,
                "waited": round(self.waited, 2)}


@dataclass
class GateStatus:
    ok: bool = False
    enabled: bool = False
    in_use: int = 0
    limit: int = 0
    error: str = ""
    checked_at: float = 0.0
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "enabled": self.enabled,
            "in_use": self.in_use,
            "limit": self.limit,
            "error": self.error,
            "checked_at": self.checked_at,
            **self.extra,
        }


class RemoteGate:
    """客户端：拿牌 / 还牌 / 查状态。所有失败都按 fail_open 降级。"""

    def __init__(self, config: GateConfig, label: str = "") -> None:
        self.cfg = config
        self.label = label
        self._down_until = 0.0        # 熔断：连续失败后一段时间直接放行，别再拖慢出图
        self._fail_streak = 0
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------- lifecycle #
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.cfg.request_timeout_sec, connect=5.0)
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.cfg.token:
            headers["Authorization"] = f"Bearer {self.cfg.token}"
        return headers

    async def _post(self, payload: dict) -> tuple[int, dict]:
        response = await self._http().post(
            self.cfg.url, json=payload, headers=self._headers()
        )
        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            body = {}
        if not isinstance(body, dict):
            body = {}
        return response.status_code, body

    def _mark_fail(self, reason: str) -> None:
        self._fail_streak += 1
        # 连续 3 次失败 → 熔断 60 秒，别让每张图都先等一次超时
        if self._fail_streak >= 3:
            self._down_until = time.monotonic() + 60
        self.last_error = reason

    def _mark_ok(self) -> None:
        self._fail_streak = 0
        self._down_until = 0.0
        self.last_error = ""

    @property
    def bypassing(self) -> bool:
        return time.monotonic() < self._down_until

    # ------------------------------------------------------------- acquire #
    async def acquire(self, *, job: str = "", task: str = "", on_wait=None) -> Lease | None:
        """拿一张牌。返回 None 表示「阀门不可用，已降级放行」。"""
        if not self.cfg.enabled:
            return None
        if self.bypassing:
            return None
        started = time.monotonic()
        deadline = started + max(5.0, self.cfg.wait_timeout_sec)
        attempt = 0
        last_note = 0.0
        while True:
            attempt += 1
            try:
                status, body = await self._post(
                    {
                        "action": "acquire",
                        "holder": self.cfg.holder,
                        "job": job,
                        "task": task,
                        "limit": self.cfg.limit,
                        "label": self.label,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - 网络问题
                self._mark_fail(str(exc))
                if self.cfg.fail_open:
                    return None
                raise
            if status == 200 and body.get("ok"):
                self._mark_ok()
                return Lease(
                    id=str(body.get("lease") or ""),
                    limit=int(body.get("limit") or self.cfg.limit),
                    in_use=int(body.get("in_use") or 0),
                    waited=time.monotonic() - started,
                    holder=self.cfg.holder,
                )
            if status in (409, 429):
                # 满员 → 排队
                self._mark_ok()
                now = time.monotonic()
                if now >= deadline:
                    self._mark_fail("排队超时")
                    raise GateBusy(
                        f"全局并发阀门排队超过 {int(self.cfg.wait_timeout_sec)}s（额度 {self.cfg.limit} 被占满）"
                    )
                wait_s = max(0.15, float(body.get("retry_after_ms") or self.cfg.poll_ms) / 1000.0)
                in_use = int(body.get("in_use") or 0)
                limit = int(body.get("limit") or self.cfg.limit)
                if on_wait and (now - last_note > 8 or attempt == 1):
                    last_note = now
                    try:
                        on_wait(in_use, limit, now - started)
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.sleep(wait_s + random.uniform(0, 0.2))
                continue
            # 其它状态（401/403/500…）→ 配置或服务端问题
            self._mark_fail(f"HTTP {status} {str(body.get('error') or '')[:80]}")
            if self.cfg.fail_open:
                return None
            raise RuntimeError(f"全局并发阀门拒绝：HTTP {status}")

    async def release(self, lease: Lease | None) -> None:
        if not lease or not lease.id or not self.cfg.enabled:
            return
        try:
            await self._post(
                {"action": "release", "lease": lease.id, "holder": self.cfg.holder}
            )
        except Exception as exc:  # noqa: BLE001 - 还牌失败靠服务端 TTL 兜底
            self._mark_fail(str(exc))

    # -------------------------------------------------------------- status #
    async def status(self) -> GateStatus:
        if not self.cfg.enabled:
            return GateStatus(ok=False, enabled=False, limit=self.cfg.limit,
                              error="未启用", checked_at=time.time())
        try:
            status, body = await self._post({"action": "status", "holder": self.cfg.holder})
        except Exception as exc:  # noqa: BLE001
            return GateStatus(ok=False, enabled=True, limit=self.cfg.limit,
                              error=str(exc)[:120], checked_at=time.time(),
                              extra={"bypassing": self.bypassing})
        if status != 200 or not body.get("ok"):
            return GateStatus(ok=False, enabled=True, limit=self.cfg.limit,
                              error=f"HTTP {status}", checked_at=time.time())
        self._mark_ok()
        return GateStatus(
            ok=True,
            enabled=True,
            in_use=int(body.get("in_use") or 0),
            limit=int(body.get("limit") or self.cfg.limit),
            checked_at=time.time(),
            extra={"holder": self.cfg.holder},
        )


def describe(lease: Lease | None) -> str:
    if not lease:
        return "未走阀门"
    return f"拿到全局并发 {lease.in_use}/{lease.limit}（等 {lease.waited:.1f}s）"
