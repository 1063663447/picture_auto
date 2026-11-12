"""Job / task model and on-disk job state."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ITEM_PREFIX = re.compile(r"^(?P<item>item[-_]?\d+)(?:[-_](?P<view>view[-_]?\d+))?$", re.I)
#: 上传进 inputs/<item>/ 之后，多视角文件的文件名就只剩 `view-02`（见 service.create_job）
VIEW_ONLY = re.compile(r"^view[-_]?(\d+)$", re.I)

#: 文件名里不能出现的字符（Windows 最严：`< > : " / \ | ? *` 与控制字符）
UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

TERMINAL = {"done", "failed", "skipped", "canceled"}
JOB_TERMINAL = {"done", "partial", "failed", "canceled"}


def safe_name_part(text: str, fallback: str = "") -> str:
    """把款式名/颜色名变成文件名片段：去掉非法字符和空白（`24 长袖成人` → `24长袖成人`）。"""
    cleaned = UNSAFE_NAME.sub("", str(text or "")).strip()
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned or fallback


def job_stamp(job_id: str) -> str:
    """成品文件名里的批次尾号：`20261105` + job id 最后 4 位（如 `2219`）。

    job id 形如 `20261105-102305-2219` → `202611052219`，
    和客户要求的 `24CXCR_WH[0]_20261105xxxx` 一致，同一天多单也不会撞名。
    """
    parts = [part for part in str(job_id or "").replace("_", "-").split("-") if part]
    today = time.strftime("%Y%m%d")
    if not parts:
        return today
    date_part = parts[0] if len(parts[0]) == 8 and parts[0].isdigit() else today
    tail = re.sub(r"[^0-9A-Za-z]", "", parts[-1])[-4:]
    return f"{date_part}{tail}"


def output_stem(
    task: "Task",
    *,
    style_key: str = "",
    color_key: str = "",
    stamp: str = "",
    tag: str = "",
) -> str:
    """成品文件名（不含扩展名）：**款式英文代号_色号编码[_补充]_批次号**。

    客户要的是英文编码，且要能直接对回款式表：
    `24CXCR_WH[0]_202611052219.png`（= 24 长袖成人 · 白色 · 2026-11-05 那单）。
    同一款同色出现多张（多原图 / 多视角）时中间补一段 `item-02` / `view-02`，避免互相覆盖。
    """
    style = safe_name_part(style_key or task.garment_name or task.garment, "STYLE")
    color = safe_name_part(color_key or task.color_name or task.color, "COLOR")
    parts = [style, color]
    if tag:
        parts.append(safe_name_part(tag, "extra"))
    if stamp:
        parts.append(safe_name_part(stamp, ""))
    return "_".join(part for part in parts if part)


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def normalise_item(file_stem: str) -> tuple[str, str]:
    """Derive (item, view) from an uploaded file name.

    `item-01_view-02.png` -> ("item-01", "view-02") so multiple angles of the same
    garment land in one item folder, matching the skill's packaging rules.
    Any other name becomes its own item.
    """
    match = ITEM_PREFIX.match(file_stem.strip())
    if match:
        item = match.group("item").lower().replace("_", "-")
        view = (match.group("view") or "").lower().replace("_", "-") or ""
        return item, view
    return "", ""


def view_of(file_stem: str) -> str:
    """从文件名取视角号。

    上传落盘后，同一 item 的多视角文件名只剩 `view-02.jpg`（父目录才是 item-01），
    这时 `normalise_item` 认不出来 —— 少了这一步，两个视角会算出同一个任务 id，
    成品文件也会互相覆盖（只剩最后一张）。
    """
    stem = str(file_stem or "").strip()
    match = ITEM_PREFIX.match(stem)
    if match:
        return (match.group("view") or "").lower().replace("_", "-")
    match = VIEW_ONLY.match(stem)
    if match:
        return f"view-{int(match.group(1)):02d}"
    return ""


@dataclass
class Task:
    id: str
    item: str
    view: str
    color: str
    color_hex: str
    color_name: str
    source: str = ""                # relative to the job dir
    garment: str = ""               # 款式 slug（多款式任务时用于区分目录/命名）
    garment_name: str = ""          # 款式展示名
    prompt: str = ""
    #: 主体：garment（原图只有衣服，禁止添加人物）| person（真人上身/不确定）
    subject: str = ""
    #: 成品复查发现「原图是空衣服、成品却冒出人」时为 True（界面/日志用于提示重画）
    person_alert: bool = False
    #: 版型锁定：同款第一个颜色是**基准色**（master=True），其余颜色的 master_task 指向它，
    #: 生成时以基准成品为参考图，只改面料颜色 —— 这样同款各色版型一模一样
    master: bool = False
    master_task: str = ""
    #: 实际用作参考图的文件（版型锁定时是基准成品；否则等同上图 source）
    reference: str = ""
    #: 基准图审核（先出基准 → 运营确认 → 其余颜色按基准改色）：
    #: approved=True 表示这张基准图已被确认/采纳，可以拿它去派生其它颜色
    approved: bool = False
    #: 审核备注：运营确认时的一句话（会沉淀进该款式的 lessons）
    review_note: str = ""
    #: 这张成品是哪一次审核采纳进来的（返工任务 id，便于溯源）
    adopted_from: str = ""
    #: pending（还没轮到）| queued（在飞书队列里排队）| running（正在生成）
    #: | done | failed | skipped | canceled
    status: str = "pending"
    attempts: int = 0
    #: 单张暂停（9.6.31）：paused=True 时这张不会开跑、也不等在审核条上；
    #: paused_from 记住暂停前的状态，继续时还原（基准图成品已出 → 回到 done，否则回 pending）
    paused: bool = False
    paused_from: str = ""
    #: 被哪张基准图连带暂停的（继续那张基准图时一起放回队列）；自己点的暂停这里是空
    paused_by: str = ""
    error: str | None = None
    error_kind: str = ""             # "" | "rate_limit"（被并发限制打回来） | "error"
    out: str | None = None           # relative to the job dir
    started_at: float | None = None
    finished_at: float | None = None
    bytes: int = 0

    @property
    def duration(self) -> float | None:
        if self.started_at and self.finished_at:
            return round(self.finished_at - self.started_at, 2)
        return None

    def file_stem(
        self, *, style_key: str = "", color_key: str = "", stamp: str = "", tag: str = ""
    ) -> str:
        """成品文件名（款式英文代号_色号编码[_补充]_批次号），见 `output_stem`。"""
        return output_stem(
            self, style_key=style_key, color_key=color_key, stamp=stamp, tag=tag
        )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "item": self.item,
            "view": self.view,
            "color": self.color,
            "color_hex": self.color_hex,
            "color_name": self.color_name,
            "status": self.status,
            "attempts": self.attempts,
            "error": self.error,
            "out": self.out,
            "duration": self.duration,
            "bytes": self.bytes,
            "prompt": self.prompt,
            "source": self.source,
            "subject": self.subject,
            "person_alert": self.person_alert,
            "master": self.master,
            "master_task": self.master_task,
            "reference": self.reference,
            "approved": self.approved,
            "review_note": self.review_note,
            "adopted_from": self.adopted_from,
            "paused": self.paused,
            "paused_from": self.paused_from,
            "paused_by": self.paused_by,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class Job:
    id: str
    params: dict
    tasks: list[Task] = field(default_factory=list)
    status: str = "queued"           # queued|running|packaging|done|partial|failed|canceled
    stage: str = "等待开始"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    batch_name: str = ""
    zip_rel: str | None = None
    dir_rel: str | None = None
    error: str | None = None
    log: list[dict] = field(default_factory=list)
    cancel_requested: bool = field(default=False, repr=False)
    total: int = 0
    finished_at: float | None = None

    # -- runtime-only ------------------------------------------------------- #
    root: Path | None = field(default=None, repr=False)

    # -- helpers ------------------------------------------------------------ #
    def add_log(self, message: str, level: str = "info") -> None:
        self.log.append({"t": round(time.time(), 3), "level": level, "msg": message})
        if len(self.log) > 500:
            del self.log[: len(self.log) - 500]
        self.updated_at = time.time()

    def counts(self) -> dict:
        stats = {"total": len(self.tasks), "pending": 0, "queued": 0, "running": 0,
                 "done": 0, "failed": 0, "skipped": 0, "canceled": 0, "paused": 0}
        for task in self.tasks:
            stats[task.status] = stats.get(task.status, 0) + 1
        stats["finished"] = stats["done"] + stats["skipped"]
        stats["rate_limited"] = sum(
            1 for task in self.tasks
            if task.status == "failed" and task.error_kind == "rate_limit"
        )
        return stats

    @property
    def duration(self) -> float | None:
        if self.status in JOB_TERMINAL and self.finished_at is not None:
            return round(max(0.0, self.finished_at - self.created_at), 2)
        return None

    def rework_rounds(self) -> list[dict]:
        """把 `params.master_reviews` 的改图轮次摊平成前端可直接渲染的列表。

        9.6.24：以前「第几轮、改好的那张图」只活在前端内存里，页面一刷新卡片就退回
        「第 1 轮 · 待确认」——改图明明成了，用户看不到也点不了确定，整批就一直挂着。
        现在服务端把每一轮记下来（reject_master 写入 + 返工任务收尾时回填），
        这里按基准图给出**最新一轮**，前端拿它就能恢复卡片。
        """
        entries = (self.params or {}).get("master_reviews")
        if not isinstance(entries, list):
            return []
        latest: dict[str, dict] = {}
        for item in entries:
            if not isinstance(item, dict):
                continue
            rework_job = str(item.get("rework_job") or "")
            targets = [str(t) for t in (item.get("master_ids") or []) if t]
            if not rework_job or not targets:
                continue
            shot = str(item.get("task") or "")
            round_no = int(item.get("round") or 1)
            status = str(item.get("status") or ("done" if shot else "running"))
            for task_id in targets:
                prev = latest.get(task_id)
                if prev is not None and round_no < prev["round"]:
                    continue
                latest[task_id] = {
                    "task_id": task_id,
                    # 前端说「第几轮」是**图**的轮次：第 1 轮就是最初那张基准图，
                    # 第一次改图出来的是第 2 轮。服务端 `rework_round` 数的是「第几次改图」，
                    # 所以这里 +1 对齐卡片上的数字。
                    "round": round_no + 1,
                    "rework_round": round_no,
                    "job_id": rework_job,
                    "status": status,
                    "task": shot,
                    "error": str(item.get("error") or ""),
                    "thumb": f"/api/jobs/{rework_job}/thumb/{shot}?w=520" if shot else None,
                    "preview": f"/api/jobs/{rework_job}/thumb/{shot}?w=1600" if shot else None,
                }
        return sorted(latest.values(), key=lambda r: (r["task_id"], r["round"]))

    def as_dict(
        self, *, with_tasks: bool = True, with_log: bool = True, with_prompts: bool = False
    ) -> dict:
        stats = self.counts()
        payload = {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "params": self.params,
            "batch_name": self.batch_name,
            "zip": f"/api/jobs/{self.id}/download" if self.zip_rel else None,
            "has_zip": bool(self.zip_rel),
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "duration": self.duration,
            "stats": stats,
            "progress": round(
                100.0 * (stats["done"] + stats["skipped"] + stats["failed"] + stats["canceled"])
                / max(1, stats["total"]),
                1,
            ),
        }
        masters = [t for t in self.tasks if t.master]
        if (self.params or {}).get("review_gate") or masters:
            payload["review"] = {
                "enabled": bool((self.params or {}).get("review_gate")),
                "awaiting": self.status == "review",
                "phase": str((self.params or {}).get("phase") or ""),
                "master_ids": [t.id for t in masters],
                "pending_ids": [t.id for t in masters if t.out and not t.approved and not t.paused],
                "paused_ids": [t.id for t in masters if t.paused],
                "approved_ids": [t.id for t in masters if t.approved],
                "derived_total": sum(1 for t in self.tasks if t.master_task),
                "derived_done": sum(1 for t in self.tasks if t.master_task and t.out),
                "rounds": self.rework_rounds(),
                "can_approve": (
                    # 9.6.32 不再要求整批停在 review：还有别的基准在生成时也该能确认先出来的那张
                    any(t.out and not t.approved and not t.paused for t in masters)
                    and any(t.master_task for t in self.tasks)
                ),
            }
        if with_tasks:
            queue_map = (self.params or {}).get("queue_tasks") or {}
            items = []
            for task in self.tasks:
                data = task.as_dict()
                if not with_prompts:
                    # 提示词每条 3~4KB，轮询时必须去掉：10 张图每 1.5s 拉一次会白烧几十 KB/次
                    data.pop("prompt", None)
                data["version"] = self._task_version(task)
                # 这张图自己排在第几（飞书表轮询出来的），界面显示「排队中，还有 x 个」
                note = queue_map.get(task.id) or {}
                data["queue_ahead"] = note.get("ahead")
                # thumb 是原图（下载/大图用），thumb_small 是列表用的缩略图（几 MB → 几十 KB）
                data["error_kind"] = task.error_kind
                data["garment"] = task.garment
                data["garment_name"] = task.garment_name
                data["thumb"] = f"/api/jobs/{self.id}/image/{task.id}" if task.out else None
                data["thumb_small"] = (
                    f"/api/jobs/{self.id}/thumb/{task.id}?w=520&v={self._task_version(task) or 0}"
                    if task.out else None
                )
                # 点开「大图」用 1600 宽的 WebP（约 200KB），要看原图再点下载
                data["preview"] = (
                    f"/api/jobs/{self.id}/thumb/{task.id}?w=1600&v={self._task_version(task) or 0}"
                    if task.out else None
                )
                items.append(data)
            payload["tasks"] = items
        if with_log:
            payload["log"] = self.log[-80:]
        return payload

    def _task_version(self, task: "Task") -> int | None:
        """成品图版本号（毫秒）。图片内容一旦产出就不再变，所以可以拿它当缓存键的一部分。"""
        if task.finished_at:
            return int(task.finished_at * 1000)
        if task.out and self.root:
            try:
                return int((self.root / str(task.out)).stat().st_mtime * 1000)
            except OSError:
                return None
        return None

    # -- persistence -------------------------------------------------------- #
    def path(self) -> Path:
        if self.root is None:
            raise RuntimeError("job.root 未设置")
        return self.root / "job.json"

    def save(self) -> None:
        self.updated_at = time.time()
        if self.status in JOB_TERMINAL:
            self.finished_at = self.finished_at or self.updated_at
        else:
            self.finished_at = None
        payload = self.as_dict(with_tasks=True, with_log=True, with_prompts=True)
        payload["zip_rel"] = self.zip_rel
        payload["dir_rel"] = self.dir_rel
        target = self.path()
        if not target.parent.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        tmp.replace(target)

    @classmethod
    def from_payload(cls, payload: dict, root: Path) -> "Job":
        job = cls(
            id=payload["id"],
            params=payload.get("params") or {},
            status=payload.get("status", "queued"),
            stage=payload.get("stage", ""),
            created_at=payload.get("created_at") or time.time(),
            updated_at=payload.get("updated_at") or time.time(),
            finished_at=(
                payload.get("finished_at") or payload.get("updated_at")
                if payload.get("status") in JOB_TERMINAL else None
            ),
            batch_name=payload.get("batch_name", ""),
            zip_rel=payload.get("zip_rel"),
            dir_rel=payload.get("dir_rel"),
            error=payload.get("error"),
            log=payload.get("log") or [],
        )
        job.root = root
        for raw in payload.get("tasks") or []:
            job.tasks.append(
                Task(
                    id=raw["id"],
                    item=raw["item"],
                    view=raw.get("view") or "",
                    color=raw["color"],
                    color_hex=raw.get("color_hex") or "",
                    color_name=raw.get("color_name") or raw["color"],
                    garment=raw.get("garment") or "",
                    garment_name=raw.get("garment_name") or "",
                    source=raw.get("source") or "",
                    prompt=raw.get("prompt") or "",
                    subject=raw.get("subject") or "",
                    person_alert=bool(raw.get("person_alert")),
                    master=bool(raw.get("master")),
                    master_task=raw.get("master_task") or "",
                    reference=raw.get("reference") or "",
                    status=raw.get("status") or "pending",
                    attempts=raw.get("attempts") or 0,
                    error=raw.get("error"),
                    error_kind=raw.get("error_kind") or "",
                    out=raw.get("out"),
                    bytes=raw.get("bytes") or 0,
                    paused=bool(raw.get("paused")),
                    paused_from=str(raw.get("paused_from") or ""),
                    paused_by=str(raw.get("paused_by") or ""),
                )
            )
        return job

    @classmethod
    def load(cls, path: Path) -> "Job":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_payload(payload, path.parent)
