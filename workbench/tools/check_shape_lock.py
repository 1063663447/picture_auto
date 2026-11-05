"""验证「同款版型锁定」：同一款式多颜色必须长得一样，不能各画各的。

背景：同一张原图、同一个款式，按颜色各生成一次时，模型每次都轻微重画轮廓 ——
袖长、袖口角度、衣身宽度、下摆位置全在漂（实测同一单 3 色的轮廓相似度只有 0.3~0.6），
客户一眼就看出「这几张不是同一件衣服」。

修法：同款第一个颜色当**基准版型**，其余颜色以它的成品为参考图，只改面料颜色
（`SILHOUETTE_LOCK_CLAUSE`）；本脚本把这条链路钉成可回归的断言。

用法：
    python tools/check_shape_lock.py            # 离线断言（mock 接口）
    python tools/check_shape_lock.py --show     # 打印每条判定
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import prompts  # noqa: E402
from skillbridge.job import Job as JobModel, Task, new_job_id  # noqa: E402
from skillbridge.providers.base import EditRequest, Provider  # noqa: E402
from skillbridge.runner import JobRunner  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

WORK = Path(__file__).resolve().parent.parent / "data" / "test" / "shape-lock"
LOCK_MARK = "APPROVED MASTER"
FAILURES: list[str] = []


def check(ok: bool, label: str, show: bool) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


class Recorder(Provider):
    """记住每次请求用了哪张参考图，并按来源返回一张可区分的图。"""

    def __init__(self) -> None:
        cfg = {"label": "recorder", "type": "mock", "ready": True, "max_images": 4}
        super().__init__("recorder", cfg)
        # max_images 是类属性，多个颜色参考图要显式调大（否则只会发第一张）
        self.max_images = 4
        self.requests: list[EditRequest] = []

    @property
    def ready(self) -> bool:
        return True

    def info(self) -> dict:
        return {"name": self.name, "label": "recorder", "ready": True, "max_images": 4,
                "max_concurrency": 0, "unlimited": True, "skip_gate": True}

    def setting(self, key: str, default=None):
        return default

    async def open(self) -> None:
        return None

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        self.requests.append(request)
        from PIL import Image

        source = Image.open(request.images[0]).convert("RGB").resize((160, 160))
        import io

        buf = io.BytesIO()
        source.save(buf, format="PNG")
        return buf.getvalue()


def make_runner(wb: Workbench, colors: list[str], *, shape_lock: str, mode: str = "single_pass"):
    root = WORK / "job"
    shutil.rmtree(root, ignore_errors=True)
    (root / "inputs" / "item-01").mkdir(parents=True, exist_ok=True)
    (root / "work").mkdir(parents=True, exist_ok=True)
    (root / "staging").mkdir(parents=True, exist_ok=True)
    (root / "outputs").mkdir(parents=True, exist_ok=True)
    src = root / "inputs" / "item-01" / "item-01.png"
    if not src.is_file():
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (720, 720), (238, 238, 236))
        draw = ImageDraw.Draw(img)
        draw.rectangle([180, 120, 540, 600], fill=(190, 40, 45))
        draw.rectangle([280, 280, 440, 430], fill=(24, 24, 28))
        img.save(src)
    garment = wb.skill.type_by_alias("23-sorona-tee")
    picked = [garment.color(label) for label in colors]
    job = JobModel(
        id=new_job_id(),
        params={
            "mode": mode,
            "provider": "recorder",
            "concurrency": len(colors),
            "subject": "garment",
            "shape_lock": shape_lock,
        },
    )
    job.root = root
    provider = Recorder()
    runner = JobRunner(job, wb.skill, garment, [c for c in picked if c], provider, data_dir=WORK)
    return job, runner, provider, garment


def main() -> int:
    show = "--show" in sys.argv
    wb = Workbench()

    print("① 多颜色 + 锁定：第一个颜色是基准，其余跟随")
    job, runner, provider, garment = make_runner(
        wb, ["red", "pink", "heather-grey"], shape_lock="master"
    )
    runner.plan()
    tasks = job.tasks
    check(len(tasks) == 3, "三个颜色三个任务（数量没变）", show)
    check(tasks[0].master and not tasks[0].master_task, "第一个颜色标记为基准版型", show)
    check(all(t.master_task == tasks[0].id for t in tasks[1:]), "其余颜色的 master_task 指向基准", show)
    check(all(not t.master for t in tasks[1:]), "其余颜色不再是基准", show)
    check(all(LOCK_MARK in t.prompt for t in tasks[1:]), "跟随任务的提示词含版型锁", show)
    check(LOCK_MARK not in tasks[0].prompt, "基准任务不套版型锁（它要按原图改款/换色）", show)
    check(
        prompts.SILHOUETTE_LOCK_CLAUSE in tasks[1].prompt,
        "版型锁原文完整注入", show,
    )
    check("do not move the print" in tasks[1].prompt, "版型锁钉住印花位置", show)

    print("② 锁定关闭：每色独立，谁也不是基准")
    job_off, runner_off, _, _ = make_runner(wb, ["red", "pink"], shape_lock="off")
    runner_off.plan()
    check(not any(t.master or t.master_task for t in job_off.tasks), "关闭后没有任何基准标记", show)
    check(all(LOCK_MARK not in t.prompt for t in job_off.tasks), "关闭后提示词不带版型锁", show)

    print("③ 单颜色：不触发锁定")
    job_one, runner_one, _, _ = make_runner(wb, ["red"], shape_lock="master")
    runner_one.plan()
    check(not any(t.master or t.master_task for t in job_one.tasks), "单颜色不需要基准", show)

    print("④ 跑一遍（mock）：跟随任务真的把基准成品当参考图")
    job2, runner2, provider2, _ = make_runner(wb, ["red", "pink"], shape_lock="master")
    asyncio.run(runner2.run())
    runner2.plan() if not job2.tasks else None
    master = job2.tasks[0]
    derived = job2.tasks[1]
    check(master.status == "done", "基准色出图成功", show)
    check(derived.status == "done", "跟随色出图成功", show)
    check(derived.reference == (master.out or ""), "跟随任务记录了基准成品为参考图", show)
    refs = [str(req.images[0].name) for req in provider2.requests]
    check(len(provider2.requests) >= 2, "两次请求都发生了", show)
    if len(provider2.requests) >= 2:
        second = provider2.requests[1]
        check(
            Path(master.out or "").name == Path(second.images[0]).name,
            "第二次请求的参考图 = 基准成品", show,
        )
        check(len(second.images) >= 2, "参考图之外还带了颜色参考图", show)
        check(LOCK_MARK in second.prompt, "请求里的提示词带版型锁", show)
    print(f"  参考图顺序：{refs}")

    print("⑤ 基准失败时退回原图（并写警告）")
    job3, runner3, provider3, _ = make_runner(wb, ["red", "pink"], shape_lock="master")
    runner3.plan()
    runner3.job.tasks[0].status = "failed"
    runner3.job.tasks[0].out = None
    runner3.job.tasks[0].finished_at = 1.0
    asyncio.run(runner3._execute_shape_task(runner3.job.tasks[1]))
    derived3 = runner3.job.tasks[1]
    check(derived3.status == "done", "基准缺失时跟随任务仍然出图", show)
    check(derived3.master_task == "", "退回后清掉 master_task", show)
    check(
        any("基准色不可用" in str(getattr(entry, "text", entry)) for entry in (job3.log or [])),
        "日志里记了「基准色不可用」", show,
    )

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("全部通过：同款多色的版型由基准色统一")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
