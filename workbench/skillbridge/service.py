"""Workbench service: owns config, skill, providers, job registry and job tasks."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import httpx

from . import feishu as feishu_mod
from . import prompts, refine, style_memory
from . import subject as subject_module
from .config import WORKBENCH_DIR, load_config, load_env
from .gate import GateConfig, RemoteGate
from .pricing import Pricer, UsageLedger
from .job import Job, Task, new_job_id, normalise_item
from .providers import build_providers
from .providers.base import Provider
from .runner import JobRunner
from .skill import ColorRef, GarmentType, Skill, load_skill
from .sources import job_image_path, original_uploads
from .output_policy import edits_people, person_selection

SAFE_NAME = re.compile(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_MODEL_HINT = re.compile(
    r"(image|dall-?e|flux|seedream|seededit|kolors|wanx|imagen|nano-banana|"
    r"grok-imagine-image|midjourney|qwen-image|\bsd-?xl\b|photoshop|edit)",
    re.I,
)
NON_IMAGE_HINT = re.compile(r"(video|veo|seedance|kling|sora|animate|tts|whisper|embed)", re.I)
MODEL_CACHE_TTL = 600.0


def _retry_targets(
    job: "Job", task_ids: list[str] | None = None, only: str = ""
) -> tuple[list["Task"], set[str]]:
    """挑出要重试的任务。

    only="rate_limit" 时只捞「被并发/限流打回来」的那些（一键重试按钮用它），
    否则按 task_ids（空 = 全部失败项）。
    """
    if only == "rate_limit":
        targets = [
            t for t in job.tasks if t.status == "failed" and t.error_kind == "rate_limit"
        ]
        return targets, {t.id for t in targets}
    wanted = {str(t) for t in (task_ids or [])}
    targets = [
        t for t in job.tasks if t.status == "failed" and (not wanted or t.id in wanted)
    ]
    return targets, wanted


def _resolve_pattern_mode(explicit: str, requirement: str = "") -> str:
    """图案策略：显式传的优先，其次从需求里推断，最后回到「逐像素保留」。"""
    from .prompts import PATTERN_MODES, detect_pattern_mode

    mode = (explicit or "").strip().lower()
    if mode in PATTERN_MODES:
        return mode
    return detect_pattern_mode(requirement) or "preserve"


class WorkbenchError(Exception):
    pass


class Workbench:
    def __init__(self, config_path: str | Path | None = None) -> None:
        self.env = load_env()
        self.config = load_config(Path(config_path) if config_path else None, self.env)
        self.data_dir = Path(self.config["data_dir"])
        self.jobs_dir = self.data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        #: 9.6.32 每条在跑的任务对应的 runner 实例（审核期「边跑边确认」要用它把派生色插进本轮）
        self._runners: dict[str, object] = {}
        self._distill: dict[str, asyncio.Task] = {}
        self._provider_sems: dict[str, asyncio.Semaphore] = {}
        self._model_cache: dict[str, tuple[float, dict]] = {}
        pricing_path = Path(str(self.config.get("pricing_file") or "pricing.json"))
        if not pricing_path.is_absolute():
            candidate = WORKBENCH_DIR / pricing_path
            pricing_path = candidate if candidate.is_file() else pricing_path
        self.pricer = Pricer(
            pricing_path,
            currency=str(
                (self.config.get("defaults") or {}).get("pricing_currency")
                or self.config.get("pricing_currency")
                or ""
            ),
            # 同一个模型名在不同网关售价不同：按接口给覆盖价
            provider_prices={
                name: {
                    str(model): float(unit)
                    for model, unit in ((cfg or {}).get("model_prices") or {}).items()
                    if unit is not None
                }
                for name, cfg in (self.config.get("providers") or {}).items()
                if (cfg or {}).get("model_prices")
            },
            provider_fixed_prices={
                name: float(cfg["fixed_unit_price"])
                for name, cfg in (self.config.get("providers") or {}).items()
                if (cfg or {}).get("fixed_unit_price") is not None
            },
        )
        self.usage = UsageLedger(self.data_dir, self.pricer)
        self.feishu_config = None
        try:
            from . import feishu as feishu_mod

            self.feishu_config = feishu_mod.load_config(self.config, self.env)
        except Exception:  # noqa: BLE001
            self.feishu_config = None
        from .auth import AccountStore

        self.accounts = AccountStore(self.feishu_config, self.data_dir)
        self.pricer.refresh_from_feishu(self.feishu_config)
        self.drafts_dir = self.data_dir / "drafts"
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.reload_skill()
        self.providers: dict[str, Provider] = build_providers(self.config, self.env)
        # 有些模型只接受部分 quality（grok 系列只认 low/medium/auto）：
        # 把全局规则挂到每个 provider 上，provider 自己写的 quality_rules 优先。
        global_rules = (self.config.get("defaults") or {}).get("quality_rules") or []
        if global_rules:
            for _provider in self.providers.values():
                _provider.cfg.setdefault("_global", {})["quality_rules"] = global_rules
        # 跨机器的全局并发阀门（Cloudflare Worker）；没配就是 None，只用本机并发
        self.gate_config = GateConfig.from_config(
            (self.config.get("defaults") or {}).get("gate"), self.env
        )
        self._gates: dict[str, RemoteGate] = {}
        self._load_existing()

    # ------------------------------------------------------------------ setup #
    @property
    def library_dir(self) -> Path:
        """款式库目录（library/prompts 就在它下面）。"""
        block = self.config.get("library") or {}
        candidate = Path(str(block.get("dir") or "library"))
        return candidate if candidate.is_absolute() else WORKBENCH_DIR / candidate

    def reload_skill(self) -> Skill:
        self.skill = load_skill(self.config["skill_root"])
        base_slugs = list(self.skill.types)     # 原 skill 自带的款式
        # 叠加工作台自己的款式库（《墨西哥-服装款式代号表》解析出来的款式/颜色）
        try:
            from .library import merge_into_skill

            library_block = self.config.get("library") or {}
            base = None
            if library_block.get("dir"):
                candidate = Path(str(library_block["dir"]))
                base = candidate if candidate.is_absolute() else WORKBENCH_DIR / candidate
            if library_block.get("enabled", True):
                added = merge_into_skill(self.skill, base)
                if added:
                    self.library_added = added
                    # 款式库里有对应款式时，把原生的三款从界面下拉里隐藏（仍可按名字调用）
                    if library_block.get("hide_original_types", True):
                        self.skill.hidden.update(base_slugs)
        except Exception as exc:  # noqa: BLE001 - 款式库坏了也不能让工作台起不来
            self.skill.warnings.append(f"款式库加载失败：{exc}")
        # optional override of the skill's 2,000,000-byte packaging limit
        override = self.config.get("max_bytes") or (self.config.get("defaults") or {}).get(
            "max_bytes"
        )
        if override:
            try:
                self.skill.max_bytes = int(override)
            except (TypeError, ValueError):
                pass
        return self.skill

    def _load_existing(self) -> None:
        for path in sorted(self.jobs_dir.glob("*/job.json")):
            try:
                job = Job.load(path)
            except Exception:
                continue
            if job.status in {"running", "packaging", "queued"}:
                job.status = "failed"
                job.stage = "服务重启，任务中断"
                for task in job.tasks:
                    if task.status in {"pending", "running"}:
                        task.status = "canceled"
                try:
                    job.save()
                except OSError:
                    pass
            self.jobs[job.id] = job

    # ------------------------------------------------------------------- info #
    def skill_payload(self) -> dict:
        return {"ok": True, **self.skill.as_dict()}

    def provider_payload(self) -> list[dict]:
        """Only providers flagged `show_in_ui` are offered in the workbench UI."""
        visible = [
            provider
            for provider in self.providers.values()
            if provider.cfg.get("show_in_ui", False)
        ]
        if not visible:  # fallback so the UI is never empty
            visible = list(self.providers.values())
        preferred = str(self.config.get("default_provider") or "")
        visible.sort(key=lambda provider: provider.name != preferred)
        rows = []
        for provider in visible:
            info = provider.info()
            # 该接口自己的覆盖价（同名模型不同网关售价不同），界面算预估要用
            overrides = (provider.cfg or {}).get("model_prices") or {}
            if overrides:
                info["model_prices"] = {
                    str(k): float(v) for k, v in overrides.items() if v is not None
                }
            fixed = provider.cfg.get("fixed_unit_price")
            if fixed is not None:
                info["fixed_unit_price"] = float(fixed)
            info["default"] = provider.name == preferred
            rows.append(info)
        return rows

    def defaults(self) -> dict:
        return self.config.get("defaults") or {}

    def gate_public(self) -> dict:
        """界面用的阀门信息（不含 token）。"""
        return self.gate_config.public()

    # ---------------------------------------------------------------- gate --
    def gate_for(self, provider: Provider | None = None):
        """给某个接口配阀门（同接口复用；未启用返回 None）。

        两种实现，接口一致：
        * `gate.provider = "feishu"` → 飞书表格当「全局并发 + 队列」（推荐，零额外服务）
        * `gate.provider = "http"`   → Cloudflare Worker 阀门（只限流）
        """
        if not self.gate_config.enabled:
            return None
        # 这个接口标了 unlimited（实测不排队也没事）→ 完全不走闸门
        if provider is not None and (
            provider.setting("unlimited", False) or provider.setting("skip_gate", False)
        ):
            return None
        name = provider.name if provider is not None else "-"
        gate = self._gates.get(name)
        if gate is not None:
            return gate
        cfg = self.gate_config
        if provider is not None:
            override = provider.setting("gate_limit", None)
            if override:
                cfg = GateConfig(**{**cfg.__dict__, "limit": max(1, int(override))})
        if cfg.provider == "feishu":
            table_id = cfg.table_id or str((self.config.get("feishu") or {}).get("gate_table_id") or "")
            if not table_id:
                self.gate_config = GateConfig(**{**cfg.__dict__, "enabled": False})
                return None
            cfg = GateConfig(**{**cfg.__dict__, "table_id": table_id})
            client = self._gate_feishu_client()
            if client is None:
                return None
            from .feishu_gate import FeishuGate

            gate = FeishuGate(cfg, client, label=name)
        else:
            gate = RemoteGate(cfg, label=name)
        self._gates[name] = gate
        return gate

    def _gate_feishu_client(self):
        """闸门表用的飞书客户端（和记录表同一个应用，表可以不同）。"""
        if self.feishu_config is None or not self.feishu_config.ready()[0]:
            return None
        from .feishu import FeishuBitable

        return FeishuBitable(self.feishu_config)

    async def gate_status(self) -> dict:
        cfg = self.gate_config
        payload: dict = {"config": cfg.public()}
        if not cfg.enabled:
            payload.update({
                "ok": False,
                "enabled": False,
                "error": "未启用（只用本机并发限制）",
                "provider": cfg.provider,
            })
            return payload
        # 默认接口若标了 unlimited（主接口 a6api 就是），gate_for() 会返回 None；
        # 旧代码直接 gate.status() → AttributeError → /api/gate 500，界面「队列」胶囊一直是坏的。
        # 现在：默认接口没闸门就退到任一「要排队」的接口（如备用接口 65535），都没有就明确
        # 回 enabled=false，绝不抛 500。
        provider = self.get_provider(None)
        gate = self.gate_for(provider)
        if gate is None:
            for name, candidate in self.providers.items():
                candidate_gate = self.gate_for(candidate)
                if candidate_gate is not None:
                    payload["fallback_provider"] = name
                    payload["reason"] = (
                        f"{provider.name if provider is not None else '主接口'} 不限并发、不排队；"
                        f"这里显示的是 {name} 的队列"
                    )
                    provider, gate = candidate, candidate_gate
                    break
        if gate is None:
            payload.update({
                "ok": False,
                "enabled": False,
                "error": "所有接口都不走闸门（不限并发、不排队）",
                "provider": provider.name if provider is not None else cfg.provider,
            })
            return payload
        status = await gate.status()
        payload.update(status.as_dict())
        payload["config"] = cfg.public()
        # 埋点：每个接口各自的拿牌/等待统计（排查「谁在排队、排了多久」）
        payload["stats_by_provider"] = {
            name: getattr(entry, "stats", {}) for name, entry in self._gates.items()
        }
        return payload

    # ----------------------------------------------------------------- models #
    def _get_provider_any(self, name: str | None) -> Provider:
        name = (name or "").strip()
        if not name:
            name = str(self.config.get("default_provider") or "mock")
        provider = self.providers.get(name)
        if provider is None:
            raise WorkbenchError(f"未知接口 `{name}`，可用：{sorted(self.providers)}")
        return provider

    def _models_cache_path(self, provider_name: str) -> Path:
        return self.data_dir / f"models-{provider_name}.json"

    def _read_models_cache(self, provider_name: str) -> dict | None:
        path = self._models_cache_path(provider_name)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_models_cache(self, provider_name: str, payload: dict) -> None:
        path = self._models_cache_path(provider_name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError:
            pass

    def _priced_models(self, payload: dict, provider_name: str) -> dict:
        result = dict(payload)
        rows = []
        for original in payload.get("models") or []:
            row = dict(original)
            price = self.pricer.price(str(row["id"]), str(self.defaults().get("size")), provider_name)
            row.update(price=price.unit, price_label=price.label(), currency=price.currency)
            rows.append(row)
        result["models"] = rows
        return result

    def _offline_models(self, provider_name: str, reason: str) -> dict:
        """网关 /models 抖动时：先用上次成功的快照，再退回 pricing.json 的模型名。"""
        snapshot = self._read_models_cache(provider_name)
        if snapshot and snapshot.get("models"):
            payload = self._priced_models(snapshot, provider_name)
            payload["cached"] = True
            payload["degraded"] = f"{reason}（使用上次拉取的快照）"
            self._model_cache[provider_name] = (time.time(), payload)
            return payload
        rows = []
        for model_id in sorted(self.pricer.raw):
            price = self.pricer.price(model_id, str(self.defaults().get("size")), provider_name)
            rows.append(
                {
                    "id": model_id,
                    "image": True,
                    "video": False,
                    "owned_by": None,
                    "current": False,
                    "price": price.unit,
                    "price_label": price.label(),
                    "currency": price.currency,
                }
            )
        payload = {
            "provider": provider_name,
            "current": "",
            "count": len(rows),
            "image_models": [row["id"] for row in rows],
            "models": rows,
            "fetched_at": time.time(),
            "cached": False,
            "degraded": f"{reason}（使用本地 pricing.json 的模型清单）",
        }
        self._model_cache[provider_name] = (time.time(), payload)
        return payload

    async def list_models(
        self, name: str | None, *, refresh: bool = False, include_unpriced: bool = False
    ) -> dict:
        """Pull the provider's model catalogue so the operator can pick a real id."""
        provider = self._get_provider_any(name)
        cached = self._model_cache.get(provider.name)
        if cached and not refresh and time.time() - cached[0] < MODEL_CACHE_TTL:
            payload = self._priced_models(cached[1], provider.name)
            payload["cached"] = True
            if not include_unpriced:
                payload = self._keep_priced_models(payload, provider)
            return payload

        current = str(provider.cfg.get("model") or "")
        try:
            raw = await provider.list_models()
        except Exception as exc:  # noqa: BLE001 - 上游抖动不该让下拉框变空
            return self._offline_models(provider.name, f"模型列表拉取失败：{exc}")
        models: list[dict] = []
        for item in raw:
            model_id = item["id"]
            is_video = bool(NON_IMAGE_HINT.search(model_id))
            is_image = bool(IMAGE_MODEL_HINT.search(model_id)) and not is_video
            models.append(
                {
                    "id": model_id,
                    "image": is_image,
                    "video": is_video,
                    "owned_by": item.get("owned_by"),
                    "current": model_id == current,
                }
            )
        for model in models:
            price = self.pricer.price(model["id"], str(self.defaults().get("size")), provider.name)
            model["price"] = price.unit
            model["price_label"] = price.label()
            model["currency"] = price.currency
        models.sort(key=lambda m: (not m["image"], m["video"], m["id"].lower()))
        payload = {
            "provider": provider.name,
            "current": current,
            "count": len(models),
            "image_models": [m["id"] for m in models if m["image"]],
            "models": models,
            "fetched_at": time.time(),
            "cached": False,
        }
        self._model_cache[provider.name] = (time.time(), payload)
        self._write_models_cache(provider.name, payload)
        if not include_unpriced:
            payload = self._keep_priced_models(payload, provider)
        return payload

    def _keep_priced_models(self, payload: dict, provider: Provider) -> dict:
        """只列「有报价」的模型（默认行为）。

        客户要求（2026-11-09）：备用接口按飞书「模型定价」表来读，不要把网关那 20 多个没报价的
        模型全铺进下拉。规则：留**能算出单价**的（飞书定价表 → 接口覆盖 → pricing.json 任一命中），
        外加当前正在用的那个（否则下拉框显示不出当前值）。飞书表读不到时会退到 pricing.json，
        名单会变长 —— 这是有意的降级，不是 bug。
        """
        options = dict(self.defaults() or {})
        setter = getattr(provider, "setting", None)
        override = setter("models_only_priced", None) if callable(setter) else None
        if override is None:
            enabled = bool(options.get("models_only_priced", True))
        else:
            enabled = bool(override)
        if not enabled:
            return payload
        models = list(payload.get("models") or [])
        kept = [m for m in models if m.get("price") is not None or m.get("current")]
        if len(kept) == len(models):
            return payload
        result = dict(payload)
        result["models"] = kept
        result["count"] = len(kept)
        result["image_models"] = [m["id"] for m in kept if m.get("image")]
        result["filtered_unpriced"] = len(models) - len(kept)
        result["filter_note"] = "只列有报价的模型（飞书「模型定价」表 / 接口价目）"
        return result

    def get_provider(self, name: str | None) -> Provider:
        name = (name or "").strip()
        if not name:
            name = str(self.config.get("default_provider") or "mock")
        provider = self.providers.get(name)
        if provider is None:
            raise WorkbenchError(f"未知接口 `{name}`，可用：{sorted(self.providers)}")
        ready, reason = provider.ready()
        if not ready:
            raise WorkbenchError(f"接口 `{name}` 未就绪：{reason}")
        return provider

    # ------------------------------------------------------------------- jobs #
    def job_dir(self, job_id: str) -> Path:
        if not re.fullmatch(r"[0-9A-Za-z\-]{4,40}", job_id or ""):
            raise WorkbenchError("非法任务 ID")
        return self.jobs_dir / job_id

    def get_job(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise WorkbenchError(f"任务不存在：{job_id}")
        self._reconcile_rounds(job)
        return job

    def _reconcile_rounds(self, job: Job) -> None:
        """历史兜底：改图任务早就跑完了，但主任务那一轮还记着「进行中」。

        收尾回填（`_update_master_rounds`）只在任务刚跑完那一刻执行；9.6.24 之前挂住的
        老单子、或者收尾时正好在重启，就会一直是「修改中」。这里每次读任务时顺手对齐一次，
        代价只有一次字典查找，所以老单子升级后也能自己好。
        """
        reviews = (job.params or {}).get("master_reviews")
        if not isinstance(reviews, list) or not reviews:
            return
        stale: set[str] = set()
        for item in reviews:
            if not isinstance(item, dict):
                continue
            rework_job = str(item.get("rework_job") or "")
            if not rework_job or rework_job == job.id:
                continue
            if str(item.get("status") or "") in {"done", "failed"} and item.get("task"):
                continue
            other = self.jobs.get(rework_job)
            if other is None or str(other.status) not in {"done", "partial", "failed", "canceled"}:
                continue
            stale.add(rework_job)
        for rework_job in stale:
            try:
                self._update_master_rounds(rework_job)
            except Exception:  # noqa: BLE001 - 对齐失败不影响读任务
                pass

    def list_jobs(self) -> list[dict]:
        jobs = sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)
        return [job.as_dict(with_tasks=False, with_log=False) for job in jobs]

    def _resolve_garment(self, name: str | None) -> GarmentType:
        try:
            return self.skill.type_by_alias(name)
        except KeyError as exc:
            raise WorkbenchError(str(exc)) from exc

    def create_job(
        self,
        uploads: list[tuple[str, bytes]],
        *,
        garment_type: str | None,
        colors: list[str] | None = None,
        selections: list[dict] | None = None,
        mode: str = "single_pass",
        provider: str | None = None,
        model: str | None = None,
        concurrency: int | str | None = None,
        rpm: int | None = None,
        size: str = "auto",
        quality: str = "high",
        seed: int | None = None,
        max_retries: int | None = None,
        timeout_sec: float | None = None,
        output_format: str = "auto",
        send_reference: bool = True,
        input_fidelity: str | None = None,
        extra_requirements: str = "",
        pattern_mode: str = "",
        pattern_brief: str = "",
        subject: str = "",
        retry_on_person: bool = False,
        shape_lock: str = "",
        review_master: bool | None = None,
        account: str = "",
    ) -> Job:
        if not uploads:
            raise WorkbenchError("请先上传至少一张图片")

        plan: list[tuple[object, list]] = []
        seen: dict[str, int] = {}
        for raw in selections or []:
            if not isinstance(raw, dict):
                continue
            try:
                picked = self._resolve_garment(raw.get("garment_type") or raw.get("slug"))
            except WorkbenchError as exc:
                raise WorkbenchError(f"款式不存在：{exc}") from exc
            try:
                wanted = picked.resolve_colors(raw.get("colors") or [])
            except KeyError as exc:
                raise WorkbenchError(str(exc)) from exc
            if not wanted:
                raise WorkbenchError(f"{picked.display_name} 至少选一个颜色")
            if picked.slug in seen:
                # 同一个款式被加了两次 → 合并颜色，不重复排任务
                index = seen[picked.slug]
                merged = {c.label: c for c in plan[index][1]}
                for color in wanted:
                    merged.setdefault(color.label, color)
                plan[index] = (plan[index][0], list(merged.values()))
                continue
            seen[picked.slug] = len(plan)
            plan.append((picked, wanted))
        if not plan:
            garment = self._resolve_garment(garment_type)
            try:
                picked_colors = garment.resolve_colors(colors)
            except KeyError as exc:
                raise WorkbenchError(str(exc)) from exc
            plan = [(garment, picked_colors)]
        garment = plan[0][0]
        selected = plan[0][1]
        if not selected:
            raise WorkbenchError("请至少选择一个颜色参考")

        provider_obj = self.get_provider(provider)
        defaults = self.defaults()
        chosen_model = str(model or "").strip() or str(provider_obj.cfg.get("model") or "")

        job_id = new_job_id()
        root = self.job_dir(job_id)
        (root / "inputs").mkdir(parents=True)
        (root / "work").mkdir()
        (root / "staging").mkdir()
        (root / "outputs").mkdir()

        counter = 0
        for filename, data in uploads:
            suffix = Path(filename).suffix.lower()
            if suffix not in IMAGE_SUFFIXES:
                raise WorkbenchError(f"不支持的图片格式：{filename}")
            stem = Path(filename).stem
            item, view = normalise_item(stem)
            if item:
                folder = item
                name = f"{view or stem}{suffix}"
            else:
                counter += 1
                folder = f"item-{counter:02d}"
                name = f"{stem or folder}{suffix}"
            target_dir = root / "inputs" / folder
            target_dir.mkdir(parents=True, exist_ok=True)
            safe = SAFE_NAME.sub("-", name).strip("-") or f"{folder}{suffix}"
            target = target_dir / safe
            index = 1
            while target.exists():
                index += 1
                target = target_dir / f"{Path(safe).stem}-{index}{suffix}"
            target.write_bytes(data)

        params_shape_lock = (
            "off"
            if str(shape_lock or defaults.get("shape_lock") or "master").strip().lower()
            in {"off", "0", "false", "no", "none", "不锁"}
            else "master"
        )
        params = {
            "garment_type": garment.slug,
            "garment_name": garment.display_name,
            "colors": [color.label for color in selected],
            # 多款式：每一项是「款式 + 该款式要出的颜色」
            "selections": [
                {
                    "garment_type": g.slug,
                    "garment_name": g.display_name,
                    "colors": [c.label for c in cols],
                }
                for g, cols in plan
            ],
            "style_count": len(plan),
            "total_colors": sum(len(cols) for _, cols in plan),
            "mode": (
                str(mode)
                if str(mode) in {"single_pass", "two_phase", "recolor_only"}
                else "single_pass"
            ),
            "provider": provider_obj.name,
            "provider_label": provider_obj.info()["label"],
            "model": chosen_model,
            "concurrency": concurrency,
            "rpm": int(
                rpm if rpm is not None else (provider_obj.setting("rpm", 0) or 0)
            ),
            "size": str(size or "auto"),
            # 按模型收敛 quality（grok 只认 low/medium/auto），免得白跑一次才被上游打回
            "quality": provider_obj.quality_for(chosen_model, str(quality or "high")),
            "seed": int(seed) if seed not in (None, "") else None,
            "max_retries": max(1, int(max_retries or defaults.get("max_retries") or 3)),
            "timeout_sec": float(timeout_sec or defaults.get("timeout_sec") or 90),
            "output_format": str(output_format or defaults.get("output_format") or "png"),
            "send_reference": bool(send_reference),
            "input_fidelity": str(
                input_fidelity or provider_obj.setting("input_fidelity", "") or defaults.get("input_fidelity", "")
            ),
            "max_bytes": self.skill.max_bytes,
            "extra_requirements": " ".join((extra_requirements or "").split()),
            "pattern_mode": _resolve_pattern_mode(pattern_mode, extra_requirements),
            "pattern_brief": " ".join((pattern_brief or "").split()),
            # 主体：auto（按原图判）/ garment（纯衣服平铺）/ person（真人上身）
            "subject": subject_module.normalise(subject),
            "subject_choice": subject_module.normalise(subject),
            "retry_on_person": bool(retry_on_person),
            # 版型锁定：master（先出基准色再改色，同款各色版型一致）/ off（每色独立生成）
            # 版型一致性（内部行为，界面无开关）：先出基准色，其余颜色以它为参考图只改色。
            # 传 shape_lock=off 可退回「每色独立生成」（命令行/接口用）。
            "shape_lock": params_shape_lock,
            # 基准图审核：先只出基准图 → 运营确认/修改 → 再按基准图改其余颜色。
            # 单色任务没有派生色，闸门没有意义，自动关掉。
            "review_gate": bool(
                (defaults.get("review_master", True) if review_master is None else review_master)
                and params_shape_lock == "master"
                and sum(len(cols) for _g, cols in plan) > 1
            ),
            "account": str(account or ""),
            "estimate": self.pricer.estimate(chosen_model, 0, str(size or "auto"), provider_obj.name),
        }

        batch_name = (
            garment.batch_name if len(plan) == 1 else f"{len(plan)}styles-recolor-batch"
        )
        job = Job(id=job_id, params=params, batch_name=batch_name)
        job.root = root
        style_text = (
            f"{garment.display_name}，{len(selected)} 色"
            if len(plan) == 1
            else " + ".join(f"{g.display_name}({len(c)} 色)" for g, c in plan)
        )
        job.add_log(
            f"任务创建：{style_text}，{len(uploads)} 张原图，接口 {provider_obj.name}"
        )
        # plan up-front so the UI can show the whole task list immediately
        try:
            JobRunner(
                job,
                self.skill,
                garment,
                selected,
                provider_obj,
                selections=plan,
                data_dir=self.data_dir,
                gate=self.gate_for(provider_obj),
            ).plan()
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(root, ignore_errors=True)
            raise WorkbenchError(f"任务规划失败：{exc}") from exc

        # 并发不再让用户选：默认一次全推，超过接口上限/闸门额度的自动排队；
        # 标了 unlimited 的接口（a6api 这种实测能扛的）既不限并发也不排队
        unlimited = bool(provider_obj.setting("unlimited", False))
        if str(concurrency).strip().lower() in {"", "auto", "none"} or concurrency is None:
            max_conc = max(1, int(provider_obj.setting("max_concurrency", 20) or 20))
            wanted = max(1, min(200, len(job.tasks)))
            if unlimited:
                # 可选软上限：不排队，但单批别一次压太多（100 并发时网关排队会让单张等 80~130s）
                soft = int(provider_obj.setting("batch_concurrency", 0) or 0)
                conc = wanted if soft <= 0 else max(1, min(wanted, soft))
                job.params["concurrency"] = conc
                if conc < wanted:
                    job.add_log(
                        f"全部提交：{wanted} 张（{provider_obj.name} 不排队，"
                        f"本批同时发 {conc} 张，其余等前一批）"
                    )
                else:
                    job.add_log(
                        f"全部提交：{wanted} 张一次发完（{provider_obj.name} 不限并发、不排队）"
                    )
                max_conc = wanted
            else:
                job.params["concurrency"] = min(wanted, max_conc)
            gate_limit = int(
                ((self.config.get("defaults") or {}).get("gate") or {}).get("limit") or 0
            )
            queue_hint = f"，闸门额度 {gate_limit}" if gate_limit else ""
            if not unlimited and wanted > max_conc:
                job.add_log(
                    f"全部提交：{wanted} 张，同时最多 {max_conc} 张在跑{queue_hint}，"
                    f"其余 {wanted - max_conc} 张排队（界面会显示「前方还有 xx 个任务」）",
                    level="warn",
                )
            elif not unlimited:
                job.add_log(
                    f"全部提交：{wanted} 张一起发（接口上限 {max_conc}{queue_hint}，超了自动排队）"
                )

        job.params["estimate"] = self.pricer.estimate(
            chosen_model, len(job.tasks), str(job.params.get("size") or "auto"), provider_obj.name
        )
        try:
            self._quota_guard(
                str(params.get("account") or ""),
                chosen_model,
                len(job.tasks),
                str(job.params.get("size") or "auto"),
                provider_obj.name,
            )
        except WorkbenchError:
            shutil.rmtree(root, ignore_errors=True)
            raise
        job.save()
        self.jobs[job_id] = job
        return job

    # ---------------------------------------------------------------- drafts --
    def save_draft(self, uploads: list[tuple[str, bytes]]) -> dict:
        """开工前先把图片存成草稿；harness 的工具之后直接引用这份草稿。"""
        if not uploads:
            raise WorkbenchError("没有可保存的图片")
        draft_id = "d" + new_job_id()
        folder = self.drafts_dir / draft_id
        folder.mkdir(parents=True, exist_ok=True)
        names: list[str] = []
        for index, (filename, data) in enumerate(uploads, start=1):
            suffix = Path(filename).suffix.lower() or ".png"
            if suffix not in IMAGE_SUFFIXES:
                raise WorkbenchError(f"不支持的图片格式：{filename}")
            (folder / f"{index:02d}{suffix}").write_bytes(data)
            names.append(filename)
        return {"draft_id": draft_id, "count": len(names), "names": names}

    def draft_uploads(self, draft_id: str) -> list[tuple[str, bytes]]:
        if not re.fullmatch(r"d[0-9A-Za-z\-]{4,40}", draft_id or ""):
            raise WorkbenchError("非法草稿 ID")
        folder = self.drafts_dir / draft_id
        if not folder.is_dir():
            raise WorkbenchError("草稿已失效，请重新上传图片")
        uploads: list[tuple[str, bytes]] = []
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                uploads.append((f"item-{path.stem}{path.suffix}", path.read_bytes()))
        if not uploads:
            raise WorkbenchError("草稿里没有图片")
        return uploads

    def draft_image_paths(self, draft_id: str) -> list[Path]:
        if not re.fullmatch(r"d[0-9A-Za-z\-]{4,40}", draft_id or ""):
            return []
        folder = self.drafts_dir / draft_id
        if not folder.is_dir():
            return []
        return [
            path
            for path in sorted(folder.iterdir())
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ]

    def drop_draft(self, draft_id: str) -> None:
        if re.fullmatch(r"d[0-9A-Za-z\-]{4,40}", draft_id or ""):
            shutil.rmtree(self.drafts_dir / draft_id, ignore_errors=True)

    # --------------------------------------------------------------- pricing --
    def pricing_payload(self, account: str | None = None) -> dict:
        self.pricer.refresh_from_feishu(self.feishu_config)
        payload = self.pricer.table()
        payload["usage"] = self.usage.summary(account=account)
        payload["default_model"] = str(
            (self.providers.get(str(self.config.get("default_provider") or "")) or next(iter(self.providers.values()))).cfg.get("model") or ""
        )
        return payload

    def usage_payload(self, account: str | None = None) -> dict:
        return self.usage.summary(account=account)

    def record_usage(self, job: Job) -> dict | None:
        stats = job.counts()
        done = stats["done"] + stats["skipped"]
        model = str(job.params.get("model") or "")
        params = job.params or {}
        detail = " · ".join(
            str(part)
            for part in (
                params.get("garment_name") or "",
                "、".join(params.get("colors") or []),
                params.get("size") or "",
            )
            if part
        )
        return self.usage.record(
            job_id=job.id,
            model=model,
            images=done,
            kind=str(params.get("kind") or "generate"),
            size=str(params.get("size") or "auto"),
            account=str(params.get("account") or ""),
            detail=detail,
            garment=str(params.get("garment_name") or ""),
            color="、".join(str(c) for c in (params.get("colors") or [])),
            provider=str(params.get("provider") or ""),
        )

    def _feishu_rows(self, max_pages: int = 6) -> list[dict] | None:
        """读飞书记录表并标准化成 [{account, images, cost, model, detail, job_id, at, status}]。"""
        cfg = self.feishu_config
        if cfg is None or not cfg.enabled:
            return None
        ok, _reason = cfg.ready()
        if not ok:
            return None
        try:
            items = feishu_mod.FeishuBitable(cfg).search_table(
                cfg.table_id, page_size=200, max_pages=max_pages
            )
        except Exception:  # noqa: BLE001 - 飞书挂了就用本地账本
            return None
        if not items:
            return None

        def text(value) -> str:
            if value is None:
                return ""
            if isinstance(value, list):
                return "".join(
                    str(x.get("text") or "") if isinstance(x, dict) else str(x) for x in value
                )
            return str(value)

        rows: list[dict] = []
        for item in items:
            fields = item.get("fields") or {}
            at = fields.get("时间") or 0
            at = float(at) / 1000.0 if isinstance(at, (int, float)) and at > 1e11 else float(at or 0)
            try:
                cost = float(fields.get("金额元") or 0)
            except (TypeError, ValueError):
                cost = 0.0
            rows.append(
                {
                    "record_id": item.get("record_id"),
                    "account": text(fields.get("账号")).strip() or "anonymous",
                    "images": int(fields.get("张数") or 0),
                    "cost": cost,
                    "model": text(fields.get("模型")).strip(),
                    "garment": text(fields.get("款式")).strip(),
                    "detail": text(fields.get("成品")).strip(),
                    "color": text(fields.get("颜色")).strip(),
                    "job_id": text(fields.get("任务ID")).strip(),
                    "status": text(fields.get("状态")).strip(),
                    "at": at,
                }
            )
        rows.sort(key=lambda row: row["at"], reverse=True)
        return rows

    def _feishu_account_usage(self) -> dict[str, dict] | None:
        """按账号聚合飞书记录（每人张数/花费/次数/最近成品）。"""
        rows = self._feishu_rows()
        if not rows:
            return None
        buckets: dict[str, dict] = {}
        jobs: dict[str, set[str]] = {}
        for row in rows:
            name = row["account"]
            bucket = buckets.setdefault(
                name,
                {"account": name, "images": 0, "cost": 0.0, "jobs": 0, "last_at": 0.0, "recent": []},
            )
            bucket["images"] += row["images"]
            bucket["cost"] += row["cost"]
            bucket["last_at"] = max(bucket["last_at"], row["at"])
            if row["job_id"]:
                jobs.setdefault(name, set()).add(row["job_id"])
            bucket["recent"].append(
                {
                    "job_id": row["job_id"],
                    "model": row["model"],
                    "images": row["images"],
                    "cost": row["cost"],
                    "detail": row["detail"],
                    "at": row["at"],
                }
            )
        for name, bucket in buckets.items():
            bucket["cost"] = round(bucket["cost"], 4)
            bucket["jobs"] = len(jobs.get(name) or ())
            bucket["recent"] = bucket["recent"][:10]
            bucket["models"] = []
        return buckets

    def accounts_payload(self) -> dict:
        """账号列表 + 每人用量（管理员用）。优先用飞书记录聚合，其次本地账本。"""
        accounts = self.accounts.load(force=True)
        feishu_usage = self._feishu_account_usage()
        usage = feishu_usage if feishu_usage else {
            row["account"]: row for row in self.usage.by_account()
        }
        rows = []
        for account in accounts.values():
            account_usage = usage.pop(account.username, None) or usage.pop(
                account.username.lower(), None
            ) or {}
            cost = round(float(account_usage.get("cost") or 0), 4)
            quota = float(account.quota or 0)
            rows.append(
                {
                    **account.as_dict(),
                    "images": account_usage.get("images", 0),
                    "cost": cost,
                    "jobs": account_usage.get("jobs", 0),
                    "last_at": account_usage.get("last_at", 0),
                    "recent": account_usage.get("recent", []),
                    "models": account_usage.get("models", []),
                    "quota_left": (round(quota - cost, 4) if quota > 0 else None),
                    "quota_pct": (round(min(100.0, cost / quota * 100), 1) if quota > 0 else None),
                }
            )
        for name, account_usage in usage.items():  # 表里没有的历史账号
            rows.append(
                {
                    "username": name,
                    "display": name,
                    "role": "unknown",
                    "active": True,
                    "quota": 0,
                    "images": account_usage.get("images", 0),
                    "cost": round(float(account_usage.get("cost") or 0), 4),
                    "jobs": account_usage.get("jobs", 0),
                    "last_at": account_usage.get("last_at", 0),
                    "recent": account_usage.get("recent", []),
                    "models": account_usage.get("models", []),
                    "quota_left": None,
                    "quota_pct": None,
                }
            )
        rows.sort(key=lambda row: (-float(row.get("cost") or 0), -int(row.get("images") or 0)))
        return {
            "currency": self.pricer.currency,
            "source": self.accounts.source,
            "usage_source": "feishu" if feishu_usage else "ledger",
            "accounts": rows,
        }

    def admin_overview(self) -> dict:
        """管理后台总面板：总量、按账号、按模型、按天、最近记录、额度。"""
        payload = self.accounts_payload()
        rows = self._feishu_rows()
        usage_source = "feishu"
        if rows is None:
            usage_source = "ledger"
            rows = [
                {
                    "account": entry.get("account") or "anonymous",
                    "images": int(entry.get("images") or 0),
                    "cost": float(entry.get("cost") or 0),
                    "model": str(entry.get("model") or ""),
                    "garment": str(entry.get("garment") or ""),
                    "detail": str(entry.get("detail") or ""),
                    "color": str(entry.get("color") or ""),
                    "job_id": str(entry.get("job_id") or ""),
                    "status": "done",
                    "at": float(entry.get("at") or 0),
                }
                for entry in self.usage.entries
            ]
            rows.sort(key=lambda row: row["at"], reverse=True)

        total_images = sum(int(r["images"] or 0) for r in rows)
        total_cost = round(sum(float(r["cost"] or 0) for r in rows), 4)
        today = time.strftime("%Y-%m-%d")
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

        by_model: dict[str, dict] = {}
        by_day: dict[str, dict] = {}
        by_style: dict[str, dict] = {}
        by_color: dict[str, dict] = {}
        by_hour = [
            {"hour": hour, "images": 0, "cost": 0.0} for hour in range(24)
        ]
        for row in rows:
            images = int(row["images"] or 0)
            cost = float(row["cost"] or 0)
            model = row.get("model") or "（未知）"
            bucket = by_model.setdefault(model, {"model": model, "images": 0, "cost": 0.0})
            bucket["images"] += images
            bucket["cost"] += cost
            style = str(row.get("garment") or "").strip() or "（未标注款式）"
            style_bucket = by_style.setdefault(
                style, {"style": style, "images": 0, "cost": 0.0}
            )
            style_bucket["images"] += images
            style_bucket["cost"] += cost
            color = str(row.get("color") or "").strip() or "（未标注颜色）"
            color_bucket = by_color.setdefault(
                color, {"color": color, "images": 0, "cost": 0.0}
            )
            color_bucket["images"] += images
            color_bucket["cost"] += cost
            stamp = float(row["at"] or 0)
            day = time.strftime("%Y-%m-%d", time.localtime(stamp))
            day_bucket = by_day.setdefault(day, {"day": day, "images": 0, "cost": 0.0})
            day_bucket["images"] += images
            day_bucket["cost"] += cost
            if day == today:
                hour = int(time.strftime("%H", time.localtime(stamp)) or 0)
                by_hour[max(0, min(23, hour))]["images"] += images
                by_hour[max(0, min(23, hour))]["cost"] += cost

        for group in (by_model, by_style, by_color):
            for bucket in group.values():
                bucket["cost"] = round(bucket["cost"], 4)
        for bucket in by_day.values():
            bucket["cost"] = round(bucket["cost"], 4)
        for bucket in by_hour:
            bucket["cost"] = round(bucket["cost"], 4)
        # 曲线图要连续的 X 轴：补齐最近 14 天（没出图的那天也要有 0，否则曲线会跳）
        day_keys = [
            (date.today() - timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in range(13, -1, -1)
        ]
        days = [by_day.get(key) or {"day": key, "images": 0, "cost": 0.0} for key in day_keys]

        accounts = payload["accounts"]
        quotas = [row for row in accounts if float(row.get("quota") or 0) > 0]
        return {
            "currency": self.pricer.currency,
            "usage_source": usage_source,
            "account_source": payload["source"],
            "totals": {
                "images": total_images,
                "cost": total_cost,
                "jobs": len({r["job_id"] for r in rows if r["job_id"]}),
                "accounts": len(accounts),
                "models": len(by_model),
                "today": by_day.get(today, {"images": 0, "cost": 0.0}),
                "yesterday": by_day.get(yesterday, {"images": 0, "cost": 0.0}),
            },
            "quota": {
                "accounts_with_quota": len(quotas),
                "total_quota": round(sum(float(r.get("quota") or 0) for r in quotas), 4),
                "used": round(sum(float(r.get("cost") or 0) for r in quotas), 4),
            },
            "accounts": accounts,
            "by_model": sorted(by_model.values(), key=lambda item: -item["images"]),
            "by_style": sorted(by_style.values(), key=lambda item: -item["images"]),
            "by_color": sorted(by_color.values(), key=lambda item: -item["images"]),
            "by_day": days,
            "by_hour": by_hour,
            "recent": rows[:20],
            "providers": [
                {"name": str(p.get("name")), "label": str(p.get("label"))}
                for p in self.provider_payload()
            ],
            "pricing_source": self.pricer.source,
        }

    def _quota_guard(self, account: str, model: str, count: int, size: str,
                     provider: str = "") -> None:
        """额度拦截：非管理员的账号（设了额度元）在用超时不让开工。"""
        if not account:
            return
        acc = self.accounts.find(account)
        if acc is None or acc.is_admin:
            return
        quota = float(acc.quota or 0)
        if quota <= 0:
            return
        used = float(
            (self._feishu_account_usage() or {}).get(acc.username, {}).get("cost")
            or next(
                (row["cost"] for row in self.usage.by_account() if row["account"] == acc.username),
                0.0,
            )
        )
        est = self.pricer.estimate(model, count, size, provider)
        estimate_total = float(est.get("total") or 0)
        if used + estimate_total > quota + 1e-9:
            raise WorkbenchError(
                f"额度不足：{acc.display or acc.username} 已用 ¥{used:.2f}，"
                f"本次预估 ¥{estimate_total:.2f}，额度 ¥{quota:.2f}。"
                "请让管理员在「账号」表或管理后台调整额度。"
            )

    def _color_ref_path(self, color_name: str, garment_slug: str) -> Path | None:
        """找到该颜色对应的色卡文件（附件字段「参考图」用）。"""
        try:
            garment = self.skill.types.get(garment_slug)
        except Exception:  # noqa: BLE001
            garment = None
        if garment is None:
            return None
        for ref in garment.colors:
            if ref.name_zh == color_name or ref.label == color_name:
                return ref.path
        return None

    def sync_to_feishu(self, job: Job, force: bool = False) -> int:
        """把任务写进飞书多维表格（含图片附件）；未配置就静默跳过。返回写入行数。

        注意：这个方法跑在线程池里（`_record_after`），异常必须自己记进 job 日志，
        否则失败是"静默"的——之前就踩过这个坑。
        """
        if not force and job.params.get("feishu_rows"):
            return int(job.params.get("feishu_rows") or 0)  # 已写过，别重复记账
        cfg = feishu_mod.load_config(self.config, self.env)
        ok, reason = cfg.ready()
        if not cfg.enabled or not ok:
            if cfg.enabled and not ok:
                job.add_log(f"飞书未同步：{reason}", level="warn")
                job.save()
            return 0

        model = str(job.params.get("model") or "")
        size = str(job.params.get("size") or "auto")
        price = self.pricer.price(model, size, str(job.params.get("provider") or ""))
        rows = feishu_mod.build_rows(job, size, price.unit, price.currency, per=cfg.per)
        if not rows:
            return 0

        root = job.root or self.job_dir(job.id)
        garment_slug = str(job.params.get("garment_type") or "")
        client = feishu_mod.FeishuBitable(cfg)
        try:
            client.ensure_fields()

            if cfg.upload_images:
                by_id = {task.id: task for task in job.tasks}
                for row in rows:
                    task = by_id.get(row.get("_task_id") or "")
                    if task is None:  # per=job 时只挂第一张成品图
                        first = next((t for t in job.tasks if t.out), None)
                        task = first
                    if task is None:
                        continue
                    try:
                        if task.out:
                            row["成品图"] = client.attach(
                                self._upload_ready(root / str(task.out), job)
                            )
                        if task.source:
                            ref = self._color_ref_path(task.color_name, garment_slug)
                            if ref is not None and ref.is_file():
                                row["参考图"] = client.attach(self._upload_ready(ref, job))
                        if cfg.upload_source and task.source:
                            row["原图"] = client.attach(
                                self._upload_ready(root / str(task.source), job)
                            )
                    except Exception as exc:  # noqa: BLE001 - 附件失败不拦整体
                        row["备注"] = (str(row.get("备注") or "") + f" [附件失败:{exc}]")[:900]
                    if cfg.per == "job":
                        break

            for row in rows:
                row.pop("_task_id", None)
            ids = client.create_records(rows)
            job.params["feishu_rows"] = len(ids)
            job.params["feishu_table"] = f"https://feishu.cn/base/{cfg.app_token}?table={cfg.table_id}"
            job.add_log(f"已写入飞书多维表格 {len(ids)} 行（含图片附件）")
            job.save()
            self._clear_pending_sync(job.id)
            return len(ids)
        except Exception as exc:  # noqa: BLE001 - 同步失败不影响出图
            job.add_log(f"飞书写入失败：{exc}", level="warn")
            job.save()
            self._mark_pending_sync(job.id, str(exc))
            return 0

    # ------------------------------------------------------------ 附件瘦身 --
    def _upload_ready(self, path: Path, job: Job) -> Path:
        """上传前把图片压一压（长边 1600 / JPEG 85）：飞书那边看的是缩略图，
        原图 3~6MB 传上去既慢又费出口流量。压缩失败就传原图。"""
        try:
            from PIL import Image

            if not path.is_file():
                return path
            if path.stat().st_size <= 700 * 1024:
                return path
            target = (job.root or self.job_dir(job.id)) / "feishu" / f"{path.stem}.jpg"
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or target.stat().st_mtime < path.stat().st_mtime:
                with Image.open(path) as handle:
                    image = handle.convert("RGB")
                    image.thumbnail((1600, 1600))
                    image.save(target, format="JPEG", quality=85, optimize=True)
            return target
        except Exception:  # noqa: BLE001
            return path

    # ------------------------------------------------- 同步失败待补写（简单账本） --
    def _pending_path(self) -> Path:
        return Path(self.data_dir) / "feishu-pending.json"

    def _pending_ids(self) -> list[str]:
        try:
            import json as _json

            payload = _json.loads(self._pending_path().read_text(encoding="utf-8"))
            return [str(x) for x in payload.get("jobs") or []]
        except Exception:  # noqa: BLE001
            return []

    def _mark_pending_sync(self, job_id: str, reason: str) -> None:
        import json as _json

        ids = self._pending_ids()
        if job_id not in ids:
            ids.append(job_id)
        try:
            self._pending_path().write_text(
                _json.dumps({"jobs": ids[-200:], "reason": reason[:200]}, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _clear_pending_sync(self, job_id: str) -> None:
        import json as _json

        ids = [x for x in self._pending_ids() if x != job_id]
        try:
            self._pending_path().write_text(
                _json.dumps({"jobs": ids}, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError:
            pass

    def pending_sync(self) -> list[str]:
        return self._pending_ids()

    def sync_pending(self) -> int:
        """把之前失败的同步补一遍（服务启动或手动触发）。返回成功行数。"""
        total = 0
        for job_id in self._pending_ids():
            job = self.jobs.get(job_id)
            if job is None:
                self._clear_pending_sync(job_id)
                continue
            if job.params.get("feishu_rows"):
                self._clear_pending_sync(job_id)
                continue
            total += self.sync_to_feishu(job, force=True)
        return total


    def estimate_for(self, model: str, count: int, size: str | None = None, provider: str = "") -> dict:
        return self.pricer.estimate(model, count, size or str(self.defaults().get("size") or "auto"), provider or str(self.config.get("default_provider") or ""))

    # ------------------------------------------------------------------- run  #
    def ui_flags(self) -> dict:
        return dict((self.defaults().get("ui") or {}))

    async def create_rework_job(
        self,
        source_job_id: str,
        task_ids: list[str],
        instruction: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        output_format: str | None = None,
        timeout_sec: float | None = None,
        refiner: str | None = None,
        prompt_override: str = "",
        account: str = "",
        pattern_mode: str = "",
        subject: str = "",
        annotation_path: str = "",
        annotation_id: str = "",
    ) -> Job:
        """以选中的成品为参考图返工（改图）。

        ``annotation_path``：框选标注图的绝对路径；会复制进新任务目录并记到参数里，
        由 runner 作为「改动区域的地图」随请求发出。
        ``annotation_id``：标注图在源任务里的相对 id（前端上传后拿到的），这里换算成绝对路径。
        成品卡上的「框选改图」走这条：不需要经过对话，直接带标注图建一条改图任务。
        """
        source_job = self.get_job(source_job_id)
        if annotation_id and not annotation_path:
            annotated = self._annotation_path(source_job, annotation_id)
            if annotated is None:
                raise WorkbenchError("标注图找不到或路径不安全")
            annotation_path = str(annotated)
        instruction = " ".join((instruction or "").split())
        if not instruction:
            raise WorkbenchError("请填写本次改图需求")
        wanted = {str(t) for t in (task_ids or [])}
        selected = [t for t in source_job.tasks if t.id in wanted and t.out]
        if not selected:
            raise WorkbenchError("请选择至少一张已生成的图片")

        source_root = source_job.root or self.job_dir(source_job_id)
        garment = self._resolve_garment(source_job.params.get("garment_type"))
        provider_obj = self.get_provider(provider or source_job.params.get("provider"))
        defaults = self.defaults()
        rework_cfg = defaults.get("rework") or {}
        chosen_model = str(model or source_job.params.get("model") or
                           provider_obj.cfg.get("model") or "")
        # 换模型改图：成品当参考图交给另一个模型，提示词里要明说别整张重画
        source_provider = str(source_job.params.get("provider") or "")
        source_model = str(source_job.params.get("model") or "")
        model_switched = bool(
            (source_provider and provider_obj.name != source_provider)
            or (source_model and chosen_model and chosen_model != source_model)
        )
        rework_mode = _resolve_pattern_mode(pattern_mode, instruction)
        subject_choice = subject_module.normalise(subject)

        job_id = new_job_id()
        root = self.job_dir(job_id)
        (root / "inputs").mkdir(parents=True)
        (root / "work").mkdir()
        (root / "staging").mkdir()
        (root / "outputs").mkdir()

        tasks: list[Task] = []
        briefs: list[str] = []
        refiners: set[str] = set()
        original_sources: dict[str, list[str]] = {}
        original_source_meta: dict[str, dict] = {}
        source_task_map: dict[str, str] = {}
        copied_originals: dict[Path, str] = {}
        missing_originals: list[str] = []
        # 上一轮已经是改图任务 → 本轮是它的下一轮（轮次用于 id/颜色的版本号）
        try:
            prev_round = int(source_job.params.get("rework_round") or 0)
        except (TypeError, ValueError):
            prev_round = 0
        is_rework_source = str(source_job.params.get("kind") or "") == "rework"
        round_no = (prev_round + 1) if is_rework_source else 1
        for index, task in enumerate(selected, start=1):
            original = job_image_path(source_job, task.out)
            if original is None:
                continue
            folder = task.item or f"item-{index:02d}"
            target_dir = root / "inputs" / folder
            target_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{index:03d}-{SAFE_NAME.sub('_', original.stem)}"
            target = target_dir / f"{stem}{original.suffix or '.png'}"
            target.write_bytes(original.read_bytes())
            # 版本号按轮次走：第一轮 -v2，接着改就是 -v3、-v4…（第 N 轮 = 第 N+1 版）
            base_id = re.sub(r"-v\d+$", "", str(task.id))
            base_color = re.sub(r"-v\d+$", "", str(task.color))
            base_name = str(task.color_name or "").replace("·改图", "")
            task_id = f"{base_id}-v{round_no + 1}"
            origin_refs = []
            for upload in original_uploads(source_job, task, self.get_job):
                copied = copied_originals.get(upload.path)
                if copied is None:
                    destination = root / "originals" / f"{len(copied_originals)+1:03d}-{SAFE_NAME.sub('_', upload.path.name)}"
                    destination.parent.mkdir(exist_ok=True)
                    destination.write_bytes(upload.path.read_bytes())
                    copied = destination.relative_to(root).as_posix()
                    copied_originals[upload.path] = copied
                    original_source_meta[copied] = {"job_id": upload.job_id, "task_id": upload.task_id,
                                                    "item": upload.item, "view": upload.view, "name": upload.name}
                origin_refs.append(copied)
            original_sources[task_id] = origin_refs
            source_task_map[task_id] = task.id
            if not origin_refs:
                missing_originals.append(task.id)

            # Explicit output choice wins. Auto inherits the original task/lineage
            # decision before considering original uploads, never this damaged result.
            task_subject = subject_choice if subject_choice != subject_module.AUTO else (
                subject_module.task_kind(source_job, task, self.get_job)
            )

            if prompt_override.strip():
                prompt, used = prompt_override.strip(), "harness"
            else:
                prompt, used = await refine.compose(
                    instruction,
                    task.prompt,
                    refiner=refiner or str(rework_cfg.get("refiner") or "template"),
                    cfg=rework_cfg.get("llm") or {},
                    env=self.env,
                    pattern_mode=rework_mode,
                    subject=task_subject,
                    garment=garment,
                )
            refiners.add(used)
            if model_switched:
                # 参考图是上一个模型出的：明确交底，别让新模型把它当素材重新生成
                prompt = f"{prompt}{refine.SWITCH_MODEL_CLAUSE}".strip()
            available_originals = min(len(origin_refs), max(0, int(provider_obj.max_images or 1) - 1))
            prompt = prompts.build_rework_reference_prompt(prompt, task_subject, available_originals)
            new_task = Task(
                id=task_id,
                item=folder,
                view=task.view,
                color=f"{base_color}-v{round_no + 1}",
                color_hex=task.color_hex,
                color_name=f"{base_name}·改图",
                source=str(target.relative_to(root)).replace("\\", "/"),
                prompt=prompt,
                subject=task_subject,
                garment=task.garment or garment.slug,
                garment_name=task.garment_name or garment.display_name,
            )
            tasks.append(new_task)
            briefs.append(
                refine.format_brief(
                    instruction=instruction,
                    original_prompt=task.prompt,
                    reference_image=str(task.out),
                    task_id=task.id,
                    garment=garment.display_name,
                    color=task.color_name,
                    pattern_mode=rework_mode,
                    subject=task_subject,
                )
            )

        if not tasks:
            shutil.rmtree(root, ignore_errors=True)
            raise WorkbenchError("选中的图片文件已不存在，无法改图")

        params = {
            "kind": "rework",
            "garment_type": garment.slug,
            "garment_name": garment.display_name,
            "colors": [t.color for t in tasks],
            "mode": "rework",
            "provider": provider_obj.name,
            "provider_label": provider_obj.info()["label"],
            "model": chosen_model,
            "concurrency": len(tasks),
            "rpm": 0,
            "size": str(size or source_job.params.get("size") or "auto"),
            "quality": str(quality or source_job.params.get("quality") or "high"),
            "seed": None,
            "max_retries": int(source_job.params.get("max_retries") or 3),
            "timeout_sec": float(
                timeout_sec or source_job.params.get("timeout_sec") or 90
            ),
            "output_format": str(
                output_format or source_job.params.get("output_format") or "png"
            ),
            "send_reference": False,
            "input_fidelity": str(
                provider_obj.setting("input_fidelity", "")
                or defaults.get("input_fidelity", "")
            ),
            "max_bytes": self.skill.max_bytes,
            "instruction": instruction,
            "refiner": ",".join(sorted(refiners)) or "template",
            "source_job": source_job_id,
            "source_tasks": list(source_task_map.values()),
            "source_task_map": source_task_map,
            "original_sources": original_sources,
            "original_source_meta": original_source_meta,
            "source_provider": source_provider,
            "source_model": source_model,
            "model_switched": model_switched,
            "brief": briefs[0] if briefs else "",
            "account": str(account or source_job.params.get("account") or ""),
            "pattern_mode": rework_mode,
            "subject_choice": subject_choice,
            "subject_edit": next((person_selection(str(source_job.params.get(key))) for key in ("subject_edit", "extra_requirements", "instruction") if edits_people(str(source_job.params.get(key) or ""))), ""),
            "task_subjects": {task.id: task.subject for task in tasks},
            "subject_policy": subject_module.AUTO_POLICY,
            "subject": tasks[0].subject if len({t.subject for t in tasks}) == 1 else "auto",
            "subjects": {kind: sum(t.subject == kind for t in tasks) for kind in subject_module.KINDS},
        }
        # 框选标注：复制进新任务目录，runner 会把它当「改动区域的地图」一起发
        if annotation_path:
            source_file = Path(annotation_path)
            if source_file.is_file():
                target_folder = root / "work"
                target_folder.mkdir(parents=True, exist_ok=True)
                suffix = source_file.suffix.lower() or ".png"
                target_file = target_folder / f"annotation{suffix}"
                shutil.copyfile(source_file, target_file)
                params["annotation_path"] = target_file.relative_to(root).as_posix()
                params["annotation_source"] = str(annotation_path)
            else:
                tasks.clear()
                shutil.rmtree(root, ignore_errors=True)
                raise WorkbenchError("框选标注图不存在，无法作为改动地图")

        params["rework_round"] = round_no
        params["revises_job"] = source_job_id
        params["revises_tasks"] = list(source_task_map.values())
        params["estimate"] = self.pricer.estimate(
            chosen_model, len(tasks), str(params.get("size") or "auto"), provider_obj.name
        )
        try:
            self._quota_guard(
                str(params.get("account") or ""),
                chosen_model,
                len(tasks),
                str(params.get("size") or "auto"),
                provider_obj.name,
            )
        except WorkbenchError:
            shutil.rmtree(root, ignore_errors=True)
            raise
        batch = f"{garment.slug}-rework-batch"
        job = Job(id=job_id, params=params, batch_name=batch)
        job.root = root
        job.tasks = tasks
        job.total = len(tasks)
        job.add_log(
            f"改图任务：{len(tasks)} 张，接口 {provider_obj.name}，模型 {chosen_model or '默认'}，"
            f"提示词由 {params['refiner']} 生成"
        )
        if model_switched:
            job.add_log(
                f"换模型重画：{source_provider or '?'} / {source_model or '?'} → "
                f"{provider_obj.name} / {chosen_model or '默认'}（成品作为参考图交给新模型）",
                level="warn",
            )
        if missing_originals:
            job.add_log(f"{len(missing_originals)} 张成品的原始上传图无法追溯；本轮仅能使用当前成品，不假定已看到原图", level="warn")
        job.add_log(f"用户需求：{instruction}")
        job.save()
        self.jobs[job_id] = job
        return job

    def derived_image(
        self, job_id: str, task_id: str, *, width: int = 0, fmt: str = "jpg", quality: int = 85
    ) -> Path | None:
        """按需生成"压缩版"图片（JPG/WebP），并缓存到 <job>/derived/。

        * `width=0` 表示保持原分辨率，只转格式（PNG 3~6MB → JPG 0.3~0.8MB）
        * 生成失败返回 None，调用方退回原图
        * 缓存必须放在**任务根目录**，不能放进 staging/ —— staging 的每个子目录
          都会被 skill 的 package_outputs.py 当成一个 item 来数图（曾经把
          staging/thumbs 当成 item，导致「contains 2 images; expected 3」打包失败）。
        """
        try:
            from PIL import Image

            fmt = "webp" if str(fmt).lower() == "webp" else "jpg"
            job = self.get_job(job_id)
            root = job.root or self.job_dir(job_id)
            source = self.image_path(job_id, task_id)
            tag = f"{Path(source.name).stem}@{width or 'full'}"
            target = root / "derived" / f"{tag}.{fmt}"
            if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as handle:
                image = handle.convert("RGB")
                if width and image.width > width:
                    ratio = width / float(image.width)
                    image = image.resize((width, max(1, int(image.height * ratio))), Image.LANCZOS)
                if fmt == "webp":
                    image.save(target, format="WEBP", quality=quality, method=4)
                else:
                    image.save(target, format="JPEG", quality=quality, optimize=True)
            return target
        except Exception:  # noqa: BLE001 - 压缩失败就用原图
            return None

    def _style_folder(self, job: Job, task: Task) -> str:
        """导出/打包时用的款式文件夹名（`24CXCR`）。

        优先按任务自己的款式解析出英文代号；老任务没有 garment 字段时用 job 的款式；
        实在解析不出来才退回 staging 里的目录名，保证导出结构与打包结构一致。
        """
        for slug in (task.garment, str((job.params or {}).get("garment_type") or "")):
            slug = str(slug or "").strip()
            if not slug:
                continue
            try:
                key = self._resolve_garment(slug).file_key
            except Exception:  # noqa: BLE001 - 款式找不到就换下一个来源
                key = ""
            if key:
                return key
        if task.out:
            parent = Path(str(task.out)).parent.name
            if parent and parent not in {"staging", ""}:
                return parent
        return "images"

    def export_zip(
        self, job_id: str, task_ids: list[str] | None = None, fmt: str = ""
    ) -> Path:
        """把选中的成品打成 ZIP（不传 task_ids 则导出全部成功图片）。

        **目录结构和打包一致**：`<批次>/<款式代号>/<文件名>`，解压后同款式的图在同一个
        文件夹里，不会所有图混在一起。`fmt=jpg` 时打包压缩版（体积约原图的 1/10）。
        """
        job = self.get_job(job_id)
        root = job.root or self.job_dir(job_id)
        wanted = {str(t) for t in (task_ids or [])}
        use_fmt = str(fmt or "").lower() in {"jpg", "jpeg", "webp"}
        batch = job.batch_name or job_id
        files: list[tuple[Path, str]] = []
        for task in job.tasks:
            if not task.out or (wanted and task.id not in wanted):
                continue
            source = root / str(task.out)
            if not source.is_file():
                continue
            folder = self._style_folder(job, task)
            if use_fmt:
                derived = self.derived_image(
                    job_id, task.id, fmt=str(fmt).lower(), quality=85
                )
                if derived is not None:
                    # derived 缓存名带 `@full` 后缀，进包时用干净的原名
                    files.append((derived, folder, f"{source.stem}{derived.suffix}"))
                    continue
            files.append((source, folder, source.name))
        if not files:
            raise WorkbenchError("没有可导出的图片")
        export_dir = root / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        suffix = "selected" if wanted else "all"
        tag = f"-{str(fmt).lower()}" if use_fmt else ""
        target = export_dir / f"{batch}-{suffix}{tag}-{stamp}.zip"

        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, folder, name in files:
                archive.write(path, Path(batch) / folder / name)
        job.add_log(
            f"导出 {len(files)} 张{'(压缩 JPG)' if use_fmt else ''}："
            f"{target.name}（按款式分了 {len({folder for _p, folder, _n in files})} 个文件夹）"
        )
        job.save()
        return target

    def _provider_sem(self, provider: Provider) -> asyncio.Semaphore | None:
        """Optional cap shared by every job using the same provider.

        Set `global_concurrency` on a provider in config.json when several jobs may
        run at once and the upstream account has a hard concurrency limit.
        """
        if provider.setting("unlimited", False):
            return None          # 实测不排队的接口：不再加共享上限
        cap = int(provider.setting("global_concurrency", 0) or 0)
        if cap <= 0:
            # 没单独配就用接口上限：多人同时提交时，几个任务共享同一池子
            cap = int(provider.setting("max_concurrency", 20) or 20)
        if cap <= 0:
            return None
        sem = self._provider_sems.get(provider.name)
        if sem is None:
            sem = asyncio.Semaphore(cap)
            self._provider_sems[provider.name] = sem
        return sem

    def _runner_for(self, job: Job) -> JobRunner:
        garment = self._resolve_garment(job.params.get("garment_type"))
        is_rework = str(job.params.get("kind") or "") == "rework"
        colors: list[ColorRef] = []
        selections: list[tuple[object, list]] = []
        if not is_rework:
            for raw in job.params.get("selections") or []:
                if not isinstance(raw, dict):
                    continue
                try:
                    picked = self._resolve_garment(raw.get("garment_type"))
                    picked_colors = picked.resolve_colors(raw.get("colors") or [])
                except (WorkbenchError, KeyError):
                    continue
                if picked_colors:
                    selections.append((picked, picked_colors))
            if not selections:
                try:
                    colors = garment.resolve_colors(job.params.get("colors") or [])
                except KeyError as exc:
                    raise WorkbenchError(str(exc)) from exc
                if colors:
                    selections = [(garment, colors)]
        if selections:
            garment, colors = selections[0][0], selections[0][1]
        provider = self.get_provider(job.params.get("provider"))
        return JobRunner(
            job,
            self.skill,
            garment,
            colors,
            provider,
            selections=selections or None,
            data_dir=self.data_dir,
            provider_sem=self._provider_sem(provider),
            gate=self.gate_for(provider),
        )

    def start(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if job_id in self._tasks and not self._tasks[job_id].done():
            raise WorkbenchError("任务正在运行中")
        job.cancel_requested = False
        runner = self._runner_for(job)
        task = asyncio.create_task(runner.run(), name=f"job-{job_id}")
        self._tasks[job_id] = task
        self._runners[job_id] = runner
        task.add_done_callback(
            lambda finished, jid=job_id: (self._record_after(jid, finished), self._forget(jid, finished))
        )
        return job.as_dict(with_tasks=False, with_log=False)

    def _record_after(self, job_id: str, task: asyncio.Task) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        # 9.6.24 先把「改图轮次」回填到发起它的主任务上（纯内存 + 落盘，最便宜的一步）：
        # 主任务一直停在「基准图修改中」、页面一刷新第 N 轮就丢，就是这么来的。
        try:
            self._update_master_rounds(job_id)
        except Exception as exc:  # noqa: BLE001 - 回填失败不能挡住后面的记账
            job.add_log(f"改图轮次回填失败：{exc}", level="warn")
        # 写飞书多维表格（同步 IO，丢线程池，别堵事件循环）
        # 线程池里抛的异常默认没人接，这里显式接住并写进 job 日志 + 待补写账本
        def _sync() -> None:
            if job.params.get("feishu_rows"):
                return
            try:
                rows = self.sync_to_feishu(job)
                if rows:
                    job.add_log(f"飞书同步完成：{rows} 行")
                    job.save()
            except Exception as exc:  # noqa: BLE001
                job.add_log(f"飞书同步异常：{exc}", level="warn")
                job.save()
                self._mark_pending_sync(job.id, str(exc))

        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, _sync)
        except RuntimeError:
            _sync()
        try:
            entry = self.record_usage(job)
            if entry:
                job.add_log(
                    f"用量记录：{entry['images']} 张 × {entry['unit'] or '未定价'} = "
                    f"{entry['cost'] if entry['cost'] is not None else '未定价'} {entry['currency']}"
                )
                job.save()
        except Exception:  # noqa: BLE001
            pass

        # 完成通知（飞书群机器人）放在最后，失败了也不影响任务状态
        try:
            self.notify_done(job)
            job.save()
        except Exception:  # noqa: BLE001
            pass

    def _update_master_rounds(self, rework_job_id: str) -> None:
        """返工任务收尾后，把结果回填到发起它的主任务的审核记录里。

        没有这一步的时候：主任务只会一直停在「基准图修改中」，前端那份「第 N 轮」状态
        又只在内存里，页面一刷新就退化成「第 1 轮 · 待确认」，于是「飞书里图都出来了，
        界面还卡着」。
        """
        rework = self.jobs.get(rework_job_id)
        if rework is None:
            return
        # 只有整条任务收尾了才回填：done/partial（有图没图都可能）、failed、canceled
        if str(rework.status) not in {"done", "partial", "failed", "canceled"}:
            return
        shot = next((t for t in rework.tasks if t.out), None)
        status = "done" if shot is not None else "failed"
        for parent in list(self.jobs.values()):
            if parent.id == rework_job_id:
                continue
            reviews = (parent.params or {}).get("master_reviews")
            if not isinstance(reviews, list) or not reviews:
                continue
            touched = False
            for item in reviews:
                if not isinstance(item, dict):
                    continue
                if str(item.get("rework_job") or "") != rework_job_id:
                    continue
                if str(item.get("status") or "") == status and (
                    shot is None or str(item.get("task") or "") == shot.id
                ):
                    continue  # 已经回填过，别重复写日志
                round_no = int(item.get("round") or 1)
                fresh = str(item.get("status") or "") != status or (
                    shot is not None and str(item.get("task") or "") != shot.id
                )
                item["status"] = status
                item["finished_at"] = time.time()
                shown = round_no + 1   # 卡片上的「第 N 轮」数的是图的轮次
                # 只有还停在「等确认」的主任务才改 stage：已经确认过的老单子别被改写
                waiting = str(parent.status) == "review"
                if shot is not None:
                    item["task"] = shot.id
                    item.pop("error", None)
                    if waiting:
                        parent.stage = (
                            f"基准图第 {shown} 轮已出图，等你确认"
                            "（点「确定」就按这张给其余颜色改色）"
                        )
                    if fresh:
                        parent.add_log(
                            f"改图任务 {rework_job_id} 完成，第 {shown} 轮结果挂到基准图审核"
                        )
                else:
                    why = str(rework.error or rework.stage or "改图没出图")[:80]
                    item["error"] = why
                    if waiting:
                        parent.stage = f"基准图第 {shown} 轮没出图：{why}"
                    if fresh:
                        parent.add_log(
                            f"改图任务 {rework_job_id} 失败，第 {shown} 轮没有出图：{why}",
                            level="warn",
                        )
                touched = True
            if touched:
                parent.save()

    # ---------------------------------------------------------------- notify #
    def public_url(self) -> str:
        """同事能打开的地址（共享模式下自动换成局域网 IP）。"""
        import os
        import socket

        server = self.config.get("server") or {}
        host = str(os.environ.get("WORKBENCH_HOST") or server.get("host") or "127.0.0.1")
        port = int(os.environ.get("WORKBENCH_PORT") or server.get("port") or 8787)
        if host in {"0.0.0.0", "::", ""}:
            host = "127.0.0.1"
            try:
                probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    probe.connect(("8.8.8.8", 80))
                    host = probe.getsockname()[0]
                finally:
                    probe.close()
            except OSError:
                pass
        return f"http://{host}:{port}/"

    def _notify_config(self) -> dict:
        return dict(self.defaults().get("notify") or {})

    def notify_done(self, job: Job) -> bool:
        """任务跑完后推一条到飞书群机器人（webhook），方便人不在电脑前也知道出图好了。"""
        cfg = self._notify_config()
        url = str(cfg.get("feishu_webhook") or self.env.get("FEISHU_WEBHOOK") or "").strip()
        if not cfg.get("enabled") or not url:
            return False
        stats = job.counts()
        est = (job.params.get("estimate") or {})
        cost = (
            f"{est.get('total')} {est.get('currency')}"
            if est.get("priced")
            else "未定价"
        )
        styles = job.params.get("selections") or []
        title = (
            f"{len(styles)} 个款式" if len(styles) > 1
            else (job.params.get("garment_name") or "生图任务")
        )
        where = self.public_url()
        lines = [
            f"【出图完成】{title} · {stats['total']} 张",
            f"成功 {stats['done'] + stats['skipped']} · 失败 {stats['failed']} · 花费 {cost}",
            f"任务号：{job.id}",
            f"取图：{where}（历史任务 → 下载 ZIP）",
        ]
        if job.zip_rel:
            lines.append("（ZIP 已生成，本机可取）")
        if job.params.get("feishu_rows"):
            lines.append(f"飞书已写 {job.params['feishu_rows']} 行")
        text = "\n".join(lines)
        try:
            with httpx.Client(timeout=8.0) as client:
                payload = {
                    "msg_type": "text",
                    "content": {"text": text},
                }
                resp = client.post(url, json=payload)
                ok = resp.status_code < 400
                job.add_log(
                    "已推送飞书群通知" if ok else f"飞书群通知失败：HTTP {resp.status_code}",
                    level="info" if ok else "warn",
                )
                return ok
        except Exception as exc:  # noqa: BLE001
            job.add_log(f"飞书群通知异常：{exc}", level="warn")
            return False

    def _forget(self, job_id: str, task: asyncio.Task) -> None:
        if self._tasks.get(job_id) is task:
            self._tasks.pop(job_id, None)
            self._runners.pop(job_id, None)

    # ----------------------------------------------- 框选标注（改动区域地图） --
    def _annotation_dir(self, job: Job) -> Path:
        root = job.root or self.job_dir(job.id)
        target = root / "work" / "annotations"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _annotation_path(self, job: Job, annotation_id: str) -> Path | None:
        """标注 id（相对任务目录的路径）→ 绝对路径；越界/不存在返回 None。"""
        text = str(annotation_id or "").strip().replace("\\", "/")
        if not text or ".." in text.split("/") or text.startswith("/") or ":" in text:
            return None
        root = job.root or self.job_dir(job.id)
        path = (root / text).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            return None
        return path if path.is_file() else None

    def save_annotation(
        self,
        job_id: str,
        task_id: str,
        data: bytes,
        boxes: list[dict] | None = None,
    ) -> dict:
        """保存运营画好框的基准图（PNG/JPG 字节）+ 框信息，返回 {id, path, boxes}。"""
        job = self.get_job(job_id)
        task = next((t for t in job.tasks if t.id == task_id), None)
        if task is None:
            raise WorkbenchError(f"没有这个任务：{task_id}")
        if not data:
            raise WorkbenchError("标注图是空的")
        suffix = ".png"
        head = data[:8]
        if head[:3] == b"\xff\xd8\xff":
            suffix = ".jpg"
        elif head[:4] == b"RIFF":
            suffix = ".webp"
        stamp = time.strftime("%Y%m%d%H%M%S")
        folder = self._annotation_dir(job)
        target = folder / f"{SAFE_NAME.sub('-', task_id)[:60]}-{stamp}{suffix}"
        target.write_bytes(data)
        rel = target.relative_to(job.root or self.job_dir(job.id)).as_posix()
        clean: list[dict] = []
        for index, box in enumerate(boxes or [], start=1):
            if not isinstance(box, dict):
                continue
            try:
                clean.append(
                    {
                        "n": int(box.get("n") or index),
                        "x": round(float(box.get("x") or 0), 4),
                        "y": round(float(box.get("y") or 0), 4),
                        "w": round(float(box.get("w") or 0), 4),
                        "h": round(float(box.get("h") or 0), 4),
                        "note": " ".join(str(box.get("note") or "").split())[:200],
                    }
                )
            except (TypeError, ValueError):
                continue
        job.params.setdefault("annotations", {})[task_id] = {
            "id": rel,
            "boxes": clean,
            "created_at": time.time(),
        }
        job.add_log(
            f"已保存框选标注：{task_id}，{len(clean)} 个框"
            + ("（" + "；".join(f"框{b['n']}：{b['note']}" for b in clean if b["note"]) + "）"
               if any(b["note"] for b in clean) else "")
        )
        job.save()
        return {"id": rel, "path": str(target), "boxes": clean}

    def annotations_for(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        return dict((job.params or {}).get("annotations") or {})

    # ------------------------------------------------- 基准图审核（先出基准） --
    def review_payload(self, job: Job) -> dict:
        """基准图审核状态：哪些基准待确认、哪些已确认、能不能继续改色。"""
        masters = [t for t in job.tasks if t.master]
        pending = [t.id for t in masters if t.out and not t.approved]
        approved = [t.id for t in masters if t.approved]
        derived = [t for t in job.tasks if t.master_task]
        return {
            "enabled": bool((job.params or {}).get("review_gate")),
            "awaiting": str(job.status) == "review",
            "phase": str((job.params or {}).get("phase") or ""),
            "master_ids": [t.id for t in masters],
            "pending_ids": pending,
            "approved_ids": approved,
            "derived_total": len(derived),
            "derived_done": sum(1 for t in derived if t.out),
            "can_approve": str(job.status) == "review" and bool(pending or approved) and bool(derived),
            "note": "确认后才会按基准图改其余颜色（确认前不花这笔钱）",
        }

    def _master_targets(
        self, job: Job, task_ids: list[str] | None
    ) -> list[Task]:
        wanted = {str(t) for t in (task_ids or [])}
        masters = [t for t in job.tasks if t.master and t.id in wanted] if wanted else [
            t for t in job.tasks if t.master
        ]
        if not masters:
            masters = [t for t in job.tasks if t.master]
        return [t for t in masters if t.out]

    async def approve_master(
        self,
        job_id: str,
        task_ids: list[str] | None = None,
        *,
        adopt: dict | None = None,
        note: str = "",
    ) -> dict:
        """确认基准图 → 按这张基准图给其余颜色改色。

        `adopt`：{基准任务 id: "任务id::task_id"}，用于「对话框里改完，采纳这次结果当基准」。
        """
        job = self.get_job(job_id)
        # 9.6.32 多张基准图时允许「边跑边确认」：以前这里硬拦「任务正在运行中，等这一轮结束再确认」，
        # 一张确认完、另一张还在生成，就得干等整轮。现在确认立刻生效，刚确认这张的派生色
        # 直接排进正在跑的那一轮（并发仍由 runner 的 sem + 飞书闸门控，不绕限流）。
        running = self._tasks.get(job_id)
        live = running if (running is not None and not running.done()) else None
        adopt = adopt or {}
        state = self.review_payload(job)
        if not adopt and not (state["awaiting"] or state["pending_ids"]):
            # 已经确认过 / 压根没开审核的任务：别再点一次，也别把它翻成 running
            raise WorkbenchError(
                "这条任务现在不在「基准图待确认」状态（可能已经确认过或没开审核）"
            )
        targets = [
            t for t in self._master_targets(job, task_ids) if not t.approved
        ]
        if not targets and not adopt:
            raise WorkbenchError("选中的基准图已经确认过了")
        # 采纳：把返工/另一批的成品复制过来，当成这张基准图
        for master_id, source_task in adopt.items():
            master = next((t for t in job.tasks if t.id == master_id), None)
            if master is None:
                raise WorkbenchError(f"没有这张基准任务：{master_id}")
            src_job_id, _, src_task_id = str(source_task).partition("::")
            if not src_task_id:
                src_job_id, src_task_id = job_id, str(source_task)
            src_job = self.get_job(src_job_id)
            src = next((t for t in src_job.tasks if t.id == src_task_id and t.out), None)
            if src is None:
                raise WorkbenchError(f"要采纳的成品不存在：{source_task}")
            src_root = src_job.root or self.job_dir(src_job_id)
            data = (src_root / str(src.out)).read_bytes()
            master.out = master.out or str(Path("outputs") / f"{master.id}.png")
            target = (job.root or self.job_dir(job_id)) / str(master.out)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            master.status = "done"
            master.bytes = len(data)
            master.finished_at = max(master.finished_at or 0, src.finished_at or 0) or time.time()
            master.adopted_from = f"{src_job_id}::{src.id}"
            job.add_log(f"已采纳 {src_job_id} / {src.id} 的成品作为基准图")

        if not targets:
            raise WorkbenchError("没有可确认的基准图（基准还没生成出来）")
        note = " ".join((note or "").split())
        for task in targets:
            task.approved = True
            if note and not task.review_note:
                task.review_note = note[:300]
        job.params["phase"] = "derived"
        job.params["master_approved"] = True
        job.params["awaiting_review"] = False
        job.params["approved_at"] = time.time()
        job.status = "running"
        job.stage = "基准已确认，按基准图改色"
        approved_now = sum(1 for t in job.tasks if t.master and t.approved)
        master_total = sum(1 for t in job.tasks if t.master)
        job.add_log(
            f"基准图已确认 {len(targets)} 张（累计 {approved_now}/{master_total}）："
            f"这些基准对应的其它颜色开始改色"
            + (f"；确认备注：{note}" if note else "")
        )
        job.save()
        # 这一轮还在跑 → 让它在当前这一轮里直接补跑这张基准的派生色，不再另起任务
        if live is not None:
            runner = self._runners.get(job_id)
            if runner is not None and hasattr(runner, "kick"):
                try:
                    runner.kick()
                    job.stage = "基准已确认：这张的颜色已经排进本轮，不用等其它基准出图"
                    job.add_log("边跑边确认：刚确认这张的派生色已排进本轮（并发仍受闸门限制）")
                    job.save()
                except Exception as exc:  # noqa: BLE001 - 插不进就等这轮结束再跑，别报错
                    job.add_log(f"边跑边确认没排进去（{exc}），这轮结束后再跑", level="warn")
                    job.save()
        else:
            try:
                runner = self._runner_for(job)
                task = asyncio.create_task(runner.run(), name=f"job-{job_id}-derived")
                self._tasks[job_id] = task
                self._runners[job_id] = runner
                task.add_done_callback(
                    lambda finished, jid=job_id: (
                        self._record_after(jid, finished), self._forget(jid, finished)
                    )
                )
            except Exception as exc:  # noqa: BLE001
                job.status = "partial"
                job.error = str(exc)
                job.add_log(f"改色启动失败：{exc}", level="error")
                job.save()
                raise WorkbenchError(f"改色启动失败：{exc}") from exc
        # 静默沉淀：不等它、不弹窗。改色已经在跑，评估完写进款式规则与统计
        learned: list[dict] = []
        if self.defaults().get("auto_learn", True):
            signals = bool(note) or bool(job.params.get("master_reviews"))
            if signals:
                task = asyncio.create_task(
                    self.distill_review(job_id, [t.id for t in targets], note=note),
                    name=f"distill-{job_id}",
                )
                self._distill[job_id] = task
                task.add_done_callback(lambda _t, jid=job_id: self._distill.pop(jid, None))
                job.params["distill"] = {"state": "running", "note": note}
                payload_extra = {"state": "running"}
            else:
                payload_extra = {"state": "skipped", "reason": "这次没有对齐要求"}
        else:
            payload_extra = {"state": "off", "reason": "defaults.auto_learn=false"}
        payload = job.as_dict(with_tasks=True, with_log=True, with_prompts=False)
        payload["learned"] = learned
        payload["distill"] = payload_extra
        return payload

    async def reject_master(
        self,
        job_id: str,
        instruction: str,
        task_ids: list[str] | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        account: str = "",
        annotation_id: str = "",
        source_job_id: str = "",
        source_task_ids: list[str] | None = None,
    ) -> Job:
        """基准图不满意 → 以它为准做一次改图（改完可以再采纳回来确认）。

        ``annotation_id``：框选标注图的相对路径（见 save_annotation），
        会作为「改动区域的地图」随改图一起发给生图接口。
        ``source_job_id`` / ``source_task_ids``：**从哪张图重画**。默认是这条任务的基准图；
        第 2 轮还想改时，前端把上一轮的改图任务传进来，于是基于上一轮的结果继续改（第 3 轮…）。
        不改变原任务状态：原任务继续停在「基准待确认」，等运营把改好的图采纳进来。
        """
        job = self.get_job(job_id)
        if source_job_id and source_task_ids:
            src_job = self.get_job(source_job_id)
            wanted = {str(t) for t in source_task_ids}
            targets = [t for t in src_job.tasks if t.id in wanted and t.out]
            if not targets:
                raise WorkbenchError("要接着改的那张图不存在或已经不在任务里")
        else:
            src_job = job
            targets = self._master_targets(job, task_ids)
        if not targets:
            raise WorkbenchError("没有可修改的基准图")
        text = " ".join((instruction or "").split())
        if not text:
            raise WorkbenchError("请说清楚基准图哪里要改")
        annotation_path = ""
        if annotation_id:
            # 标注图存在「被框的那张图」所在的任务里（第 N 轮可能是改图任务）
            annotated = self._annotation_path(src_job, annotation_id)
            if annotated is None:
                raise WorkbenchError("标注图找不到或路径不安全")
            annotation_path = str(annotated)
        rework = await self.create_rework_job(
            src_job.id,
            [t.id for t in targets],
            text,
            provider=provider or str(job.params.get("provider") or ""),
            model=model or str(job.params.get("model") or ""),
            account=account,
            annotation_path=annotation_path,
        )
        # 返工轮次统计（用来看「基准图返工率」有没有降下来）
        for task in targets:
            try:
                garment = self.skill.type_by_alias(
                    task.garment or str(job.params.get("garment_type") or "")
                )
            except KeyError:
                continue
            try:
                style_memory.bump_stats(self.library_dir, garment.slug, review_rounds=1)
            except Exception:  # noqa: BLE001
                pass
        # 审核记录永远挂在「主任务的那张基准图」上：链式改图时 targets 是上一轮的改图任务，
        # 但采纳/统计/复原/沉淀都认主任务的那张，所以这里用调用方给的 task_ids。
        review_ids = [str(t) for t in (task_ids or []) if t] or [t.id for t in targets]
        job.params.setdefault("master_reviews", []).append(
            {
                "at": time.time(),
                "instruction": text,
                "rework_job": rework.id,
                "master_ids": review_ids,
                "from_job": src_job.id,
                "from_tasks": [t.id for t in targets],
                "round": int(rework.params.get("rework_round") or 1),
            }
        )
        round_no = int(rework.params.get("rework_round") or 1)
        job.stage = (
            f"基准图第 {round_no + 1} 轮修改中：改好后采纳，就会按新图改色"
            if round_no > 1 else "基准图修改中：改好后在对话框里确认，就会按新图改色"
        )
        job.add_log(
            f"基准图打回修改：{text}（返工任务 {rework.id}"
            + ("，已附框选标注图" if annotation_path else "")
            + "）"
        )
        job.save()
        self.start(rework.id)
        return rework

    async def revert_rework(self, job_id: str, task_id: str) -> dict:
        """把这张基准图「复原到第 1 轮」：丢掉它的所有改图轮次。

        客户要求「防止越改越糟还回不去」，同时**不能把糟的那一轮记成好的经验**，所以这里
        一起清掉：
          * `params.master_reviews` 里属于这张的条目 —— 确认时 `learn_from_review` 和
            `distill_review` 都是从它里面取「要求原文」去写款式记忆的，不删就会被沉淀；
          * 款式统计里多记的 `review_rounds`（返工轮次）；
          * 还在跑的改图任务尽量停掉（停不掉也不影响，结果没人引用）。
        已确认/已采纳的基准图不允许复原：那时改图结果已经是正式基准了。
        """
        job = self.get_job(job_id)
        task = next((t for t in job.tasks if t.id == task_id and t.master), None)
        if task is None:
            raise WorkbenchError("没有这张基准图")
        if task.approved:
            raise WorkbenchError("这张已经确认过了，不能复原；要重做请用它继续改图")
        reviews = list((job.params or {}).get("master_reviews") or [])
        keep: list[dict] = []
        dropped: list[dict] = []
        for item in reviews:
            ids = list(item.get("master_ids") or []) if isinstance(item, dict) else []
            from_tasks = list(item.get("from_tasks") or []) if isinstance(item, dict) else []
            hit = task_id in ids or any(str(t).startswith(task_id) for t in from_tasks)
            if isinstance(item, dict) and hit:
                dropped.append(item)
            else:
                keep.append(item)
        if not dropped:
            raise WorkbenchError("这张基准图还没有改图轮次，不用复原")
        job.params["master_reviews"] = keep

        # 停在半路的改图任务：请求取消（失败也无所谓，结果不再被引用）
        for item in dropped:
            rework_id = str(item.get("rework_job") or "")
            if not rework_id:
                continue
            running = self._tasks.get(rework_id)
            if running is not None and not running.done():
                try:
                    running.cancel()
                except Exception:  # noqa: BLE001
                    pass

        # 返工轮次统计减回去（不能出现负数）
        slug = task.garment or str(job.params.get("garment_type") or "")
        try:
            garment = self.skill.type_by_alias(slug)
        except KeyError:
            garment = None
        if garment is not None:
            try:
                stats = style_memory.load_stats(self.library_dir, garment.slug) or {}
                current = int(stats.get("review_rounds") or 0)
                back = min(len(dropped), max(0, current))
                if back:
                    style_memory.bump_stats(self.library_dir, garment.slug, review_rounds=-back)
            except Exception:  # noqa: BLE001 - 统计出错不影响复原
                pass

        task.review_note = ""
        rounds = len(dropped)
        job.add_log(
            f"复原到第 1 轮：{task.color_name or task.color} 丢弃 {rounds} 轮改图记录，"
            "这一轮的要求不会写进款式记忆"
        )
        job.save()
        return {"dropped": rounds, "task_id": task_id, "job": job.as_dict()}

    async def distill_review(
        self,
        job_id: str,
        task_ids: list[str] | None = None,
        *,
        note: str = "",
        online: bool | None = None,
    ) -> dict:
        """拍板后的**静默评估 + 沉淀**：判一条要求是「这个款式的通病」还是「这一张的微调」。

        结果写进 job.params["distill"]，并在任务日志里留一行；失败只记警告，绝不影响出图。
        """
        from . import align_judge

        job = self.get_job(job_id)
        targets = self._master_targets(job, task_ids)
        signals: list[tuple[str, str]] = []       # (款式 slug, 要求原文)
        review_rounds = 0
        for task in targets:
            slug = task.garment or str(job.params.get("garment_type") or "")
            if task.review_note:
                signals.append((slug, task.review_note))
            for review in (job.params.get("master_reviews") or []):
                if isinstance(review, dict) and task.id in (review.get("master_ids") or []):
                    text = str(review.get("instruction") or "").strip()
                    if text:
                        signals.append((slug, text))
                        review_rounds += 1
        if note:
            for task in targets:
                signals.append((task.garment or str(job.params.get("garment_type") or ""), note))
        # 去重（同一款式同一句只说一次）
        seen: set[tuple[str, str]] = set()
        unique = [item for item in signals if item[1] and not (item in seen or seen.add(item))]
        result = align_judge.DistillResult()
        if not unique:
            job.params["distill"] = {"state": "done", "total": 0, "reason": "没有可评估的要求"}
            job.save()
            return job.params["distill"]
        cfg = self.distill_cfg()
        if online is None:
            online = bool(self.defaults().get("auto_learn_online", True))
        for slug, text in unique:
            try:
                garment = self.skill.type_by_alias(slug) if slug else None
            except KeyError:
                garment = None
            phrase = (garment.phrase if garment is not None else "")
            verdict = await asyncio.to_thread(
                align_judge.judge, text, cfg=cfg, garment_phrase=phrase, online=online
            )
            result.verdicts.append(verdict)
            if garment is None:
                continue
            item = await asyncio.to_thread(
                style_memory.remember,
                self.library_dir,
                garment.slug,
                text,
                verdict=verdict,
                source={"job_id": job_id, "origin": "distill"},
            )
            if item and not item.get("duplicate"):
                style_memory.bump_stats(
                    self.library_dir,
                    garment.slug,
                    learned=1,
                    **({"promoted": 1} if verdict.promote else {}),
                )
        if review_rounds:
            for task in targets:
                slug = task.garment or str(job.params.get("garment_type") or "")
                try:
                    garment = self.skill.type_by_alias(slug) if slug else None
                except KeyError:
                    garment = None
                if garment is not None:
                    style_memory.bump_stats(
                        self.library_dir, garment.slug, review_rounds=review_rounds
                    )
        summary = result.as_dict()
        summary["state"] = "done"
        job.params["distill"] = summary
        job.params["distill_at"] = time.time()
        promoted = [v for v in result.verdicts if v.promote]
        dropped = [v for v in result.verdicts if v.scope != "style"]
        job.add_log(
            f"静默评估 {len(result.verdicts)} 条对齐要求："
            f"{len(result.remembered)} 条记进款式规则（其中 {len(promoted)} 条升级为款式级硬规则），"
            f"{len(dropped)} 条判为一次性微调（不注入提示词）"
        )
        for verdict in result.verdicts:
            job.add_log(
                f"  · [{verdict.scope}/{verdict.kind} {verdict.importance:.2f}] "
                f"{verdict.text[:40]} → {verdict.reason or verdict.clause[:40]}"
            )
        # 让改动立刻对下一条提示词生效
        try:
            self.reload_skill()
        except Exception:  # noqa: BLE001
            pass
        job.save()
        return summary

    async def await_distill(self, job_id: str, timeout: float = 90.0) -> dict:
        """等这条任务的静默评估结束（测试/收尾用；界面上不需要等）。"""
        task = self._distill.get(job_id)
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
        job = self.get_job(job_id)
        return dict((job.params or {}).get("distill") or {})

    def learn_from_review(self, job_id: str, task_ids: list[str] | None = None) -> list[dict]:
        """把本次确认的备注/审核记录沉淀进该款式的 lessons（供下次生成直接用）。"""
        job = self.get_job(job_id)
        targets = self._master_targets(job, task_ids)
        added: list[dict] = []
        for task in targets:
            notes = [task.review_note] if task.review_note else []
            for review in (job.params.get("master_reviews") or []):
                if isinstance(review, dict) and task.id in (review.get("master_ids") or []):
                    notes.append(str(review.get("instruction") or ""))
            for text in notes:
                item = style_memory.remember(
                    self.library_dir,
                    task.garment or str(job.params.get("garment_type") or ""),
                    str(text),
                    source={"job_id": job_id, "task_id": task.id},
                    llm_cfg=self.distill_cfg(),
                )
                if item:
                    added.append(item)
        return added

    def distill_cfg(self) -> dict:
        cfg = dict(self.defaults().get("harness") or {})
        cfg["api_key"] = str(self.env.get(str(cfg.get("api_key_env") or ""), ""))
        return cfg

    def retry(
        self, job_id: str, task_ids: list[str] | None = None, *, only: str = ""
    ) -> dict:
        job = self.get_job(job_id)
        if job_id in self._tasks and not self._tasks[job_id].done():
            raise WorkbenchError("任务正在运行中")
        targets, wanted = _retry_targets(job, task_ids, only)
        if not targets:
            if only == "rate_limit":
                raise WorkbenchError("没有被限流打回来的图片需要重试")
            raise WorkbenchError("没有失败的任务需要重试")
        job.cancel_requested = False
        job.error = None
        job.zip_rel = None
        runner = self._runner_for(job)
        task = asyncio.create_task(
            runner.run(retry_failed_only=True, task_ids=wanted or None),
            name=f"retry-{job_id}",
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda finished, jid=job_id: self._forget(jid, finished))
        return job.as_dict(with_tasks=False, with_log=False)

    def cancel(self, job_id: str) -> dict:
        """取消任务：跑着的就中断，停在「等确认基准图」的就直接把整批收尾。

        9.6.28：以前这里只置 `cancel_requested` 并 cancel 掉 runner 的 asyncio 任务。
        可两段式任务停在 `review` 等人工确认时**根本没有 runner 在跑**，于是点了取消什么都不变，
        界面永远「等你点确定」，整批把后面的活全堵住。现在没人跑就直接收尾成 canceled
        （已出的成品保留，没开始的颜色标 cancelled）。
        """
        job = self.get_job(job_id)
        job.cancel_requested = True
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            job.add_log("收到取消请求（正在生成的这张会中断）", level="warn")
            job.save()
            # 带 tasks 返回：前端可以直接整页重画（不带任务的 dict 会把画廊清空）
            return job.as_dict(with_tasks=True, with_log=True)
        waiting = 0
        for item in job.tasks:
            if item.status in {"pending", "queued", "running"}:
                item.status = "canceled"
                item.error = item.error or "已取消"
                waiting += 1
        if str(job.status) not in {"done", "partial", "failed", "canceled"}:
            job.status = "canceled"
            job.stage = "已取消" + (f"：{waiting} 张没开始的颜色已跳过" if waiting else "")
            job.finished_at = time.time()
        job.add_log(
            f"任务已取消" + (f"，跳过 {waiting} 张没开始的成品" if waiting else ""),
            level="warn",
        )
        job.save()
        return job.as_dict(with_tasks=True, with_log=True)

    # ------------------------------------------------------- 单张/多张暂停 --
    def pause_tasks(self, job_id: str, task_ids: list[str]) -> dict:
        """暂停指定的几张（可以包含基准图）。

        - 还没开始（pending / queued）：标成 paused，runner 不再开跑；
        - 基准图（已出图、在等确认）：标成 paused，从审核条上撤下来，不再拦着这批；
          它派生出来的、还没开始的颜色一起暂停（否则永远等不到基准）；
        - 正在生成：允许标暂停，但那一次接口调用停不下来，跑完这张就停（不重试、不派生）。
        """
        job = self.get_job(job_id)
        wanted = [str(t) for t in (task_ids or []) if t]
        if not wanted:
            raise WorkbenchError("请先选中要暂停的成品")
        targets = [t for t in job.tasks if t.id in wanted]
        if not targets:
            raise WorkbenchError("选中的成品不在这个任务里")
        changed: list[str] = []
        cascade: list[str] = []
        for task in targets:
            if task.paused:
                continue
            task.paused = True
            task.paused_from = task.status
            task.paused_by = ""
            # 已出图的基准图（done）保留成品，只是从审核条上撤下来
            if task.status != "done":
                task.status = "paused"
            changed.append(task.id)
            if task.master:
                for other in job.tasks:
                    if other.id == task.id or other.paused or other.out:
                        continue
                    if other.master_task == task.id:
                        # 同款派生色：基准停了它也跑不了（记下是被这张连带暂停的）
                        other.paused = True
                        other.paused_from = other.status
                        other.paused_by = task.id
                        other.status = "paused"
                        cascade.append(other.id)
        if changed:
            job.add_log(
                f"已暂停 {len(changed)} 张" + (f"（连带 {len(cascade)} 张派生色）" if cascade else ""),
                level="warn",
            )
        self._settle_if_only_paused(job)
        job.save()
        return job.as_dict(with_tasks=True, with_log=True)

    def resume_tasks(self, job_id: str, task_ids: list[str]) -> dict:
        """继续：把暂停的几张放回队列；暂停基准图时连带暂停的派生色一起放回。"""
        job = self.get_job(job_id)
        wanted = [str(t) for t in (task_ids or []) if t]
        if not wanted:
            raise WorkbenchError("请先选中要继续的成品")
        targets = [t for t in job.tasks if t.id in wanted and t.paused]
        if not targets:
            raise WorkbenchError("选中的成品没有在暂停状态")
        for task in targets:
            self._resume_one(task)
            if task.master:
                for other in job.tasks:
                    if other.paused and other.paused_by == task.id:
                        self._resume_one(other)
        job.add_log(f"继续 {len(targets)} 张：放回队列", level="info")
        # 还有基准图没确认 → 回到审核态，不急着开 runner
        awaiting = [t for t in job.tasks if t.master and t.out and not t.approved and not t.paused]
        if awaiting:
            job.status = "review"
            job.stage = "基准图已出，等你确认（确认后按这张图改其余颜色）"
            job.save()
            return job.as_dict(with_tasks=True, with_log=True)
        if str(job.status) in {"done", "partial", "failed", "canceled"}:
            job.status = "running"
            job.stage = "继续生成中"
        job.save()
        if all(str(t.status) not in {"pending", "queued"} for t in job.tasks):
            return job.as_dict(with_tasks=True, with_log=True)
        if job_id not in self._tasks or self._tasks[job_id].done():
            try:
                self.start(job_id)
            except WorkbenchError:
                pass
        return job.as_dict(with_tasks=True, with_log=True)

    @staticmethod
    def _resume_one(task) -> None:
        task.paused = False
        back = task.paused_from or "pending"
        task.paused_from = ""
        task.paused_by = ""
        task.status = back if (back == "done" and task.out) else "pending"
        task.error = None

    def cancel_style(self, job_id: str, task_ids: list[str]) -> dict:
        """取消这一款（9.6.33）：基准图 + 这款还没开始的颜色一起取消，并且都不再显示。

        和「取消这批」的区别：这是针对某一张基准图（某个款式）的，其它款式照跑；
        和「暂停」的区别：暂停是还能继续，这里是本次任务不再生成这一款。
        已出的成品文件不删，只是标记 hidden 从界面上撤下来（历史/日志还在）。
        """
        job = self.get_job(job_id)
        wanted = [str(t) for t in (task_ids or []) if t]
        if not wanted:
            raise WorkbenchError("请先选中要取消的款式")
        targets = [t for t in job.tasks if t.id in wanted]
        if not targets:
            raise WorkbenchError("选中的款式不在这个任务里")
        hidden: list[str] = []
        for task in targets:
            task.paused = False
            task.paused_from = ""
            task.paused_by = ""
            # 整款取消了：状态统一标 canceled（成品文件保留，但这批不再算它）
            task.status = "canceled"
            task.error = task.error or "本款已取消"
            task.hidden = True
            task.finished_at = task.finished_at or time.time()
            hidden.append(task.id)
            # 这款还没开始的颜色一起撤掉（已经出图的留着，钱已经花了）
            if task.master:
                for other in job.tasks:
                    if other.id == task.id or other.hidden or other.out:
                        continue
                    if other.master_task == task.id:
                        other.paused = False
                        other.status = "canceled"
                        other.error = other.error or "本款已取消"
                        other.hidden = True
                        other.finished_at = other.finished_at or time.time()
                        hidden.append(other.id)
        job.add_log(
            f"已取消 {len(targets)} 个款式（连带 {len(hidden) - len(targets)} 张没开始的颜色），"
            "这些卡片不再显示",
            level="warn",
        )
        self._settle_if_only_paused(job)
        if str(job.status) in {"review", "running", "queued", "packaging"}:
            live = [t for t in job.tasks if not t.hidden]
            waiting_master = [
                t for t in live if t.master and t.out and not t.approved and not t.paused
            ]
            if not waiting_master and not any(
                t.status in {"pending", "queued", "running"} for t in live
            ):
                job.status = "partial" if any(t.out for t in live) else "canceled"
                job.stage = "已取消该款式：其余已出的成品保留"
                job.finished_at = time.time()
        job.save()
        return job.as_dict(with_tasks=True, with_log=True)

    def _settle_if_only_paused(self, job: Job) -> None:
        """剩下的活全被暂停时把任务收尾，别让界面一直转圈等一个永远不动的队列。"""
        live = [t for t in job.tasks if t.status in {"pending", "queued", "running"}]
        if live:
            return
        if str(job.status) in {"done", "partial", "failed", "canceled", "paused"}:
            return
        paused = [t for t in job.tasks if t.paused]
        done = sum(1 for t in job.tasks if t.out)
        job.status = "partial" if done else "canceled"
        job.stage = (
            f"已暂停：{len(paused)} 张没生成（选中后点「继续」可以接着跑）"
            if paused else "已收尾"
        )
        job.finished_at = time.time()
        job.add_log(job.stage, level="warn")

    def delete(self, job_id: str) -> None:
        job = self.get_job(job_id)
        task = self._tasks.pop(job_id, None)
        if task and not task.done():
            task.cancel()
        job.cancel_requested = True
        self.jobs.pop(job_id, None)
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
        for gate in self._gates.values():
            try:
                await gate.close()
            except Exception:
                pass
        for provider in self.providers.values():
            try:
                await provider.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ files #
    def image_path(self, job_id: str, task_id: str) -> Path:
        job = self.get_job(job_id)
        for task in job.tasks:
            if task.id == task_id and task.out:
                path = (job.root or Path(".")) / task.out
                if path.is_file():
                    return path
        raise WorkbenchError("图片不存在")

    def thumbnail_path(self, job_id: str, task_id: str, width: int = 520) -> Path | None:
        """生成/复用列表用缩略图（WebP），缓存到 <job>/thumbs/。

        失败返回 None，调用方退回原图。同样**不能写进 staging/**，否则 skill 的
        打包脚本会把 thumbs 目录当成一个 item，数图数量对不上而整批打包失败。
        """
        try:
            from PIL import Image

            job = self.get_job(job_id)
            root = job.root or self.job_dir(job_id)
            source = self.image_path(job_id, task_id)
            target = root / "thumbs" / f"{Path(source.name).stem}@{width}.webp"
            if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as handle:
                image = handle.convert("RGB")
                if image.width > width:
                    ratio = width / float(image.width)
                    image = image.resize(
                        (width, max(1, int(image.height * ratio))), Image.LANCZOS
                    )
                image.save(target, format="WEBP", quality=82, method=4)
            return target
        except Exception:  # noqa: BLE001 - 缩略图失败就让前端拿原图
            return None

    def zip_path(self, job_id: str) -> Path:
        job = self.get_job(job_id)
        if not job.zip_rel:
            raise WorkbenchError("打包文件尚未生成")
        path = (job.root or Path(".")) / job.zip_rel
        if not path.is_file():
            raise WorkbenchError("打包文件不存在")
        return path
