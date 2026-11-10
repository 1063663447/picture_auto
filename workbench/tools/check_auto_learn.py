# -*- coding: utf-8 -*-
"""主动沉淀（自学习）回归：确认基准图时**不需要用户说「记住」**也会把规则记进款式。

客户要求：「是否有沉淀 skill 和记忆（主动沉淀和自学习，而不是需要用户说明才沉淀）」。

链路：
  生成 → 基准图停在 review
       → 用户在对话里提修改（reject_master，会记进 job.params.master_reviews）
       → 改完确认（approve_master，note 可以留空）
       → **自动**把「这次对齐过的要求」写进 library/prompts/<slug>.json 的 lessons
       → 下次同款生成的提示词自动带上

用法：python tools/check_auto_learn.py [--show]
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

from skillbridge import prompts, style_memory  # noqa: E402
from skillbridge.providers.base import EditRequest, Provider  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

FAILURES: list[str] = []
STYLES = [("23-sorona-tee", ["white", "black"]), ("11-kids-dolman-tee", ["black", "pink"])]


def check(ok: bool, label: str, show: bool = False) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


def canvas(path: Path, color=(190, 40, 45)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (512, 512), (242, 242, 244))
    draw = ImageDraw.Draw(image)
    draw.rectangle([150, 90, 360, 430], fill=color)
    image.save(path)
    return path


def png_bytes(color=(30, 30, 34)) -> bytes:
    image = Image.new("RGB", (256, 256), color)
    ImageDraw.Draw(image).text((90, 120), "OUT", fill=(250, 250, 250))
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
        return {"name": self.name, "label": "recorded", "ready": True,
                "max_images": self.max_images, "max_concurrency": 0,
                "unlimited": True, "skip_gate": True}

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
    (library / "prompts").mkdir(parents=True, exist_ok=True)
    # 清空拷过来的 lessons，保证测的是「这次自动学到的」
    for path in (library / "prompts").glob("*.json"):
        if path.name == "index.json":
            continue
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("lessons", None)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(bench.library_dir / "styles.json", library / "styles.json")
    bench.config.setdefault("library", {})["dir"] = str(library)
    recorder = Recorder()
    bench.get_provider = lambda name=None: recorder       # type: ignore[assignment]
    bench.gate_for = lambda provider=None: None           # type: ignore[assignment]
    bench.defaults()["auto_learn"] = True
    bench.defaults()["auto_learn_online"] = False   # 离线判定，结果可复现
    bench.reload_skill()
    return bench, recorder


async def wait_job(bench: Workbench, job_id: str, timeout: float = 60.0) -> None:
    task = bench._tasks.get(job_id)
    if task is not None:
        await asyncio.wait_for(task, timeout=timeout)


async def scenario(show: bool) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="auto-learn-", dir=os.environ.get("PI_SCRATCH_DIR") or None))
    bench: Workbench | None = None
    try:
        bench, recorder = build_bench(tmp)
        upload = canvas(tmp / "upload" / "item-01" / "view-01.png")

        print("① 多款式批量：每款都停在基准待确认")
        job = bench.create_job(
            [("view-01.png", upload.read_bytes())],
            garment_type=STYLES[0][0],
            selections=[{"garment_type": slug, "colors": colors} for slug, colors in STYLES],
            provider="recorder",
            subject="garment",
            review_master=True,
            concurrency=1,
        )
        check(job.params.get("review_gate") is True, "多款式也开了基准闸门", show)
        bench.start(job.id)
        await wait_job(bench, job.id)
        check(job.status == "review", f"停在 review（{job.status}）", show)
        masters = [t for t in job.tasks if t.master]
        check(len(masters) == len(STYLES), f"基准图 {len(masters)} 张（每款一张）", show)

        print("② 对话里提两次修改（不会有人教它「要记住」）")
        rework1 = await bench.reject_master(job.id, "领口再小一点，别太大")
        await wait_job(bench, rework1.id)
        rework2 = await bench.reject_master(job.id, "印花和文字必须原样，不要重画")
        await wait_job(bench, rework2.id)
        check(bool(job.params.get("master_reviews")), "两次修改都记在任务里", show)
        check(len(job.params.get("master_reviews") or []) == 2, "共 2 条对齐记录", show)

        print("③ 确认基准图：**note 留空**，看它会不会自己沉淀")
        before = {
            slug: len(style_memory.load_lessons(bench.library_dir, slug)) for slug, _ in STYLES
        }
        payload = await bench.approve_master(job.id, note="")
        await wait_job(bench, job.id)
        summary = await bench.await_distill(job.id)      # 后台静默评估
        learned_total = int(summary.get("remembered") or 0)
        check(learned_total >= 2,
              f"确认后后台自动评估沉淀 {learned_total} 条（没让用户说记住）", show)
        for slug, _colors in STYLES:
            lessons = style_memory.load_lessons(bench.library_dir, slug)
            grew = len(lessons) - before[slug]
            check(grew >= 1, f"{slug}：自动学到的规则 {grew} 条（共 {len(lessons)}）", show)
        zh_text = " ".join(
            str(item.get("zh"))
            for slug, _ in STYLES
            for item in style_memory.load_lessons(bench.library_dir, slug)
        )
        check("领口再小一点" in zh_text, "修改里说的「领口再小一点」被记下了", show)
        check("印花和文字必须原样" in zh_text, "第二条修改也被记下了", show)
        check(job.status in {"done", "partial"}, f"确认后改色跑完（{job.status}）", show)

        print("④ 自学习结果马上生效（下次同款生成带着规则）")
        bench.reload_skill()
        garment = bench.skill.type_by_alias(STYLES[0][0])
        built = prompts.build_single_pass(bench.skill, garment, garment.colors[0], subject="garment")
        block = "SKILL-LEVEL RULES" if "SKILL-LEVEL RULES" in built else "STYLE RULES LEARNED"
        check(block in built, f"提示词里出现规则段（{block}）", show)
        check("领口再小一点" in built or "collar" in built.lower(),
              "规则内容真的进了提示词", show)

        print("⑤ 再去确认一次：同一句不重复记，也不会重复注入")
        job2 = bench.create_job(
            [("view-01.png", upload.read_bytes())],
            garment_type=STYLES[0][0],
            selections=[{"garment_type": STYLES[0][0], "colors": ["white", "black"]}],
            provider="recorder",
            subject="garment",
            review_master=True,
            concurrency=1,
        )
        bench.start(job2.id)
        await wait_job(bench, job2.id)
        await bench.reject_master(job2.id, "领口再小一点，别太大")
        await wait_job(bench, bench.get_job(job2.id).params.get("master_reviews")[0]["rework_job"])
        count_before = len(style_memory.load_lessons(bench.library_dir, STYLES[0][0]))
        await bench.approve_master(job2.id, note="")
        await wait_job(bench, job2.id)
        await bench.await_distill(job2.id)
        count_after = len(style_memory.load_lessons(bench.library_dir, STYLES[0][0]))
        check(count_after == count_before,
              f"重复的对齐要求只累加命中次数，不新增（{count_before} → {count_after}）", show)
        hits = max(
            (int(item.get("hits") or 1)
             for item in style_memory.load_lessons(bench.library_dir, STYLES[0][0])),
            default=1,
        )
        check(hits >= 2, f"命中次数累加到 {hits}", show)

        print()
        if FAILURES:
            print(f"失败 {len(FAILURES)} 条：")
            for item in FAILURES:
                print("  -", item)
            return 1
        print("全部通过：确认即自动沉淀（无需用户说「记住」），并立刻作用于下次生成")
        return 0
    finally:
        # 收尾：先把还在跑的后台任务停掉，再删临时目录（否则 job.json.tmp 会写空）
        if bench is not None:
            for task in list(bench._tasks.values()):
                task.cancel()
            await asyncio.sleep(0.8)
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    return asyncio.run(scenario("--show" in sys.argv))


if __name__ == "__main__":
    raise SystemExit(main())
