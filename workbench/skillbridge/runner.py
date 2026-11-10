"""Concurrent execution engine: plan tasks, call the image API in parallel, package."""

from __future__ import annotations

import asyncio
import collections
import io
import random
import shutil
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath, PureWindowsPath

from PIL import Image

from . import prompts
from . import swatch as swatch_mod
from . import subject as subject_mod
from .job import Job, Task, job_stamp, normalise_item, view_of
from .gate import GateBusy
from .packaging import package_outputs
from .providers.base import EditRequest, Provider, ProviderError, sniff_extension
from .skill import ColorRef, GarmentType, Skill
from .sources import original_uploads

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
        if self.pattern_mode == "preserve":
            params["artwork_policy"] = "ink-and-cloth-v1"
        self.pattern_brief = str(params.get("pattern_brief") or "").strip()
        from .output_policy import edits_people, person_selection
        requirements = [str(params.get(key) or "").strip() for key in ("subject_edit", "extra_requirements", "instruction")]
        self.output_requirement = "；".join(dict.fromkeys(text for text in requirements if text))
        if edits_people(self.output_requirement):
            params["subject_edit"] = person_selection(self.output_requirement)
        # Persist the operator choice before plan() replaces subject with a batch summary.
        # Legacy summaries cannot override saved per-task classifications on reload.
        self.subject_override = subject_mod.choice(job)
        params.setdefault("subject_choice", self.subject_override)
        # 出图后发现「又画了个人」要不要自动重画一次（默认只警告，不偷偷多花钱）
        self.retry_on_person = bool(params.get("retry_on_person", False))
        # 版型一致性（内部固定行为，界面上没有开关）：先出基准色，其余颜色基于它改色；
        # params 里显式写 shape_lock=off 才退回「每色独立生成」
        self.shape_lock = str(params.get("shape_lock") or "master").strip().lower() != "off"
        if str(params.get("mode") or "") == "two_phase":
            self.shape_lock = False      # 两段式本身就是「先改款再换色」，不再套第二层
        # 基准图审核闸门：先只出基准图，运营在对话框里确认/修改，确认后才按基准图改其余颜色。
        # phase=derived 表示「已经确认过，这一趟只跑派生色」，不再拦截。
        self.phase = str(params.get("phase") or "").strip().lower()
        self.review_gate = bool(params.get("review_gate", False)) and self.shape_lock and self.phase != "derived"
        self.negative = prompts.full_negative(self.skill, self.pattern_mode, subject=self.subject_override)
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
        """Explicit output choice wins; uncertain uploads retain their actual subject."""
        return subject_mod.detect(path, self.subject_override)

    def _apply_task_subject(self, task: Task) -> None:
        """Resolve original task/lineage evidence, never a damaged generated subject."""
        kind = subject_mod.task_kind(self.job, task)
        task.subject = kind
        self.job.params.setdefault("task_subjects", {})[task.id] = kind
        if self.mode == "rework":
            kinds = [subject_mod.normalise(t.subject) for t in self.job.tasks]
            self.job.params["subject"] = kinds[0] if len(set(kinds)) == 1 else subject_mod.AUTO
            self.job.params["subjects"] = {k: kinds.count(k) for k in subject_mod.KINDS}
        task.prompt = prompts.enforce_composition_rule(prompts.enforce_subject_rule(task.prompt, kind))

    def plan(self) -> None:
        sources = self._sources()
        if not sources:
            raise ValueError("没有可处理的输入图片")
        # Missing skin cannot distinguish empty clothes from a covered/cropped wearer.
        subjects = {str(path): self.subject_for(path) for _item, _view, path in sources}
        counts = {kind: 0 for kind in subject_mod.KINDS}
        for kind in subjects.values():
            counts[kind] = counts.get(kind, 0) + 1
        kinds = set(subjects.values())
        self.job.params["subject"] = next(iter(kinds)) if len(kinds) == 1 else subject_mod.AUTO
        self.job.params["subjects"] = dict(counts)
        if counts.get("garment"):
            self.job.add_log(
                f"输出主体：{counts['garment']} 张按只有衣服处理（移除原有人物/模特并禁止新增）；"
                f"{counts.get('person', 0)} 张按真人/不确定处理"
            )
        if counts.get("auto"):
            self.job.add_log(
                f"主体按原图保留：{counts['auto']} 张无明确露肤证据，不据此移除模特或改变背景；"
                "平铺保持平铺，穿着保持穿着"
            )
        tasks: list[Task] = []
        for garment, colors in self.selections:
            for item, view, path in sources:
                base = f"{item}-{view}" if view else item
                subject = subjects.get(str(path), subject_mod.AUTO)
                # 版型锁定：同款同图的第一个颜色当基准，其余颜色以它为参考图只改色，
                # 否则每色独立生成 → 袖长/衣宽/下摆每次都漂，客户一眼看出「不是同一件」
                lock = self.shape_lock and len(colors) > 1
                master_id = ""
                for index, color in enumerate(colors):
                    # 多款式时 id 里带上款式，避免「同图同色不同款」撞号
                    task_id = (
                        f"{base}__{garment.slug}__{color.label}"
                        if self.multi_style
                        else f"{base}__{color.label}"
                    )
                    is_master = lock and index == 0
                    if lock and index == 0:
                        master_id = task_id
                    derive = lock and index > 0
                    # 效果图是否真的会随请求发出去（接口要多图、该色有图）
                    want_effect = self.use_effect(color)
                    if derive:
                        # 只改色：参考图 = 基准成品（版型/印花/场景）+ 该色效果图（颜色）
                        prompt = prompts.build_master_recolor(
                            color,
                            garment=garment,
                            subject=subject,
                            effect=want_effect,
                            # 派生色会发面料色卡（不带版型），提示词也要这么说
                            swatch=want_effect,
                        )
                    elif self.mode in {"two_phase", "recolor_only"}:
                        prompt = prompts.build_recolor_only(
                            color,
                            pattern_mode=self.pattern_mode,
                            garment=garment,
                            subject=subject,
                            effect=want_effect,
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
                            effect=want_effect,
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
                            master=is_master,
                            master_task="" if (is_master or not derive) else master_id,
                        )
                    )
        self.job.tasks = tasks
        self.job.params["task_subjects"] = {task.id: task.subject for task in tasks}
        self.job.params["subject_policy"] = subject_mod.AUTO_POLICY
        self.job.total = len(tasks)
        if any(task.master_task for task in tasks):
            self.job.add_log(
                f"版型锁定：先出 {sum(1 for t in tasks if t.master)} 张基准色，"
                f"其余 {sum(1 for t in tasks if t.master_task)} 张按基准成品改色（同款各色版型一致）"
            )
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
            self.job.stage = "生成中" if self.phase != "derived" else "按确认的基准图改色"
            self.job.add_log(
                f"开始处理：{len(self.job.tasks)} 个任务，并发 {self.concurrency}，"
                f"模式 {self.mode}，接口 {self.provider.name}"
                + ("（本轮=确认后派生改色）" if self.phase == "derived" else "")
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

            if self.job.status == "review":
                # 基准图审核：停在这里等运营确认，不打包、不派生其它颜色
                self.job.params["awaiting_review"] = True
                self._save(force=True)
                return

            self.job.params["generation_finished_at"] = time.time()
            self.job.status = "packaging"
            self.job.stage = f"图片处理完成 {self.job.counts()['finished']}/{len(self.job.tasks)}，校验并打包"
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
            stats = self.job.counts()
            packing_only = stats["done"] > 0 and not any(stats[k] for k in ("pending", "queued", "running", "canceled"))
            self.job.status = "partial" if packing_only else "canceled"
            self.job.stage = "图片生成完成，打包已取消；原始成品可下载" if packing_only else "已取消"
            self.job.add_log(self.job.stage, level="warn")
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
        def open_tasks(items: list[Task]) -> list[Task]:
            """只跑还没出图的：重复点开始 / 确认后继续，都不能把已完成的图再买一次。"""
            return [t for t in items if t.status not in {"done", "skipped"} and not t.out]

        masters = open_tasks([t for t in self.job.tasks if t.master])
        derived = open_tasks([t for t in self.job.tasks if t.master_task])
        plain = open_tasks([t for t in self.job.tasks if not t.master and not t.master_task])
        if not any(t.master_task for t in self.job.tasks):
            await self._gather(
                open_tasks(self.job.tasks), self._execute_color_task
            )
            return
        # ① 先把基准色出完（同款各色共用这一版版型）
        if masters:
            await self._gather(masters, self._execute_color_task)
        if self.review_gate:
            # 基准图审核闸门：这一趟只出基准，停下来等运营确认（不打包、不派生）
            stuck = [t for t in self.job.tasks if t.master and not t.out]
            if stuck:
                self.job.status = "review"
                self.job.stage = f"基准图没出来（{len(stuck)} 张失败），请重试或换模型"
                self.job.add_log("基准图未能生成，无法进入审核", level="warn")
            else:
                self.job.status = "review"
                self.job.stage = "基准图已出，等你确认（确认后按这张图改其余颜色）"
                self.job.add_log(
                    "先出基准图：确认没问题再按它改色；不满意就直接说要改哪里，"
                    "改好再确认（确认前不会花钱出其它颜色）"
                )
            self._save(force=True)
            return
        # ② 其余颜色以基准成品为参考图，只改面料颜色
        await self._gather(derived + plain, self._execute_shape_task)

    def _task_by_id(self, task_id: str) -> Task | None:
        for task in self.job.tasks:
            if task.id == task_id:
                return task
        return None

    async def _execute_shape_task(self, task: Task) -> None:
        """版型锁定下的「非基准色」任务：参考图换成基准成品，只改颜色。

        基准色失败（没出图）时退回原图正常生成，只是这一天该色版型可能不同 —— 记一条警告。
        """
        master = self._task_by_id(task.master_task) if task.master_task else None
        if not task.master_task:
            # 不参与版型锁定的任务（单色/未锁定）走普通路径，别写「基准不可用」
            await self._execute_color_task(task)
            return
        master_path = (self.root / str(master.out)) if (master and master.out) else None
        if master_path is None or not master_path.is_file():
            self.job.add_log(
                f"{task.id} 基准色不可用（{task.master_task or '未指定'}），已退回用原图生成，"
                f"该色版型可能与其它颜色不同",
                level="warn",
            )
            task.master_task = ""
            await self._execute_color_task(task)
            return
        task.reference = str(master.out or "")
        await self._execute_color_task(task, source_override=master_path)

    async def _run_rework(self) -> None:
        """Every task already carries its reference image and its revised prompt."""
        await self._gather(self.job.tasks, self._execute_rework_task)

    def _job_image_path(self, relative: object, *, original: bool = False) -> Path | None:
        """Resolve a saved job-relative image, never an arbitrary/escaping filesystem path."""
        if not isinstance(relative, str) or not relative.strip() or "\x00" in relative or ":" in relative:
            return None
        text = relative.replace("\\", "/")
        posix, windows = PurePosixPath(text), PureWindowsPath(text)
        if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
            return None
        if original and (not posix.parts or posix.parts[0].lower() != "originals"):
            return None
        try:
            root = self.root.resolve()
            path = (root / text).resolve()
            path.relative_to(root)
            if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
                return None
            return path
        except (OSError, ValueError, RuntimeError):
            return None

    def _rework_originals(self, task: Task, source: Path) -> list[Path]:
        """Use this task's original copies, or recorded ancestry for a legacy rework."""
        mapping = self.job.params.get("original_sources")
        entries = mapping.get(task.id, []) if isinstance(mapping, dict) else []
        if not isinstance(entries, list):
            return []
        originals: list[Path] = []
        seen = {source.resolve()}
        if not entries and self.job.params.get("source_job") and mapping is None:
            uploads = original_uploads(
                self.job, task, lambda job_id: subject_mod.load_lineage_job(self.job, job_id)
            )
            for upload in uploads:
                path = upload.path.resolve()
                if path not in seen and path.suffix.lower() in IMAGE_SUFFIXES:
                    originals.append(path)
                    seen.add(path)
        for relative in entries:
            path = self._job_image_path(relative, original=True)
            if path is None:
                self.job.add_log(f"{task.id} 原始上传参考缺失或路径不安全，已跳过", level="warn")
            elif path not in seen:
                originals.append(path)
                seen.add(path)
        return originals

    async def _execute_rework_task(self, task: Task) -> None:
        self._apply_task_subject(task)
        source = self._job_image_path(task.source)
        if source is None:
            self._set_status(task, "failed", error="参考图缺失或路径不安全")
            return
        originals = self._rework_originals(task, source)
        capacity = max(1, int(getattr(self.provider, "max_images", 1) or 1))
        images = [source, *originals[:capacity - 1]]
        if capacity == 1:
            self.job.add_log(
                f"{task.id} 接口仅支持 1 张输入，原始上传未附加；仅编辑当前成品，不增加生成调用",
                level="warn",
            )
        elif not originals:
            self.job.add_log(
                f"{task.id} 未找到可用原始上传，仅编辑当前成品；无法据缺失原图恢复未见视角",
                level="warn",
            )
        elif len(images) - 1 < len(originals):
            self.job.add_log(
                f"{task.id} 受接口 {capacity} 图上限限制，已附加前 {len(images) - 1}/{len(originals)} 张原始上传",
                level="warn",
            )
        task.prompt = prompts.build_rework_reference_prompt(
            task.prompt, task.subject, original_count=len(images) - 1
        )
        request = EditRequest(
            images=images,
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
            original_task = next(t for t in self.job.tasks if (t.item, t.view, t.source) == entry[0])
            self._apply_task_subject(original_task)
            kind = original_task.subject
            request = EditRequest(
                images=[path],
                prompt=prompts.build_style_only(self.garment, subject=kind),
                negative=self.negative_for(self.garment, kind),
                size=self._resolve_size(path),
                quality=str(self.job.params.get("quality") or "high"),
                seed=self.job.params.get("seed"),
                model=self.model,
                input_fidelity=self.input_fidelity,
            )
            try:
                data = await self._guarded_call(request, subject=kind)
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

    def use_effect(self, color: ColorRef | None) -> bool:
        """这一单能不能真的把效果图发出去（接口支持多图 + 该色有图 + 用户没关参考图）。"""
        return bool(
            color is not None
            and color.effect is not None
            and self.send_reference
            and int(getattr(self.provider, "max_images", 1) or 1) >= 2
        )

    def negative_for(self, garment: GarmentType | None, subject: str = "") -> str:
        """按款式+主体取否定词（袖口工艺、是否禁人都可能不同），结果缓存。"""
        kind = self.subject_override
        if kind not in {subject_mod.GARMENT, subject_mod.PERSON}:
            kind = subject_mod.normalise(subject) if subject else ""
        key = f"{(garment.slug if garment is not None else '')}|{kind}"
        cached = self._negatives.get(key)
        if cached is None:
            cached = prompts.full_negative(self.skill, self.pattern_mode, garment, kind)
            self._negatives[key] = cached
        return cached

    async def _execute_color_task(self, task: Task, *, source_override: Path | None = None) -> None:
        self._apply_task_subject(task)
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
        if not task.reference:
            # 记下这张实际拿什么当参考图（界面显示「跟随基准」用）
            task.reference = (
                str(source.relative_to(self.root)).replace("\\", "/")
                if source_override is not None
                else task.source
            )
        images = [source]
        # 派生色（source_override = 基准成品）只发「面料色卡」，不发整张效果图：
        # 效果图带版型，模型偶尔会连版型一起搬（飞书 1526~1542：短袖跑成带帽卫衣）。
        derived = source_override is not None
        swatch = None
        if derived and self.use_effect(color) and color.effect is not None:
            swatch = swatch_mod.swatch_path(color.effect, self.root / "work" / "color-swatches")
        if swatch is not None:
            images.append(swatch)
        elif self.use_effect(color) and color.effect is not None:
            # 首色/基准：效果图是版型+工艺+颜色的权威
            images.append(color.effect)
        elif self.send_reference:
            if color.path.is_file():
                images.append(color.path)
            else:
                # 参考图文件真的不在（数据问题）→ 记一条警告继续跑，别整张失败
                self.job.add_log(
                    f"颜色参考图缺失，已跳过参考：{garment.display_name} / {color.label}",
                    level="warn",
                )
                self._save()
        request_prompt = task.prompt
        original = self._job_image_path(task.source) if source_override is not None else None
        capacity = max(1, int(getattr(self.provider, "max_images", 1) or 1))
        if original is not None and original != source.resolve() and len(images) < capacity:
            # Keep effect/color IMAGE 2 in place; the upload is IMAGE 3 when that slot exists.
            # No crop, rewrite or extra generation call: attach the original file unchanged.
            images.append(original)
            request_prompt += " " + prompts.original_upload_reference_clause(len(images), task.subject)
            if self.pattern_mode == "preserve":
                request_prompt += (
                    " ORIGINAL ARTWORK DETAIL AUTHORITY: the attached unchanged original upload, "
                    "not a generated master or preview, is the source of truth for retained subjects' "
                    "letters, logos, small print, line edges, texture and local sharpness. Never "
                    "inherit blurred, simplified, redrawn or altered artwork from IMAGE 1. Keep "
                    "the original's exact design, letter shapes, spacing and colors without "
                    "inventing fine detail or digital sharpening; retain approved cut and target "
                    "fabric color. This does not restore people explicitly removed by the operator."
                )
        # 带着效果图重塑版型时不能开 input_fidelity=high：高保真会把原图的版型（宽松/落肩）
        # 钉死，效果图就白给了（实测同一条提示词，开=原样搬过来，关=真的变成效果图的紧身版型）。
        # 派生任务（参考图是基准成品）要保形，仍然用原来的设置。
        fidelity = self.input_fidelity
        if source_override is None and self.use_effect(color):
            override = str(self.provider.setting("input_fidelity_with_effect", "") or "").strip()
            fidelity = override or None
        request = EditRequest(
            images=images[:capacity],
            prompt=request_prompt,
            negative=self.negative_for(garment, task.subject),
            size=self._resolve_size(source),
            quality=str(self.job.params.get("quality") or "high"),
            seed=self.job.params.get("seed"),
            model=self.model,
            input_fidelity=fidelity,
        )
        await self._attempt(task, request)

    def _person_alert(self, task: Task, data: bytes) -> dict:
        """仅保留现有背景新增皮肤启发式警告；不是人体检测，也不保证检查穿着原图。"""
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

    async def _guarded_call(self, request: EditRequest, task: Task | None = None,
                            *, subject: str = "") -> bytes:
        """Every API call passes the rpm gate, the shared provider cap, and the job cap.

        配了全局阀门（Cloudflare）时先要一张牌再发请求，回来立刻还牌 —— 同一把 Key
        在多台电脑上跑，总并发也不会超过接口能吃的额度。
        """
        # Final request boundary also covers saved briefs, rework, both phases and retries.
        if task is not None:
            self._apply_task_subject(task)
            subject = task.subject
        elif self.subject_override != subject_mod.AUTO:
            subject = self.subject_override
        request = replace(
            request,
            prompt=prompts.enforce_composition_rule(prompts.enforce_subject_rule(request.prompt, subject)),
            negative=prompts.enforce_subject_negative(request.negative or "", subject),
        )
        prompt, negative = prompts.apply_output_policy(request.prompt, request.negative or "", subject, self.output_requirement)
        prompt, negative = prompts.enforce_artwork_topology(prompt, negative, self.pattern_mode)
        # 接口不支持 negative 字段时（65535 网关 / a6api / fal 等），skill 要求把负面约束
        # **合并到主提示词末尾**，否则那 3~4KB 否定词（"不要罗纹袖口""不要帽兜"…）根本没发给模型 ——
        # 这正是「提示词写了很多规则、模型照样画错」的主要原因之一。
        if negative and not getattr(self.provider, "supports_negative", False):
            prompt = prompts.with_negative(prompt, negative, supported=False)
            negative = ""
        request = replace(request, prompt=prompt, negative=negative)
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
        def package_progress(line: str) -> None:
            if not line.startswith("PACKAGING "):
                return
            self.job.stage = "图片生成完成，校验打包 " + line[len("PACKAGING "):]
            self.job.params["packaging_progress"] = line[len("PACKAGING "):]
            self._save(force=True)
        result = await package_outputs(
            script=self.skill.root / "scripts" / "package_outputs.py",
            staging_dir=self.staging_dir,
            output_dir=self.outputs_dir,
            batch_name=self.job.batch_name or self.garment.batch_name,
            max_bytes=self.skill.max_bytes,
            expected_per_item=expected,
            on_progress=package_progress,
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
