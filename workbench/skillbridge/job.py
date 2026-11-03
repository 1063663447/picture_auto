"""Job / task model and on-disk job state."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ITEM_PREFIX = re.compile(r"^(?P<item>item[-_]?\d+)(?:[-_](?P<view>view[-_]?\d+))?$", re.I)

TERMINAL = {"done", "failed", "skipped", "canceled"}


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


@dataclass
class Task:
    id: str
    item: str
    view: str
    color: str
    color_hex: str
    color_name: str
    source: str                     # relative to the job dir
    prompt: str = ""
    status: str = "pending"          # pending|running|done|failed|skipped|canceled
    attempts: int = 0
    error: str | None = None
    out: str | None = None           # relative to the job dir
    started_at: float | None = None
    finished_at: float | None = None
    bytes: int = 0

    @property
    def duration(self) -> float | None:
        if self.started_at and self.finished_at:
            return round(self.finished_at - self.started_at, 2)
        return None

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

    # -- runtime-only ------------------------------------------------------- #
    root: Path | None = field(default=None, repr=False)

    # -- helpers ------------------------------------------------------------ #
    def add_log(self, message: str, level: str = "info") -> None:
        self.log.append({"t": round(time.time(), 3), "level": level, "msg": message})
        if len(self.log) > 500:
            del self.log[: len(self.log) - 500]
        self.updated_at = time.time()

    def counts(self) -> dict:
        stats = {"total": len(self.tasks), "pending": 0, "running": 0, "done": 0,
                 "failed": 0, "skipped": 0, "canceled": 0}
        for task in self.tasks:
            stats[task.status] = stats.get(task.status, 0) + 1
        stats["finished"] = stats["done"] + stats["skipped"]
        return stats

    def as_dict(self, *, with_tasks: bool = True, with_log: bool = True) -> dict:
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
            "stats": stats,
            "progress": round(
                100.0 * (stats["done"] + stats["skipped"] + stats["failed"] + stats["canceled"])
                / max(1, stats["total"]),
                1,
            ),
        }
        if with_tasks:
            payload["tasks"] = [
                {**task.as_dict(),
                 "thumb": f"/api/jobs/{self.id}/image/{task.id}" if task.out else None}
                for task in self.tasks
            ]
        if with_log:
            payload["log"] = self.log[-80:]
        return payload

    # -- persistence -------------------------------------------------------- #
    def path(self) -> Path:
        if self.root is None:
            raise RuntimeError("job.root 未设置")
        return self.root / "job.json"

    def save(self) -> None:
        self.updated_at = time.time()
        payload = self.as_dict(with_tasks=True, with_log=True)
        payload["zip_rel"] = self.zip_rel
        payload["dir_rel"] = self.dir_rel
        target = self.path()
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
                    source=raw.get("source") or "",
                    prompt=raw.get("prompt") or "",
                    status=raw.get("status") or "pending",
                    attempts=raw.get("attempts") or 0,
                    error=raw.get("error"),
                    out=raw.get("out"),
                    bytes=raw.get("bytes") or 0,
                )
            )
        return job

    @classmethod
    def load(cls, path: Path) -> "Job":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_payload(payload, path.parent)
