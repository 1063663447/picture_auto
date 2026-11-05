"""Concurrent execution engine: plan tasks, call the image API in parallel, package."""

from __future__ import annotations

import asyncio
import collections
import io
import random
import shutil
import time
from dataclasses import replace
from pathlib import Path

from PIL import Image

from . import prompts
from . import subject as subject_mod
from .job import Job, Task, job_stamp, normalise_item, view_of
from .gate import GateBusy
from .packaging import package_outputs
from .providers.base import EditRequest, Provider, ProviderError, sniff_extension
from .skill import ColorRef, GarmentType, Skill

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
# 被网关/并发限制打回来的错误特征（用于「一键重试被限流的图」）
RATE_LIMIT_HINTS = (
    "429", "rate limit", "rate_limit", "ratelimit", "too many requests", "too many",
    "concurren", "并发", "限流", "频率", "qps", "tpm", "rpm", "overloaded", "busy",
    "capacity", "请求过多", "排队", "闸门",
)


def is_rate_limited(message: str) -> bool:
    text = str(message or "").lower()
    return any(hint in text for hint in RATE_LIMIT_HINTS)
# staging/ 下只允许出现 "每个 item 一个目录"。缓存目录曾经被写进 staging（thumbs / derived），
# 被 skill 的打包脚本当成 item 数图 → 「contains 2 images; expected 3」整批失败。
# 这里在打包前兜底清掉，防止历史数据/旧版本留下的目录再次踩坑。
STAGING_JUNK_DIRS = {"thumbs", "derived", "exports", ".cache"}


def clean_staging(staging_dir: Path) -> list[str]:
    """打包前把 staging 收拾干净：只留「每个 item 一个目录」。

    返回被清掉的目录名（缓存目录），方便写日志/排查。
    """
    removed: list[str] = []
    if not staging_dir.is_dir():
        return removed
    for item_dir in sorted(p for p in staging_dir.iterdir() if p.is_dir()):
        if item_dir.name in STAGING_JUNK_DIRS:
            shutil.rmtree(item_dir, ignore_errors=True)
            removed.append(item_dir.name)
            continue
        if not any(p.is_file() for p in item_dir.iterdir()):
            try:
                item_dir.rmdir()
            except OSError:
                pass
    return removed


class RateLimiter:
    """Simple requests-per-minute gate shared by every task of one job."""

    def __init__(self, rpm: int) -> None:
        self.rpm = max(0, int(rpm or 0))
        self._lock = asyncio.Lock()
        self._stamps: collections.deque[float] = collections.deque()

    async def acquire(self) -> None:
        if not self.rpm:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._stamps and now - self._stamps[0] >= 60.0:
                    self._stamps.popleft()
                if len(self._stamps) < self.rpm:
                    self._stamps.append(now)
                    return
                wait = 60.0 - (now - self._stamps[0]) + 0.05
                await asyncio.sleep(max(0.05, wait))


class JobRunner:
    def __init__(
        self,
        job: Job,
        skill: Skill,
        garment: GarmentType,
        colors: list[ColorRef],
        provider: Provider,
        *,
        selections: list[tuple[GarmentType, list[ColorRef]]] | None = None,
        data_dir: Path,
        provider_sem: asyncio.Semaphore | None = None,
        gate=None,
    ) -> None:
        self.job = job
        self.skill = skill
        # 多款式：selections = [(款式, [颜色...]), ...]；单款式时只有一个元素
        self.selections: list[tuple[GarmentType, list[ColorRef]]] = [
            (g, list(c)) for g, c in (selections or [(garment, colors)])
        ]
        self.garment = self.selections[0][0]
        self.colors = self.selections[0][1]
        self.multi_style = len(self.selections) > 1
        self.provider = provider
        self.data_dir = data_dir

        params = job.params
        self.mode = str(params.get("mode") or "single_pass")
        # 「并发数」已从界面去掉：默认**全推**，超过飞书闸门额度 / 接口上限的自动排队，
        # 排队位置来自飞书表轮询（见 _queue_payload）。
        # unlimited 的接口（实测能扛）不设本机并发上限；其它接口仍有安全上限
        self.unlimited = bool(provider.setting("unlimited", False))
        limit = 1000 if self.unlimited else 128
        try:
            self.concurrency = max(1, min(limit, int(params.get("concurrency") or 4)))
        except (TypeError, ValueError):
            self.concurrency = max(1, min(limit, len(job.tasks) or 4))
        self.max_retries = max(1, int(params.get("max_retries") or 3))
        # 图片接口常见 60~120s；超时往往代表上游仍在跑（可能已出图），自动重试会重复计费
        self.retry_on_timeout = bool(params.get("retry_on_timeout", False))
        self.timeout = float(params.get("timeout_sec") or 180)
        self.send_reference = bool(params.get("send_reference", True)) and provider.max_images > 1
        self.output_format = str(params.get("output_format") or "auto").lower()
        self.input_fidelity = str(params.get("input_fidelity") or "").strip() or None
        self.extra_requirements = str(params.get("extra_requirements") or "").strip()
        # 图案策略：用户说了算（preserve / per_color_variant / replace / free）
        self.pattern_mode = str(params.get("pattern_mode") or "").strip().lower()
        if self.pattern_mode not in prompts.PATTERN_MODES:
            self.pattern_mode = (
                prompts.detect_pattern_mode(self.extra_requirements) or "preserve"
            )
        self.pattern_brief = str(params.get("pattern_brief") or "").strip()
        # 主体：garment（原图只有衣服）/ person（真人上身）/ auto（按原图判断）。
        # 判不准就按 person 走中性提示词，见 subject.detect
        self.subject_override = subject_mod.normalise(params.get("subject"))
        # 出图后发现「又画了个人」要不要自动重画一次（默认只警告，不偷偷多花钱）
        self.retry_on_person = bool(params.get("retry_on_person", False))
        self.negative = prompts.full_negative(self.skill, self.pattern_mode)
        # 多款式时每个款式有各自的袖口工艺（平口/螺纹），否定词必须按款取，
        # 不能共用第一款的：否则长袖 T 恤会拿到卫衣的否定、或者反过来
        self._negatives: dict[str, str] = {}
        # 排队信息：任务 id → 最近一次「前方还有几张」（来自飞书闸门）
        self._queue_notes: dict[str, dict] = {}
        # 成品文件名尾号（202611052219）与同款同色的补充标签
        self.stamp = job_stamp(str(getattr(job, "id", "") or ""))
        self.limiter = RateLimiter(int(params.get("rpm") or 0))
        # 本机并发不能超过接口实测能吃的并发（terra 实测 20）；闸门额度不在这里收，
        # 因为「超过额度就排队」本来就该在飞书表里排，界面才看得见前方还有几张
        # unlimited（实测能扛的接口，如 a6api）→ 不按接口上限收并发，也不排队
        if self.unlimited:
            self.max_concurrency = self.concurrency
        else:
            self.max_concurrency = max(1, int(provider.setting("max_concurrency", 20) or 20))
        if self.concurrency > self.max_concurrency:
            job.add_log(
                f"并发从 {self.concurrency} 收到接口上限 {self.max_concurrency}（其余排队）",
                level="warn",
            )
            self.concurrency = self.max_concurrency
            self.job.params["concurrency"] = self.concurrency
        self.sem = asyncio.Semaphore(self.concurrency)
        # 跨机器的全局并发阀门（Cloudflare Worker）；None = 只用本机限制
        self.gate = gate
        self._gate_warned = False
        self.provider_sem = provider_sem
        self.model = str(params.get("model") or "") or None
        self._aspect_cache: dict[Path, float] = {}
        self._tasks_running: set[asyncio.Task] = set()
        self._last_save = 0.0
        # 打包失败但图片都在时不再让整批变 failed（成品还能单张下载/批量导出）
        self.package_error = ""

    # ------------------------------------------------------------------ paths #
    @property
    def root(self) -> Path:
        return self.job.root  # type: ignore[return-value]

    @property
    def inputs_dir(self) -> Path:
        return self.root / "inputs"

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def staging_dir(self) -> Path:
        return self.root / "staging"

    @property
    def outputs_dir(self) -> Path:
        return self.root / "outputs"

    # ------------------------------------------------------------------ state #
    def _save(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_save < 0.35:
            return
        self._last_save = now
        try:
            self.job.params["queue"] = self._queue_payload()
            # 每张图自己的排队位置（界面卡片显示「排队中，还有 N 个」）
            self.job.params["queue_tasks"] = {
                key: {"ahead": int(note.get("ahead") or 0), "at": round(float(note.get("at") or 0), 1)}
                for key, note in self._queue_notes.items()
                if time.time() - float(note.get("at") or 0) <= 30
            }
            self.job.save()
        except OSError:
            pass

    def _queue_payload(self) -> dict:
        """排队情况：**前方还有几个任务**（界面就显示这个数）。

        两个来源取较大值：
          * 飞书闸门轮询到的 `ahead`（正在跑 + 排我前面的票，含别的电脑）；
          * 本机还没轮到出图的任务数（本地并发位不够时，这些还没进飞书队列）。
        """
        stats = self.job.counts()
        now = time.time()
        fresh = [
            note for note in self._queue_notes.values() if now - float(note.get("at") or 0) <= 25
        ]
        gate_ahead = min((int(note.get("ahead") or 0) for note in fresh), default=0)
        gate_queued = max((int(note.get("queued") or 0) for note in fresh), default=0)
        waiting = int(stats.get("pending") or 0) + int(stats.get("queued") or 0)
        busy = int(stats.get("running") or 0)
        if not waiting and not busy:
            return {"ahead": 0, "pending": 0, "queued": 0, "running": 0, "active": False}
        return {
            "ahead": max(waiting, gate_ahead),
            "gate_ahead": gate_ahead,
            "queued": gate_queued,
            "pending": int(stats.get("pending") or 0),
            "waiting": waiting,
            "running": busy,
            "limit": int(getattr(self.gate, "limit", 0) or 0),
            "active": True,
            "updated": round(now, 1),
        }

    def _set_status(self, task: Task, status: str, **fields) -> None:
        task.status = status
        for key, value in fields.items():
            setattr(task, key, value)
        if status in {"done", "failed", "skipped", "canceled"}:
            task.finished_at = time.time()
        self._save(force=status in {"done", "failed", "skipped", "canceled"})

    def _mark_unfinished_canceled(self) -> None:
        """A cancelled asyncio task never reaches its own status update."""
        for task in self.job.tasks:
            if task.status in {"pending", "queued", "running"}:
                task.status = "canceled"
                task.error = task.error or "已取消"
                task.finished_at = time.time()
        self._save(force=True)

    # ------------------------------------------------------------------- plan #
    def _sources(self) -> list[tuple[str, str, Path]]:
        """Enumerate uploaded originals as (item, view, path)."""
        entries: list[tuple[str, str, Path]] = []
        if not self.inputs_dir.is_dir():
            return entries
        for item_dir in sorted(p for p in self.inputs_dir.iterdir() if p.is_dir()):
            files = sorted(
                p for p in item_dir.iterdir()
                if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
            )
            for path in files:
                view = normalise_item(path.stem)[1] or view_of(path.stem)
                entries.append((item_dir.name, view, path))
        return entries

    def subject_for(self, path: Path) -> str:
        """这张原图有没有真人：判不准按 person（中性提示词，不多禁）。"""
        return subject_mod.detect(path, self.subject_override)

    def plan(self) -> None:
        sources = self._sources()
        if not sources:
            raise ValueError("没有可处理的输入图片")
        # 每张原图先判主体：纯衣服平铺图绝不能拿到「保持同一个人」那句
        subjects = {str(path): self.subject_for(path) for _item, _view, path in sources}
        counts = {"garment": 0, "person": 0}
        for kind in subjects.values():
            counts[kind] = counts.get(kind, 0) + 1
        self.job.params["subject"] = (
            "garment" if counts.get("garment") and not counts.get("person") else "person"
        )
        self.job.params["subjects"] = dict(counts)
        if counts.get("garment"):
            self.job.add_log(
                f"主体判定：{counts['garment']} 张是纯衣服（平铺/挂拍），已禁止添加人物；"
                f"{counts.get('person', 0)} 张按真人/不确定处理"
            )
        tasks: list[Task] = []
        for garment, colors in self.selections:
            for item, view, path in sources:
                base = f"{item}-{view}" if view else item
                subject = subjects.get(str(path), "person")
                for color in colors:
                    # 多款式时 id 里带上款式，避免「同图同色不同款」撞号
                    task_id = (
                        f"{base}__{garment.slug}__{color.label}"
                        if self.multi_style
                        else f"{base}__{color.label}"
                    )
                    if self.mode in {"two_phase", "recolor_only"}:
                        prompt = prompts.build_recolor_only(
                            color,
                            pattern_mode=self.pattern_mode,
                            garment=garment,
                            subject=subject,
                        )
                    else:
                        prompt = prompts.build_single_pass(
                            self.skill,
                            garment,
                            color,
                            self.extra_requirements,
                            pattern_mode=self.pattern_mode,
                            pattern_brief=self.pattern_brief,
                            subject=subject,
                        )
                    tasks.append(
                        Task(
                            id=task_id,
                            item=item,
                            view=view,
                            color=color.label,
                            color_hex=color.hex,
                            color_name=color.name_zh,
                            garment=garment.slug,
                            garment_name=garment.display_name,
                            source=str(path.relative_to(self.root)).replace("\\", "/"),
                            prompt=prompt,
                            subject=subject,
                        )
                    )
        self.job.tasks = tasks
        self.job.total = len(tasks)
        self._save(force=True)

    def _retarget_failed(self, task_ids: set[str] | None = None) -> list[Task]:
        pending = [
            t
            for t in self.job.tasks
            if t.status in {"failed", "canceled"} and (not task_ids or t.id in task_ids)
        ]
        for task in pending:
            task.status = "pending"
            task.error = None
            task.error_kind = ""
            task.out = None
        return pending

    # ------------------------------------------------------------------- run  #
    async def run(
        self, *, retry_failed_only: bool = False, task_ids: set[str] | None = None
    ) -> None:
        self.job.root = self.root
        try:
            await self.provider.open()
            if not self.job.tasks:
                self.plan()
            elif retry_failed_only:
                retried = self._retarget_failed(task_ids)
                if not retried:
                    self.job.status = "done"
                    self.job.stage = "没有需要重试的任务"
                    self._save(force=True)
                    return

            self.job.status = "running"
            self.job.stage = "生成中"
            self.job.add_log(
                f"开始处理：{len(self.job.tasks)} 个任务，并发 {self.concurrency}，"
                f"模式 {self.mode}，接口 {self.provider.name}"
            )
            self._save(force=True)

            if self.mode == "rework":
                await self._run_rework()
            elif self.mode == "two_phase":
                await self._run_two_phase()
            else:
                await self._run_single_pass()

            if self.job.cancel_requested:
                self._mark_unfinished_canceled()
                self.job.status = "canceled"
                self.job.stage = "已取消"
                self.job.add_log("任务已取消", level="warn")
                self._save(force=True)
                return

            self.job.status = "packaging"
            self.job.stage = "校验并打包"
            self._save(force=True)
            await self._package()

            stats = self.job.counts()
            if self.package_error and stats["failed"] == 0:
                # 图片都在、只是 ZIP 没打成：别整批判失败，成品照样能下载/导出
                self.job.status = "partial"
                self.job.stage = f"图片已出，打包失败：{self.package_error[:120]}"
                self.job.add_log(
                    "打包没成功，但成品都可用：单张「下载」或「批量导出」仍能全部取走",
                    level="warn",
                )
            else:
                self.job.status = "done" if stats["failed"] == 0 else "partial"
                self.job.stage = (
                    f"完成：{stats['done']} 张成功"
                    + (f"，{stats['failed']} 张失败" if stats["failed"] else "")
                )
            self.job.add_log(self.job.stage, level="warn" if stats["failed"] else "info")
        except asyncio.CancelledError:
            self._mark_unfinished_canceled()
            self.job.status = "canceled"
            self.job.stage = "已取消"
            self.job.add_log("任务被中断", level="warn")
            self._save(force=True)
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            self.job.status = "failed"
            self.job.error = str(exc)
            self.job.stage = f"失败：{exc}"
            self.job.add_log(f"失败：{exc}", level="error")
            self._save(force=True)
        finally:
            self._save(force=True)
            # 注意：**不要**在这里 close()。provider 是按接口共享的（同一台机器上
            # 可能有好几个任务同时用它），谁先跑完谁关，会把别人正在发的请求打断
            # （实测报 "provider 未初始化"）。统一在服务退出时关闭。

    async def _run_single_pass(self) -> None:
        await self._gather(self.job.tasks, self._execute_color_task)

    async def _run_rework(self) -> None:
        """Every task already carries its reference image and its revised prompt."""
        await self._gather(self.job.tasks, self._execute_rework_task)

    async def _execute_rework_task(self, task: Task) -> None:
        source = self.root / task.source
        if not source.is_file():
            self._set_status(task, "failed", error="参考图缺失")
            return
        request = EditRequest(
            images=[source],
            prompt=task.prompt,
            negative=self.negative_for(self.garment_for_task(task), task.subject),
            size=self._resolve_size(source),
            quality=str(self.job.params.get("quality") or "high"),
            seed=None,
            model=self.model,
            input_fidelity=self.input_fidelity,
        )
        await self._attempt(task, request)

    async def _run_two_phase(self) -> None:
        if self.multi_style:
            raise ValueError("两段式（two_phase）暂不支持多款式，请用一次到位 single_pass")
        unique: dict[tuple[str, str, str], Path] = {}
        for task in self.job.tasks:
            unique[(task.item, task.view, task.source)] = self.root / task.source
        self.job.stage = f"第一阶段：改款（{len(unique)} 张原图）"
        self._save(force=True)

        styled: dict[str, Path] = {}
        lock = asyncio.Lock()

        async def style_one(entry: tuple[tuple[str, str, str], Path]) -> None:
            (item, view, _), path = entry
            name = f"{item}-{view}" if view else item
            out = self.work_dir / f"{name}.png"
            if out.is_file() and out.stat().st_size > 1024:
                async with lock:
                    styled[f"{item}|{view}"] = out
                return
            request = EditRequest(
                images=[path],
                prompt=prompts.build_style_only(self.garment, subject=self.subject_for(path)),
                negative=self.negative_for(self.garment, self.subject_for(path)),
                size=self._resolve_size(path),
                quality=str(self.job.params.get("quality") or "high"),
                seed=self.job.params.get("seed"),
                model=self.model,
                input_fidelity=self.input_fidelity,
            )
            try:
                data = await self._guarded_call(request)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
                async with lock:
                    styled[f"{item}|{view}"] = out
                self.job.add_log(f"改款完成 {name}")
            except Exception as exc:  # noqa: BLE001
                self.job.add_log(f"改款失败 {name}: {exc}", level="error")
            self._save()

        await self._gather(list(unique.items()), style_one)

        missing = {
            (task.item, task.view) for task in self.job.tasks
            if f"{task.item}|{task.view}" not in styled
        }
        for task in self.job.tasks:
            if (task.item, task.view) in missing:
                self._set_status(task, "failed", error="改款阶段失败，未进入换色")

        targets = [t for t in self.job.tasks if t.status == "pending"]
        self.job.stage = f"第二阶段：换色（{len(targets)} 张）"
        self._save(force=True)

        async def recolor_one(task: Task) -> None:
            base = styled.get(f"{task.item}|{task.view}")
            if base is None:
                self._set_status(task, "failed", error="缺少改款中间图")
                return
            await self._execute_color_task(task, source_override=base)

        await self._gather(targets, recolor_one)

    async def _gather(self, items, handler) -> None:
        async def guarded(item, index: int):
            if self.job.cancel_requested:
                return
            await asyncio.sleep(0.001 * (index % 50))
            await handler(item)

        tasks = [
            asyncio.create_task(guarded(item, index)) for index, item in enumerate(items)
        ]
        self._tasks_running = set(tasks)
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._tasks_running = set()

    # -------------------------------------------------------------- one call  #
    def _resolve_size(self, source: Path) -> str:
        """`size=auto` → the preset closest to the source aspect ratio.

        The skill requires keeping the original aspect ratio; gpt-image style APIs
        only accept a few fixed sizes, so pick the nearest one instead of letting the
        API fall back to a square.
        """
        requested = str(self.job.params.get("size") or "auto")
        if requested != "auto":
            return requested
        presets = getattr(self.provider, "size_presets", None)
        if not presets:
            return "auto"
        aspect = self._aspect_cache.get(source)
        if aspect is None:
            try:
                with Image.open(source) as image:
                    aspect = image.width / max(1, image.height)
            except Exception:  # noqa: BLE001
                aspect = 1.0
            self._aspect_cache[source] = aspect

        best = "auto"
        best_delta = None
        for preset in presets:
            try:
                width, height = (int(part) for part in str(preset).lower().split("x", 1))
            except ValueError:
                continue
            delta = abs(width / height - aspect)
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best = str(preset)
        return best

    def garment_for_task(self, task: Task) -> GarmentType:
        """任务属于哪个款式。

        多款式任务里 `self.garment` 只是第一个款式，必须按 task.garment 找，
        否则第二个款式的颜色会在第一个款式里查 → 查不到就报「颜色参考缺失」，
        查得到（同名色）更糟：会悄悄拿错款式的参考图。
        """
        slug = task.garment or self.garment.slug
        if slug == self.garment.slug:
            return self.garment
        for garment, _colors in self.selections:
            if garment.slug == slug:
                return garment
        try:
            return self.skill.type_by_alias(slug)
        except KeyError:
            return self.garment

    def negative_for(self, garment: GarmentType | None, subject: str = "") -> str:
        """按款式+主体取否定词（袖口工艺、是否禁人都可能不同），结果缓存。"""
        kind = subject_mod.normalise(subject) if subject else ""
        key = f"{(garment.slug if garment is not None else '')}|{kind}"
        cached = self._negatives.get(key)
        if cached is None:
            cached = prompts.full_negative(self.skill, self.pattern_mode, garment, kind)
            self._negatives[key] = cached
        return cached

    async def _execute_color_task(self, task: Task, *, source_override: Path | None = None) -> None:
        garment = self.garment_for_task(task)
        color = garment.color(task.color)
        if color is None:
            self._set_status(
                task,
                "failed",
                error=f"颜色参考缺失：{garment.display_name} 里没有「{task.color_name or task.color}」这个颜色",
            )
            return
        source = source_override or (self.root / task.source)
        images = [source]
        if self.send_reference:
            if color.path.is_file():
                images.append(color.path)
            else:
                # 参考图文件真的不在（数据问题）→ 记一条警告继续跑，别整张失败
                self.job.add_log(
                    f"颜色参考图缺失，已跳过参考：{garment.display_name} / {color.label}",
                    level="warn",
                )
                self._save()
        request = EditRequest(
            images=images,
            prompt=task.prompt,
            negative=self.negative_for(garment, task.subject),
            size=self._resolve_size(source),
            quality=str(self.job.params.get("quality") or "high"),
            seed=self.job.params.get("seed"),
            model=self.model,
            input_fidelity=self.input_fidelity,
        )
        await self._attempt(task, request)

    def _person_alert(self, task: Task, data: bytes) -> dict:
        """原图是空衣服，成品里却冒出人 → 记警告（可选自动重画，见 retry_on_person）。"""
        if subject_mod.normalise(task.subject) != subject_mod.GARMENT:
            return {"alert": False, "skin": 0.0}
        source = self.root / task.source
        return subject_mod.person_alert(source, data)

    async def _attempt(self, task: Task, request: EditRequest) -> None:
        if task.status == "running":
            return
        # 先记「排队中」：真正发请求时才翻成「生成中」，否则界面上排队和生成分不清
        self._set_status(task, "queued")
        attempt = 0
        last_error = "未知错误"
        while attempt < self.max_retries:
            if self.job.cancel_requested:
                self._set_status(task, "canceled", attempts=attempt)
                return
            attempt += 1
            task.attempts = attempt
            if attempt > 1:
                self._set_status(task, "queued")
            try:
                data = await self._guarded_call(request, task)
                alert = self._person_alert(task, data)
                if alert.get("alert"):
                    self.job.add_log(
                        f"{task.id} 疑似生成了真人（原图是空衣服，新增皮肤区域 "
                        f"{alert.get('skin', 0):.1%}）",
                        level="warn",
                    )
                    task.person_alert = True
                    if self.retry_on_person and attempt < self.max_retries:
                        # 只加一次强化约束重画，别把「疑似」当成失败无限重试
                        self.retry_on_person = False
                        request = replace(
                            request, prompt=f"{request.prompt}{prompts.NO_PERSON_REINFORCE}"
                        )
                        raise ProviderError(
                            "成品里出现了人物，已加禁人约束重画一次", retryable=True
                        )
                rel = self._store(task, data)
                self._set_status(task, "done", out=rel, error=None, error_kind="", bytes=len(data))
                return
            except ProviderError as exc:
                last_error = exc.message
                retryable = exc.retryable
                if is_rate_limited(exc.message):
                    task.error_kind = "rate_limit"
                if "超时" in exc.message and not self.retry_on_timeout:
                    retryable = False
                    last_error = (
                        f"{exc.message}；上游可能已生成，为避免重复计费未自动重试"
                        f"（可在图片下方点「重试」）"
                    )
                wait = exc.retry_after if exc.retry_after else min(2 ** (attempt - 1), 30)
                self.job.add_log(
                    f"{task.id} 第 {attempt}/{self.max_retries} 次失败: {exc.message}",
                    level="warn",
                )
            except GateBusy as exc:
                # 闸门排队超时：不算「接口失败」，但同样要重试，并标记成可一键重试的限流失败
                last_error = str(exc)
                retryable = True
                task.error_kind = "rate_limit"
                wait = min(5 * attempt, 60)
                self.job.add_log(f"{task.id} 排队未进：{exc}（稍后重试）", level="warn")
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                retryable = True
                wait = min(2 ** (attempt - 1), 30)
                self.job.add_log(f"{task.id} 第 {attempt} 次异常: {last_error}", level="warn")

            if not retryable or attempt >= self.max_retries:
                break
            await asyncio.sleep(wait + random.uniform(0, 0.6))
            self._save()

        kind = "rate_limit" if (task.error_kind == "rate_limit" or is_rate_limited(last_error)) else "error"
        self._set_status(
            task, "failed",
            error=f"{last_error}（已尝试 {attempt} 次）",
            error_kind=kind,
        )
        self.job.add_log(f"{task.id} 放弃：{last_error}", level="error")

    async def _call(self, request: EditRequest) -> bytes:
        data = await self.provider.edit(request, self.timeout)
        self._validate_image(data)
        return data

    def _gate_wait_note(self, info: dict) -> None:
        """闸门排队回调节点：把「前方还有几张」记下来，界面据此显示排队进度。"""
        info = dict(info or {})
        now = time.time()
        info["at"] = now
        task_key = str(info.get("task") or "") or f"r{int(now * 10) % 1000}"
        self._queue_notes[task_key] = info
        ahead = int(info.get("ahead") or 0)
        in_use = int(info.get("in_use") or 0)
        limit = int(info.get("limit") or 0)
        waited = float(info.get("waited") or 0)
        self.job.add_log(
            f"排队中：前方还有 {ahead} 个任务（闸门 {in_use}/{limit}，已等 {waited:.0f}s）",
            level="warn",
        )
        # 立刻落盘一份排队数据：不然要等别的任务完成才刷新，界面上会「看不到排队」
        self._save(force=True)

    async def _guarded_call(self, request: EditRequest, task: Task | None = None) -> bytes:
        """Every API call passes the rpm gate, the shared provider cap, and the job cap.

        配了全局阀门（Cloudflare）时先要一张牌再发请求，回来立刻还牌 —— 同一把 Key
        在多台电脑上跑，总并发也不会超过接口能吃的额度。
        """
        await self.limiter.acquire()
        lease = None
        if self.gate is not None:
            task_id = task.id if task else ""
            lease = await self.gate.acquire(
                job=self.job.id,
                task=task_id,
                # 每个任务各自记一条排队位置，界面取「最快那张」的数
                on_wait=lambda info: self._gate_wait_note({**info, "task": task_id}),
            )
            if lease is None and not self._gate_warned:
                self._gate_warned = True
                reason = str(getattr(self.gate, "last_error", "") or "阀门暂时不可用")[:90]
                self.job.add_log(
                    f"全局并发阀门不可用（{reason}），本次只用本机并发 {self.concurrency}",
                    level="warn",
                )
        try:
            if self.provider_sem is not None:
                async with self.provider_sem:
                    async with self.sem:
                        self._enter_running(task)
                        return await self._call(request)
            async with self.sem:
                self._enter_running(task)
                return await self._call(request)
        finally:
            if lease is not None:
                await self.gate.release(lease)

    def _enter_running(self, task: Task | None) -> None:
        """真正拿到并发位、要发请求了：排队 → 生成中（计时从这里开始，不含排队时间）。"""
        if task is None or task.status == "running":
            return
        self._set_status(task, "running", started_at=task.started_at or time.time())

    @staticmethod
    def _validate_image(data: bytes) -> None:
        if len(data) < 512:
            raise ProviderError(
                f"接口返回内容过小（{len(data)} 字节），可能是错误信息", retryable=True
            )
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"接口返回的不是有效图片: {exc}", retryable=True) from exc

    def _store(self, task: Task, data: bytes) -> str:
        suffix = sniff_extension(data)
        if self.output_format in {"png", "jpg", "jpeg", "webp"}:
            wanted = ".jpg" if self.output_format == "jpeg" else f".{self.output_format}"
            if suffix != wanted:
                data = self._convert(data, wanted)
                suffix = wanted
        # 打包按**款式**分层：同款式的图放同一个文件夹（文件夹名 = 款式英文代号 24CXCR）
        garment = self.garment_for_task(task)
        item_dir = self.staging_dir / garment.file_key
        item_dir.mkdir(parents=True, exist_ok=True)
        # 文件名 = 款式代号_色号编码[_补充]_批次号，例如 24CXCR_WH[0]_202611052219.png
        color = garment.color(task.color)
        stem = task.file_stem(
            style_key=garment.file_key,
            color_key=(color.file_code if color is not None else ""),
            stamp=self.stamp,
            tag=self._dup_tag(task),
        )
        target = item_dir / f"{stem}{suffix}"
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)
        return str(target.relative_to(self.root)).replace("\\", "/")

    def _dup_tag(self, task: Task) -> str:
        """同款同色出现多张（多原图 / 多视角）时补一段，否则返回空。

        文件名里没有 item 了（客户只要款式+色号+批次），所以撞名只能在中间补：
        `24CXCR_WH[0]_item-02_202611052219.png`。
        """
        style = task.garment or self.garment.slug
        same = [
            other
            for other in self.job.tasks
            if (other.garment or self.garment.slug) == style and other.color == task.color
        ]
        if len(same) <= 1:
            return ""
        return f"{task.item}-{task.view}" if task.view else task.item

    @staticmethod
    def _convert(data: bytes, suffix: str) -> bytes:
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            buffer = io.BytesIO()
            if suffix == ".jpg":
                image.save(buffer, format="JPEG", quality=95, optimize=True, progressive=True)
            elif suffix == ".webp":
                image.save(buffer, format="WEBP", quality=95, method=6)
            else:
                image.save(buffer, format="PNG", optimize=True, compress_level=9)
            return buffer.getvalue()

    # -------------------------------------------------------------- packaging #
    async def _package(self) -> None:
        if not self.staging_dir.is_dir():
            raise ValueError("没有生成任何图片，无法打包")
        for name in clean_staging(self.staging_dir):
            self.job.add_log(f"清理 staging 里的缓存目录：{name}", level="warn")

        produced = [
            p for p in self.staging_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        ]
        if not produced:
            self.job.add_log("没有任何成功的成品，跳过打包", level="warn")
            return

        stats = self.job.counts()
        # staging 现在按**款式**分文件夹：每个文件夹里应该有「颜色数 × 原图数(含视角)」张
        sources = len({(task.item, task.view) for task in self.job.tasks})
        expected = (
            len(self.colors) * max(1, sources)
            if self.mode == "single_pass"
            and not self.multi_style
            and stats["failed"] == 0
            and stats["canceled"] == 0
            else None
        )
        result = await package_outputs(
            script=self.skill.root / "scripts" / "package_outputs.py",
            staging_dir=self.staging_dir,
            output_dir=self.outputs_dir,
            batch_name=self.job.batch_name or self.garment.batch_name,
            max_bytes=self.skill.max_bytes,
            expected_per_item=expected,
        )
        batch = self.job.batch_name or self.garment.batch_name
        self.job.batch_name = batch
        if result.ok:
            self.job.dir_rel = (
                str((self.outputs_dir / batch).relative_to(self.root))
                .replace("\\", "/")
            )
            if result.zip_path:
                self.job.zip_rel = (
                    str(result.zip_path.relative_to(self.root)).replace("\\", "/")
                )
            self.job.add_log(
                f"打包完成：{len(result.info.get('lines', []))} 项校验输出"
                + ("" if expected else "（存在失败任务，按现有图片打包）")
            )
        else:
            self.job.add_log(f"打包失败：{result.error}", level="error")
            # 不再整批抛错：成品已落盘，让用户至少能单张下载/批量导出
            self.package_error = str(result.error or "打包失败")
            self._save(force=True)
