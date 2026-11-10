# -*- coding: utf-8 -*-
"""框选标注回归：标注图真的被存下来、真的随改图发给接口、提示词里声明「框外不动」。

客户要求（2026-11-11）：
> 直接点击要改的基准图 → 大图 → 给用户提供框框，框出有歧义的部分并在框下注明要改的效果/问题
> → 提供给 harness 分析和提示词修改（也可以同步把带框的基准图返给生图接口方便参考），
> 提高修改成功率。

这个脚本验证后端这条链：
  ① 标注能落盘（图 + 框信息 + 备注），路径安全；
  ② 改图请求里：第 1 张是当前成品、第 2 张是**标注图**，原始上传顺延；
  ③ 提示词里出现 ANNOTATION 约束（「框线/编号不是设计、只在框内改」）；
  ④ 标注信息进了 harness 上下文（agent 拿到框的位置与要求）。
"""
from __future__ import annotations

import asyncio
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from skillbridge import agent, prompts  # noqa: E402
from skillbridge.providers.base import EditRequest, Provider  # noqa: E402
from skillbridge.service import Workbench, WorkbenchError  # noqa: E402

FAILURES: list[str] = []
SLUG = "23-sorona-tee"


def check(ok: bool, label: str, show: bool = False) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


def canvas(path: Path, color=(240, 240, 238)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (640, 640), (244, 244, 246))
    draw = ImageDraw.Draw(image)
    draw.rectangle([180, 130, 460, 520], fill=color)
    image.save(path)
    return path


def annotate_png(boxes: list[dict], size=(640, 640)) -> bytes:
    """模拟前端画好框的标注图（红框 + 编号）。"""
    image = Image.new("RGB", size, (244, 244, 246))
    draw = ImageDraw.Draw(image)
    draw.rectangle([180, 130, 460, 520], fill=(240, 240, 238))
    for box in boxes:
        x, y = box["x"] * size[0], box["y"] * size[1]
        w, h = box["w"] * size[0], box["h"] * size[1]
        draw.rectangle([x, y, x + w, y + h], outline=(255, 59, 59), width=4)
        draw.text((x + 6, max(2, y - 16)), str(box["n"]), fill=(255, 59, 59))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def png_bytes(color=(30, 30, 34)) -> bytes:
    image = Image.new("RGB", (256, 256), color)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


class Recorder(Provider):
    supports_negative = True
    max_images = 4

    def __init__(self) -> None:
        super().__init__("recorder", {"label": "recorder", "type": "mock", "ready": True})
        self.requests: list[EditRequest] = []

    def info(self) -> dict:
        return {"name": self.name, "label": "recorded", "ready": True, "max_images": 4,
                "max_concurrency": 0, "unlimited": True, "skip_gate": True}

    async def open(self) -> None:
        return None

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        self.requests.append(request)
        return png_bytes()


def build_bench(tmp: Path) -> tuple[Workbench, Recorder]:
    bench = Workbench()
    bench.data_dir = tmp / "data"
    bench.jobs_dir = bench.data_dir / "jobs"
    bench.jobs_dir.mkdir(parents=True, exist_ok=True)
    library = tmp / "library"
    for name in ("prompts", "refs", "effects"):
        source = bench.library_dir / name
        if source.is_dir():
            shutil.copytree(source, library / name, dirs_exist_ok=True)
    shutil.copy2(bench.library_dir / "styles.json", library / "styles.json")
    bench.config.setdefault("library", {})["dir"] = str(library)
    bench.defaults()["auto_learn"] = False
    recorder = Recorder()
    bench.get_provider = lambda name=None: recorder       # type: ignore[assignment]
    bench.gate_for = lambda provider=None: None           # type: ignore[assignment]
    bench.reload_skill()
    return bench, recorder


async def wait_job(bench: Workbench, job_id: str, timeout: float = 60.0) -> None:
    task = bench._tasks.get(job_id)
    if task is not None:
        await asyncio.wait_for(task, timeout=timeout)


async def scenario(show: bool) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="annotate-", dir=os.environ.get("PI_SCRATCH_DIR") or None))
    bench: Workbench | None = None
    try:
        bench, recorder = build_bench(tmp)
        upload = canvas(tmp / "upload" / "item-01" / "view-01.png")
        job = bench.create_job(
            [("view-01.png", upload.read_bytes())],
            garment_type=SLUG,
            selections=[{"garment_type": SLUG, "colors": ["white", "black"]}],
            provider="recorder",
            subject="garment",
            review_master=True,
            concurrency=1,
        )
        bench.start(job.id)
        await wait_job(bench, job.id)
        master = next(t for t in job.tasks if t.master)
        check(job.status == "review" and master.out, "基准图已出，停在待确认", show)

        print("① 标注落盘：图 + 框 + 备注")
        boxes = [
            {"n": 1, "x": 0.31, "y": 0.10, "w": 0.38, "h": 0.12, "note": "领口太大，收小一圈"},
            {"n": 2, "x": 0.34, "y": 0.42, "w": 0.32, "h": 0.26, "note": "印花往中间挪，别压到侧缝"},
        ]
        saved = bench.save_annotation(job.id, master.id, annotate_png(boxes), boxes)
        check(bool(saved.get("id")), f"返回标注 id：{saved.get('id')}", show)
        annotated_file = (job.root / saved["id"])
        check(annotated_file.is_file(), "标注图已写到任务目录", show)
        check((job.root / saved["id"]).stat().st_size > 500, "标注图不是空文件", show)
        stored = bench.annotations_for(job.id).get(master.id) or {}
        check(len(stored.get("boxes") or []) == 2, f"框信息已记下：{len(stored.get('boxes') or [])} 个", show)
        check((stored.get("boxes") or [{}])[0].get("note") == "领口太大，收小一圈",
              "框的备注也存了", show)
        try:
            bench.reject_master  # noqa: B018 - 只是确认方法在
            bad = bench._annotation_path(job, "../etc/passwd")
            check(bad is None, "越界路径被拒", show)
        except WorkbenchError:
            check(True, "越界路径被拒", show)

        print("② 改图带上标注图（IMAGE 2）+ 提示词约束")
        instruction = "按框选改：框1 领口收小一圈；框2 印花往中间挪"
        rework = await bench.reject_master(
            job.id, instruction, [master.id], annotation_id=saved["id"]
        )
        await wait_job(bench, rework.id)
        check(rework.status in {"done", "partial"}, f"改图任务跑完（{rework.status}）", show)
        request = recorder.requests[-1]
        names = [path.name for path in request.images]
        check(len(request.images) >= 2, f"请求带了多张图（{len(request.images)}）", show)
        rework_source = (rework.root / rework.tasks[0].source)
        check(request.images[0] == rework_source.resolve(),
              f"IMAGE 1 是当前成品（{names[0]}）", show)
        check(len(names) > 1 and names[1].startswith("annotation"),
              f"IMAGE 2 是标注图（{names[1] if len(names) > 1 else '无'}）", show)
        if len(request.images) > 1:
            check(request.images[1].read_bytes() == annotated_file.read_bytes(),
                  "标注图内容与保存的一致", show)
        check(prompts.annotation_clause(2) in request.prompt, "提示词里有「标注是地图、不是设计」约束", show)
        check("OPERATOR ANNOTATION MAP" in request.prompt, "约束段名写进了提示词", show)
        check("must NEVER appear in the result" in request.prompt,
              "明确禁止把框线/编号画进成品", show)
        check(instruction[:8] in request.prompt or "框选" in request.prompt,
              "用户的要求也在提示词里", show)

        print("③ harness 上下文：框的位置与要求都给了 AI")
        rects = [
            {"n": 1, "x": 0.31, "y": 0.10, "w": 0.38, "h": 0.12, "note": "领口太大，收小一圈"},
            {"n": 2, "x": 0.34, "y": 0.42, "w": 0.32, "h": 0.26, "note": "印花往中间挪"},
        ]
        ctx = agent.AgentContext(mode="master", garment="23 索罗娜短袖", job_id=job.id,
                                 task_ids=[master.id], annotations=rects)
        intro = agent._intake_intro(ctx, "按框选改")
        check("画框标注" in intro, "开场说明用户已经框选标注", show)
        check("框1" in intro and "领口" in intro, "框1 的内容进了上下文", show)
        check("框2" in intro and "印花" in intro, "框2 的内容进了上下文", show)
        check("别扩大到框外" in intro, "上下文要求「只改框内」", show)

        print("④ 9.6.7 前端/接口的框选细节")
        root = Path(__file__).resolve().parent.parent
        app = (root / "web" / "app.js").read_text(encoding="utf-8")
        index = (root / "web" / "index.html").read_text(encoding="utf-8")
        css = (root / "web" / "annotate.css").read_text(encoding="utf-8")
        api = (root / "skillbridge" / "api.py").read_text(encoding="utf-8")
        check('class="annotate-frame"' in index, "框层和图片装在同一个盒子里（框跟手）", show)
        check(".annotate-frame{position:relative" in css and
              ".annotate-stage svg{position:absolute;left:0;top:0" in css,
              "框层相对图片定位，不是铺满整块", show)
        check("try { stage.setPointerCapture?.(event.pointerId); } catch (_)" in app,
              "指针 capture 抛错不影响画框", show)
        check("runAnnotationRound(" in app and "streamQuiet(" in app,
              "「交给 AI 改」直接发送（不再等用户点第二次）", show)
        check("watchReworkFor(" in app and "const reworkTimers = new Map();" in app,
              "改图进度按 jobId 分开盯（可多张并发）", show)
        check("if (st.working)" in app and 'data-state="${kind}"' in app,
              "卡片有「改图中」状态和转圈占位", show)
        check("adoptRound(" in app and "::${rw.shots[0].id}" in app,
              "第 2 轮可以直接采纳当基准", show)
        check("wanted = {str(item) for item in task_ids}" in api,
              "接口只处理点名的那一张基准图（不连累其它款色）", show)

        print()
        if FAILURES:
            print(f"失败 {len(FAILURES)} 条：")
            for item in FAILURES:
                print("  -", item)
            return 1
        print("全部通过：框选标注 → 标注图随改图发出 → 提示词限定框内 → AI 拿到框的位置与要求")
        return 0
    finally:
        if bench is not None:
            for task in list(bench._tasks.values()):
                task.cancel()
            await asyncio.sleep(0.8)
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    return asyncio.run(scenario("--show" in sys.argv))


if __name__ == "__main__":
    raise SystemExit(main())
