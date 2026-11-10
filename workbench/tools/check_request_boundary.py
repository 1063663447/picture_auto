"""请求边界回归：negative 字段 + 主体/款式规则在**真正发出去的那一份**里生效。

背景：65535 网关 / a6api 这类接口不支持 `negative_prompt` 字段，工作台以前直接把 negative 丢掉 ——
那 3~4KB 的否定词（"不要罗纹袖口""不要帽兜""不要重画图案"…）从来没到过模型手里，
这也是「提示词写了很多规则、模型照样画错」的主要原因之一。
skill 原文要求：不支持负面提示词时，把它**合并到主提示词末尾**。

用法：python tools/check_request_boundary.py [--show]
"""
from __future__ import annotations

import asyncio
import io
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from skillbridge.job import Job as JobModel, new_job_id  # noqa: E402
from skillbridge.providers.base import EditRequest, Provider  # noqa: E402
from skillbridge.runner import JobRunner  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

WORK = Path(__file__).resolve().parent.parent / "data" / "test" / "request-boundary"
FAILURES: list[str] = []


def check(ok: bool, label: str, show: bool) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


class Recorder(Provider):
    def __init__(self, supports_negative: bool) -> None:
        super().__init__("recorder", {"label": "recorder", "type": "mock", "ready": True})
        self.max_images = 4
        self.supports_negative = supports_negative
        self.requests: list[EditRequest] = []

    @property
    def ready(self) -> bool:
        return True

    def info(self) -> dict:
        return {"name": self.name, "label": "recorder", "ready": True, "max_images": 4,
                "max_concurrency": 0, "unlimited": True, "skip_gate": True}

    def setting(self, key, default=None):
        return default

    async def open(self) -> None:
        return None

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        self.requests.append(request)
        buf = io.BytesIO()
        Image.open(request.images[0]).convert("RGB").resize((96, 96)).save(buf, format="PNG")
        return buf.getvalue()


def build(wb: Workbench, supports_negative: bool):
    root = WORK / "job"
    shutil.rmtree(root, ignore_errors=True)
    for name in ("inputs/item-01", "work", "staging", "outputs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 200), (190, 40, 45)).save(root / "inputs" / "item-01" / "item-01.png")
    garment = wb.skill.type_by_alias("25-kids-long-sleeve")
    job = JobModel(id=new_job_id(),
                   params={"mode": "single_pass", "provider": "recorder", "concurrency": 1,
                           "subject": "person", "send_reference": True})
    job.root = root
    provider = Recorder(supports_negative)
    return job, JobRunner(job, wb.skill, garment, [garment.colors[0]], provider, data_dir=WORK), provider


def main() -> int:
    show = "--show" in sys.argv
    wb = Workbench()

    print("① 不支持 negative 字段的接口：负面约束并进提示词")
    job, runner, provider = build(wb, False)
    asyncio.run(runner.run())
    request = provider.requests[0]
    check("Strict constraints" in request.prompt, "提示词末尾合并了负面约束", show)
    check(not request.negative, "negative 字段已清空（避免重复/无效字段）", show)
    check("remove all of that" in request.prompt.lower(), "合并后的提示词含「去掉源图罗纹收口」", show)
    check("rib-knit" in request.prompt.lower(), "合并后的提示词含罗纹相关否定", show)
    check("CONVERT THE GARMENT TYPE" in request.prompt, "转换段仍在提示词里", show)

    print("② 支持 negative 字段的接口：走原生字段，不塞进提示词")
    job2, runner2, provider2 = build(wb, True)
    asyncio.run(runner2.run())
    request2 = provider2.requests[0]
    check(bool(request2.negative), "negative 字段有内容", show)
    check("Strict constraints" not in request2.prompt, "提示词里没有重复合并", show)

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("通过：请求边界按接口能力正确处理负面约束")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
