"""验证「原图 + 效果图」这条链路：效果图导入、随请求发送、提示词说清分工。

背景：客户要求以后生图都带着**原图 + 效果图**（款式表里每色一张官方平铺产品图）。
效果图是版型/工艺/颜色的权威参考，有了它同款各色天然一致，原来的「版型锁定」就不需要了。

用法：
    python tools/check_effect_ref.py            # 离线断言（mock 接口）
    python tools/check_effect_ref.py --show     # 打印每条判定
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import prompts  # noqa: E402
from skillbridge.job import Job as JobModel, new_job_id  # noqa: E402
from skillbridge.providers.base import EditRequest, Provider  # noqa: E402
from skillbridge.runner import JobRunner  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "data" / "test" / "effect-ref"
LIB = ROOT / "library"
ROLES = "REFERENCE ROLES"
FAILURES: list[str] = []


def check(ok: bool, label: str, show: bool) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


class Recorder(Provider):
    def __init__(self, max_images: int = 4) -> None:
        super().__init__("recorder", {"label": "recorder", "type": "mock", "ready": True})
        self.max_images = max_images
        self.requests: list[EditRequest] = []

    @property
    def ready(self) -> bool:
        return True

    def info(self) -> dict:
        return {"name": self.name, "label": "recorder", "ready": True,
                "max_images": self.max_images, "max_concurrency": 0,
                "unlimited": True, "skip_gate": True}

    def setting(self, key: str, default=None):
        return default

    async def open(self) -> None:
        return None

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        import io

        from PIL import Image

        self.requests.append(request)
        image = Image.open(request.images[0]).convert("RGB").resize((160, 160))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()


def prepare(colors: list[str]) -> Path:
    root = WORK / "job"
    shutil.rmtree(root, ignore_errors=True)
    for name in ("inputs/item-01", "work", "staging", "outputs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (720, 720), (238, 238, 236))
    draw = ImageDraw.Draw(img)
    draw.rectangle([180, 120, 540, 600], fill=(190, 40, 45))
    draw.rectangle([280, 280, 440, 430], fill=(24, 24, 28))
    img.save(root / "inputs" / "item-01" / "item-01.png")
    return root


def build_root(wb: Workbench, colors: list[str], *, max_images: int = 4):
    root = prepare(colors)
    garment = wb.skill.type_by_alias("23-sorona-tee")
    picked = [garment.color(c) for c in colors if garment.color(c) is not None]
    # 不传 shape_lock：用默认（=先出基准色，内部行为）
    job = JobModel(
        id=new_job_id(),
        params={"mode": "single_pass", "provider": "recorder",
                "concurrency": len(picked), "subject": "garment",
                "send_reference": True, "input_fidelity": "high"},
    )
    job.root = root
    provider = Recorder(max_images)
    runner = JobRunner(job, wb.skill, garment, picked, provider, data_dir=WORK)
    return job, runner, provider, garment, picked


def main() -> int:
    show = "--show" in sys.argv
    wb = Workbench()

    print("① 款式表效果图已导入")
    styles = [s for s in wb.skill.types.values() if (LIB / "effects" / s.slug).is_dir()]
    check(bool(styles), f"至少一个款式导入过效果图（{len(styles)} 个）", show)
    manifest = LIB / "effects" / "manifest.json"
    check(manifest.is_file(), "有 manifest（记录来源 xlsx 与 sha256）", show)
    garment23 = wb.skill.type_by_alias("23-sorona-tee")
    missing = [c.label for c in garment23.colors if c.effect is None]
    check(not missing, f"23 索罗娜短袖 10 色效果图齐全（缺 {missing}）", show)
    first = garment23.colors[0]
    check(first.effect is not None and first.effect.is_file(), "效果图文件真实存在", show)
    check(first.as_dict()["effect"].startswith("/api/effects/"), "效果图有可访问 URL", show)
    styles_json = json.loads((LIB / "styles.json").read_text(encoding="utf-8"))
    rows = styles_json if isinstance(styles_json, list) else styles_json.get("styles") or []
    with_effect = sum(1 for s in rows for c in s.get("colors") or [] if c.get("effect"))
    total = sum(len(s.get("colors") or []) for s in rows)
    check(with_effect == total, f"款式库 {with_effect}/{total} 色都有效果图", show)

    print("② 提示词：说清两张图的分工")
    prompt = prompts.build_single_pass(wb.skill, garment23, first, subject="garment")
    check(ROLES in prompt, "一次到位提示词含 REFERENCE ROLES", show)
    check("IMAGE 1 is the customer's own photo" in prompt, "写明第 1 张是客户原图", show)
    check("IMAGE 2 is the official product photo" in prompt, "写明第 2 张是官方效果图", show)
    check("silhouette, shoulder drop" in prompt or "shoulder drop" in prompt, "版型以效果图为准", show)
    check("no print, text, logo" in prompt, "禁止把效果图的印花/logo 抄过来", show)
    check("do not replace the customer's scene" in prompt, "禁止把客户场景换成白底", show)
    check("RESHAPE, DO NOT RECOLOUR" in prompt, "明确要求「重塑版型」而不是只换色", show)
    check("shows this style's fit" in prompt, "把该款式的具体版型写进提示词", show)
    check(
        "stays at full width" not in prompt,
        "带效果图时不再写「袖筒到边保持原宽度」（会和紧身款打架）", show,
    )
    noeffect = prompts.build_single_pass(wb.skill, garment23, first, subject="garment", effect=False)
    check(ROLES not in noeffect, "effect=False 时不写角色说明", show)
    recolor = prompts.build_recolor_only(first, garment=garment23, subject="garment")
    check(ROLES in recolor, "换色提示词同样带角色说明", show)

    print("③ 请求：原图 + 效果图一起发")
    job, runner, provider, garment, picked = build_root(wb, ["black"])
    runner.plan()
    task = job.tasks[0]
    check(ROLES in task.prompt, "任务提示词带角色说明", show)
    asyncio.run(runner.run())
    check(len(provider.requests) == 1, "发出 1 次请求", show)
    black = garment.color("black")
    black_effect = black.effect if black is not None else None
    if provider.requests and black_effect is not None:
        images = provider.requests[0].images
        check(len(images) == 2, f"请求带 2 张图（实际 {len(images)}）", show)
        if len(images) == 2:
            check(Path(images[0]).name == "item-01.png", "第 1 张是客户原图", show)
            check(Path(images[1]).name == black_effect.name, "第 2 张是效果图", show)
            check(Path(images[1]).parent.name == "23-sorona-tee", "第 2 张来自 effects/<款式>/", show)

    if provider.requests:
        check(
            provider.requests[0].input_fidelity in (None, ""),
            "带效果图重塑时自动关掉 input_fidelity（否则原图版型会被钉死）", show,
        )

    print("④ 接口只支持单图时：不发效果图，也不再提两张图")
    job2, runner2, provider2, _, _ = build_root(wb, ["black"], max_images=1)
    runner2.plan()
    check(ROLES not in job2.tasks[0].prompt, "单图接口的提示词不带角色说明", show)
    asyncio.run(runner2.run())
    if provider2.requests:
        check(len(provider2.requests[0].images) == 1, "单图接口只发原图", show)

    print("⑤ 没有效果图的颜色：退回颜色参考图")
    job3, runner3, provider3, _, _ = build_root(wb, ["white"], max_images=4)
    runner3.plan()
    task3 = job3.tasks[0]
    check(ROLES in task3.prompt, "带效果图的颜色有角色说明", show)
    task3.prompt = prompts.build_single_pass(
        wb.skill, garment, garment.color("white"), subject="garment", effect=False
    )
    asyncio.run(runner3.run())
    check(ROLES not in job3.tasks[0].prompt, "关闭后提示词不带角色说明", show)

    print("⑥ 界面：版型锁定选项已移除，一致性改为内部行为")
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    check("shapeLock" not in index, "index.html 里没有版型锁定下拉", show)
    check("shapeLock" not in app, "app.js 里不再提交 shape_lock", show)
    check('id="subject"' in index, "画面主体选项还在", show)

    print("⑦ 同款多色：基准色 + 该色效果图（内部固定行为）")
    job4, runner4, provider4, garment4, _ = build_root(wb, ["black", "pink"], max_images=4)
    runner4.plan()
    master4, derived4 = job4.tasks[0], job4.tasks[1]
    check(master4.master and not master4.master_task, "第一个颜色是基准色", show)
    check(derived4.master_task == master4.id, "其余颜色跟随基准", show)
    roles4 = prompts.enforce_subject_rule(prompts.MASTER_EFFECT_CLAUSE, derived4.subject).removeprefix(prompts.NO_PERSON_CLAUSE).strip()
    check("small SWATCH" in derived4.prompt or roles4 in derived4.prompt,
          "跟随任务的提示词含「基准 + 色卡」分工", show)
    check(derived4.subject != "garment" or derived4.prompt.startswith(prompts.NO_PERSON_CLAUSE), "只有衣服时禁人物规则优先于参考图角色", show)
    check("SWATCH of this colourway" in derived4.prompt,
          "写明第 2 张是面料色卡（只定颜色，不带版型）", show)
    asyncio.run(runner4.run())
    check(len(provider4.requests) == 2, "两次请求（基准 + 跟随）", show)
    if len(provider4.requests) == 2:
        images = provider4.requests[1].images
        check(len(images) == 3, f"跟随请求带基准、效果、原图共 3 张（实际 {len(images)}）", show)
        check(
            provider4.requests[1].input_fidelity == "high",
            "跟随基准的任务仍用 input_fidelity=high（保形）", show,
        )
        if len(images) == 3:
            check(Path(images[0]).name == Path(master4.out or "").name, "第 1 张是基准成品", show)
            check(
                "swatches" in str(Path(images[1]).parent),
                "第 2 张是该色面料色卡（派生色不再发整张效果图）", show,
            )
            check(Path(images[2]).read_bytes() == (runner4.root / derived4.source).read_bytes(), "第 3 张是未修改的原始上传画布", show)

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("全部通过：生图带着原图 + 效果图，提示词分工明确")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
