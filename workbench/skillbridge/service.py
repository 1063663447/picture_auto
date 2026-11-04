"""Workbench service: owns config, skill, providers, job registry and job tasks."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import zipfile
from pathlib import Path

from . import feishu as feishu_mod
from . import refine
from .config import WORKBENCH_DIR, load_config, load_env
from .pricing import Pricer, UsageLedger
from .job import Job, Task, new_job_id, normalise_item
from .providers import build_providers
from .providers.base import Provider
from .runner import JobRunner
from .skill import ColorRef, GarmentType, Skill, load_skill

SAFE_NAME = re.compile(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_MODEL_HINT = re.compile(
    r"(image|dall-?e|flux|seedream|seededit|kolors|wanx|imagen|nano-banana|"
    r"grok-imagine-image|midjourney|qwen-image|\bsd-?xl\b|photoshop|edit)",
    re.I,
)
NON_IMAGE_HINT = re.compile(r"(video|veo|seedance|kling|sora|animate|tts|whisper|embed)", re.I)
MODEL_CACHE_TTL = 600.0


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
        self._load_existing()

    # ------------------------------------------------------------------ setup #
    def reload_skill(self) -> Skill:
        self.skill = load_skill(self.config["skill_root"])
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
        return [provider.info() for provider in visible]

    def defaults(self) -> dict:
        return self.config.get("defaults") or {}

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

    def _offline_models(self, provider_name: str, reason: str) -> dict:
        """网关 /models 抖动时：先用上次成功的快照，再退回 pricing.json 的模型名。"""
        snapshot = self._read_models_cache(provider_name)
        if snapshot and snapshot.get("models"):
            payload = dict(snapshot)
            payload["cached"] = True
            payload["degraded"] = f"{reason}（使用上次拉取的快照）"
            self._model_cache[provider_name] = (time.time(), payload)
            return payload
        rows = []
        for model_id in sorted(self.pricer.raw):
            price = self.pricer.price(model_id, str(self.defaults().get("size")))
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

    async def list_models(self, name: str | None, *, refresh: bool = False) -> dict:
        """Pull the provider's model catalogue so the operator can pick a real id."""
        provider = self._get_provider_any(name)
        cached = self._model_cache.get(provider.name)
        if cached and not refresh and time.time() - cached[0] < MODEL_CACHE_TTL:
            payload = dict(cached[1])
            payload["cached"] = True
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
            price = self.pricer.price(model["id"], str(self.defaults().get("size")))
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
        return payload

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
        return job

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
        account: str = "",
    ) -> Job:
        if not uploads:
            raise WorkbenchError("请先上传至少一张图片")

        garment = self._resolve_garment(garment_type)
        try:
            selected = garment.resolve_colors(colors)
        except KeyError as exc:
            raise WorkbenchError(str(exc)) from exc
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

        params = {
            "garment_type": garment.slug,
            "garment_name": garment.display_name,
            "colors": [color.label for color in selected],
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
            "quality": str(quality or "high"),
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
            "account": str(account or ""),
            "estimate": self.pricer.estimate(chosen_model, 0, str(size or "auto")),
        }

        job = Job(id=job_id, params=params, batch_name=garment.batch_name)
        job.root = root
        job.add_log(
            f"任务创建：{garment.display_name}，{len(selected)} 色，"
            f"{len(uploads)} 张原图，接口 {provider_obj.name}"
        )
        # plan up-front so the UI can show the whole task list immediately
        try:
            JobRunner(
                job,
                self.skill,
                garment,
                selected,
                provider_obj,
                data_dir=self.data_dir,
            ).plan()
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(root, ignore_errors=True)
            raise WorkbenchError(f"任务规划失败：{exc}") from exc

        # 并发默认等于预计调用次数（用户要求：一次把能发的都发出去）
        if str(concurrency).strip().lower() in {"", "auto", "none"} or concurrency is None:
            job.params["concurrency"] = max(1, min(64, len(job.tasks)))
            job.add_log(f"并发自动设为 {job.params['concurrency']}（= 预计调用次数）")

        job.params["estimate"] = self.pricer.estimate(
            chosen_model, len(job.tasks), str(job.params.get("size") or "auto")
        )
        try:
            self._quota_guard(
                str(params.get("account") or ""),
                chosen_model,
                len(job.tasks),
                str(job.params.get("size") or "auto"),
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
    def pricing_payload(self) -> dict:
        self.pricer.refresh_from_feishu(self.feishu_config)
        payload = self.pricer.table()
        payload["usage"] = self.usage.summary()
        payload["default_model"] = str(
            (self.providers.get("terra") or next(iter(self.providers.values()))).cfg.get("model") or ""
        )
        return payload

    def usage_payload(self) -> dict:
        return self.usage.summary()

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
                    "detail": str(entry.get("detail") or ""),
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

        by_model: dict[str, dict] = {}
        by_day: dict[str, dict] = {}
        for row in rows:
            model = row["model"] or "（未知）"
            bucket = by_model.setdefault(model, {"model": model, "images": 0, "cost": 0.0})
            bucket["images"] += int(row["images"] or 0)
            bucket["cost"] += float(row["cost"] or 0)
            day = time.strftime("%Y-%m-%d", time.localtime(row["at"] or 0))
            day_bucket = by_day.setdefault(day, {"day": day, "images": 0, "cost": 0.0})
            day_bucket["images"] += int(row["images"] or 0)
            day_bucket["cost"] += float(row["cost"] or 0)

        for bucket in by_model.values():
            bucket["cost"] = round(bucket["cost"], 4)
        for bucket in by_day.values():
            bucket["cost"] = round(bucket["cost"], 4)
        days = sorted(by_day.values(), key=lambda item: item["day"])[-14:]

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
            },
            "quota": {
                "accounts_with_quota": len(quotas),
                "total_quota": round(sum(float(r.get("quota") or 0) for r in quotas), 4),
                "used": round(sum(float(r.get("cost") or 0) for r in quotas), 4),
            },
            "accounts": accounts,
            "by_model": sorted(by_model.values(), key=lambda item: -item["images"]),
            "by_day": days,
            "recent": rows[:20],
            "providers": [
                {"name": str(p.get("name")), "label": str(p.get("label"))}
                for p in self.provider_payload()
            ],
            "pricing_source": self.pricer.source,
        }

    def _quota_guard(self, account: str, model: str, count: int, size: str) -> None:
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
        est = self.pricer.estimate(model, count, size)
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
        """把任务写进飞书多维表格（含图片附件）；未配置就静默跳过。返回写入行数。"""
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
        price = self.pricer.price(model, size)
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
                            row["成品图"] = client.attach(root / str(task.out))
                        if task.source:
                            ref = self._color_ref_path(task.color_name, garment_slug)
                            if ref is not None and ref.is_file():
                                row["参考图"] = client.attach(ref)
                        if cfg.upload_source and task.source:
                            row["原图"] = client.attach(root / str(task.source))
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
            return len(ids)
        except Exception as exc:  # noqa: BLE001 - 同步失败不影响出图
            job.add_log(f"飞书写入失败：{exc}", level="warn")
            job.save()
            return 0

    def estimate_for(self, model: str, count: int, size: str | None = None) -> dict:
        return self.pricer.estimate(model, count, size or str(self.defaults().get("size") or "auto"))

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
    ) -> Job:
        """以选中的成品为参考图返工（改图）。"""
        source_job = self.get_job(source_job_id)
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
        rework_mode = _resolve_pattern_mode(pattern_mode, instruction)

        job_id = new_job_id()
        root = self.job_dir(job_id)
        (root / "inputs").mkdir(parents=True)
        (root / "work").mkdir()
        (root / "staging").mkdir()
        (root / "outputs").mkdir()

        tasks: list[Task] = []
        briefs: list[str] = []
        refiners: set[str] = set()
        for index, task in enumerate(selected, start=1):
            original = source_root / str(task.out)
            if not original.is_file():
                continue
            folder = task.item or f"item-{index:02d}"
            target_dir = root / "inputs" / folder
            target_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{folder}_v2" if not task.view else f"{folder}-{task.view}_v2"
            target = target_dir / f"{stem}{original.suffix or '.png'}"
            target.write_bytes(original.read_bytes())

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
                )
            refiners.add(used)
            new_task = Task(
                id=f"{folder}__{task.color}-v2",
                item=folder,
                view="",
                color=f"{task.color}-v2",
                color_hex=task.color_hex,
                color_name=f"{task.color_name}·改图",
                source=str(target.relative_to(root)).replace("\\", "/"),
                prompt=prompt,
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
            "source_tasks": [t.id for t in selected],
            "brief": briefs[0] if briefs else "",
            "account": str(account or source_job.params.get("account") or ""),
            "pattern_mode": rework_mode,
        }

        params["estimate"] = self.pricer.estimate(
            chosen_model, len(tasks), str(params.get("size") or "auto")
        )
        try:
            self._quota_guard(
                str(params.get("account") or ""),
                chosen_model,
                len(tasks),
                str(params.get("size") or "auto"),
            )
        except WorkbenchError:
            shutil.rmtree(root, ignore_errors=True)
            raise
        batch = f"{garment.slug}-rework-batch"
        job = Job(id=job_id, params=params, batch_name=batch)
        job.root = root
        job.tasks = tasks
        job.total = len(tasks)
        job.add_log(f"改图任务：{len(tasks)} 张，接口 {provider_obj.name}，提示词由 {params['refiner']} 生成")
        job.add_log(f"用户需求：{instruction}")
        job.save()
        self.jobs[job_id] = job
        return job

    def export_zip(self, job_id: str, task_ids: list[str] | None = None) -> Path:
        """把选中的成品打成 ZIP（不传 task_ids 则导出全部成功图片）。"""
        job = self.get_job(job_id)
        root = job.root or self.job_dir(job_id)
        wanted = {str(t) for t in (task_ids or [])}
        files = [
            root / str(task.out)
            for task in job.tasks
            if task.out and (not wanted or task.id in wanted)
        ]
        files = [path for path in files if path.is_file()]
        if not files:
            raise WorkbenchError("没有可导出的图片")
        export_dir = root / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        suffix = "selected" if wanted else "all"
        target = export_dir / f"{job.batch_name or job_id}-{suffix}-{stamp}.zip"

        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                try:
                    relative = path.relative_to(root / "staging")
                except ValueError:
                    relative = Path(path.name)
                archive.write(path, relative)
        job.add_log(f"导出 {len(files)} 张：{target.name}")
        job.save()
        return target

    def _provider_sem(self, provider: Provider) -> asyncio.Semaphore | None:
        """Optional cap shared by every job using the same provider.

        Set `global_concurrency` on a provider in config.json when several jobs may
        run at once and the upstream account has a hard concurrency limit.
        """
        cap = int(provider.setting("global_concurrency", 0) or 0)
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
        if not is_rework:
            try:
                colors = garment.resolve_colors(job.params.get("colors") or [])
            except KeyError as exc:
                raise WorkbenchError(str(exc)) from exc
        provider = self.get_provider(job.params.get("provider"))
        return JobRunner(
            job,
            self.skill,
            garment,
            colors,
            provider,
            data_dir=self.data_dir,
            provider_sem=self._provider_sem(provider),
        )

    def start(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if job_id in self._tasks and not self._tasks[job_id].done():
            raise WorkbenchError("任务正在运行中")
        job.cancel_requested = False
        runner = self._runner_for(job)
        task = asyncio.create_task(runner.run(), name=f"job-{job_id}")
        self._tasks[job_id] = task
        task.add_done_callback(
            lambda finished, jid=job_id: (self._record_after(jid, finished), self._forget(jid, finished))
        )
        return job.as_dict(with_tasks=False, with_log=False)

    def _record_after(self, job_id: str, task: asyncio.Task) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        # 写飞书多维表格（同步 IO，丢线程池，别堵事件循环）
        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self.sync_to_feishu, job)
        except RuntimeError:
            pass
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

    def _forget(self, job_id: str, task: asyncio.Task) -> None:
        if self._tasks.get(job_id) is task:
            self._tasks.pop(job_id, None)

    def retry(self, job_id: str, task_ids: list[str] | None = None) -> dict:
        job = self.get_job(job_id)
        if job_id in self._tasks and not self._tasks[job_id].done():
            raise WorkbenchError("任务正在运行中")
        wanted = {str(t) for t in (task_ids or [])}
        targets = [
            t for t in job.tasks if t.status == "failed" and (not wanted or t.id in wanted)
        ]
        if not targets:
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
        job = self.get_job(job_id)
        job.cancel_requested = True
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        job.add_log("收到取消请求", level="warn")
        job.save()
        return job.as_dict(with_tasks=False, with_log=False)

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

    def zip_path(self, job_id: str) -> Path:
        job = self.get_job(job_id)
        if not job.zip_rel:
            raise WorkbenchError("打包文件尚未生成")
        path = (job.root or Path(".")) / job.zip_rel
        if not path.is_file():
            raise WorkbenchError("打包文件不存在")
        return path
