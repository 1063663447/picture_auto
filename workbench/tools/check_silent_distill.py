# -*- coding: utf-8 -*-
"""静默评估回归：拍板后后台评判「款式通病 vs 一次性微调」，只把前者沉淀进 skill/记忆。

客户要求（2026-11-11）：
> 对齐的需求拍板后，harness 后台静默评估本次需求对齐的重要性（有的只是针对当前衣服微调，
> 有的是原 skill 有问题导致款式/图案跑偏）来沉淀自身记忆和 skill；沉淀后每次生图有对应款式时，
> 要在对应款式的 skill、上下文、记忆中运用，尽量降低基准图的返工率。

这个脚本钉四件事：
  ① 评判本身：款式级缺陷 → style+promote；「这张裁切再往下点」→ one_off 不注入
  ② 拍板后才跑、且不阻塞改色（approve 立即返回 running，distill 后台补上）
  ③ 沉淀分层：promote 的进 SKILL-LEVEL RULES（靠前），普通进 STYLE RULES LEARNED，
     one_off 只留档不注入
  ④ 统计：返工轮次 / 沉淀条数写进款式 stats，能看「返工率」趋势

用法：python tools/check_silent_distill.py [--show]
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

from skillbridge import align_judge, prompts, style_memory  # noqa: E402
from skillbridge.providers.base import EditRequest, Provider  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

FAILURES: list[str] = []
SLUG = "23-sorona-tee"


def check(ok: bool, label: str, show: bool = False) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


def canvas(path: Path, color=(190, 40, 45)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (512, 512), (242, 242, 244))
    ImageDraw.Draw(image).rectangle([150, 90, 360, 430], fill=color)
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
    import json

    for path in (library / "prompts").glob("*.json"):
        if path.name == "index.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("lessons", None)
        payload.pop("stats", None)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(bench.library_dir / "styles.json", library / "styles.json")
    bench.config.setdefault("library", {})["dir"] = str(library)
    bench.defaults()["auto_learn"] = True
    # 离线判定：结果可复现（生产默认让 harness 模型复核）
    bench.defaults()["auto_learn_online"] = False
    recorder = Recorder()
    bench.get_provider = lambda name=None: recorder       # type: ignore[assignment]
    bench.gate_for = lambda provider=None: None           # type: ignore[assignment]
    bench.reload_skill()
    return bench, recorder


async def wait_job(bench: Workbench, job_id: str, timeout: float = 60.0) -> None:
    task = bench._tasks.get(job_id)
    if task is not None:
        await asyncio.wait_for(task, timeout=timeout)


def judge_cases(show: bool) -> None:
    print("① 评判本身：款式通病 vs 一次性微调")
    cases = [
        ("袖口总是画出罗纹收口，应该是平口", "style", True, "garment"),
        ("帽绳颜色串了，黑卫衣配了白绳", "style", True, "garment"),
        ("印花总是往左偏，位置不对", "style", True, "artwork"),
        ("这张裁切再往下一点，头部留多了", "one_off", False, "scene"),
        ("这一张颜色再亮一点点就行", "one_off", False, "colour"),
        ("以后这个款的领口统一小一号", "style", True, "garment"),
    ]
    for text, scope, promote, kind in cases:
        verdict = align_judge.judge(text, online=False)
        check(verdict.scope == scope, f"「{text[:14]}…」判为 {verdict.scope}（期望 {scope}）", show)
        if scope == "style":
            check(verdict.promote is promote, f"「{text[:14]}…」promote={verdict.promote}", show)
            check(verdict.kind == kind, f"「{text[:14]}…」类别 {verdict.kind}（期望 {kind}）", show)
        else:
            check(not verdict.promote, f"「{text[:14]}…」不升级", show)
    check(align_judge.judge("", online=False).importance == 0.0, "空要求不产生结论", show)


async def scenario(show: bool) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="distill-", dir=os.environ.get("PI_SCRATCH_DIR") or None))
    bench: Workbench | None = None
    try:
        bench, _recorder = build_bench(tmp)
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
        check(job.status == "review", f"停在基准待确认（{job.status}）", show)

        print("② 拍板前：什么都不该记")
        check(not style_memory.load_lessons(bench.library_dir, SLUG), "确认前没有任何沉淀", show)

        print("③ 两轮对齐：一条款式通病 + 一条纯微调")
        rework = await bench.reject_master(job.id, "袖口总是画出罗纹收口，应该是平口")
        await wait_job(bench, rework.id)
        rework2 = await bench.reject_master(job.id, "这张裁切再往下一点，头部留多了")
        await wait_job(bench, rework2.id)
        stats_before = style_memory.load_stats(bench.library_dir, SLUG)
        check(int(stats_before.get("review_rounds") or 0) >= 2,
              f"返工轮次已记账：{stats_before.get('review_rounds')}", show)

        print("④ 拍板：改色先跑，评估在后台")
        payload = await bench.approve_master(job.id, note="")
        check((payload.get("distill") or {}).get("state") == "running",
              f"接口立即返回、评估后台跑（{payload.get('distill')}）", show)
        await wait_job(bench, job.id)
        check(job.status in {"done", "partial"}, f"改色照常跑完（{job.status}）", show)
        summary = await bench.await_distill(job.id)
        check(summary.get("state") == "done", f"评估结束（{summary.get('state')}）", show)
        check(summary.get("total") == 2, f"评估了 {summary.get('total')} 条要求", show)
        check(summary.get("promoted") == 1, f"其中 {summary.get('promoted')} 条升级为硬规则", show)
        check(summary.get("dropped") == 1, f"{summary.get('dropped')} 条判为一次性微调", show)

        print("⑤ 沉淀分层：只把款式通病写进提示词")
        lessons = style_memory.load_lessons(bench.library_dir, SLUG)
        promoted = [item for item in lessons if item.get("promote")]
        one_off = [item for item in lessons if str(item.get("scope")) == "one_off"]
        check(len(promoted) == 1, f"硬规则 {len(promoted)} 条", show)
        check(len(one_off) == 1, f"一次性微调留档 {len(one_off)} 条（不注入）", show)
        bench.reload_skill()
        garment = bench.skill.type_by_alias(SLUG)
        built = prompts.build_single_pass(bench.skill, garment, garment.colors[0], subject="garment")
        check("SKILL-LEVEL RULES FOR THIS STYLE" in built, "提示词里有款式级硬规则段", show)
        check("STYLE RULES LEARNED" not in built or "裁切" not in built,
              "一次性微调没被写进提示词", show)
        hard = prompts.promoted_clause(garment)
        check("rib" in hard.lower() or "plain" in hard.lower() or "袖" in hard,
              f"硬规则内容是袖口那条：{hard[:60]}", show)
        check("裁切" not in built, "提示词里没有「裁切」这类一次性要求", show)

        print("⑥ 统计：返工率可以看了")
        stats = style_memory.load_stats(bench.library_dir, SLUG)
        check(int(stats.get("review_rounds") or 0) >= 2, f"review_rounds={stats.get('review_rounds')}", show)
        check(int(stats.get("promoted") or 0) >= 1, f"promoted={stats.get('promoted')}", show)
        check(bool(stats.get("updated_at")), "统计带更新时间", show)

        print()
        if FAILURES:
            print(f"失败 {len(FAILURES)} 条：")
            for item in FAILURES:
                print("  -", item)
            return 1
        print("全部通过：静默评估 → 只沉淀款式级问题（硬规则）→ 下次生图自动生效")
        return 0
    finally:
        if bench is not None:
            for task in list(bench._tasks.values()):
                task.cancel()
            await asyncio.sleep(0.8)
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    show = "--show" in sys.argv
    judge_cases(show)
    return asyncio.run(scenario(show))


if __name__ == "__main__":
    raise SystemExit(main())
