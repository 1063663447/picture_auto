"""验证「主体约束」这一层：原图是纯衣服时，提示词必须禁止模型添加人物。

背景：客户原图是平铺的单件衣服（没有真人），提示词里却写着
"Keep the exact same person, face, hair, skin, hands …"，模型照着补了一个女生穿着卖。
实测同一批里 3 张平铺只有 1 张冒出人，就是因为那句话给了模型「可以有个人」的许可。

本脚本把规则钉成可回归的断言：改提示词/改 skill 之后跑一遍就知道有没有退化。

用法：
    python tools/check_no_person.py            # 纯离线断言
    python tools/check_no_person.py --show     # 额外打印每条的判定结果
    python tools/check_no_person.py --live     # 再用 a6api 真跑 1 张，验证成品没人物
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from skillbridge import prompts, refine, subject  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

FAILURES: list[str] = []
WORK_DIR = Path(__file__).resolve().parent.parent / "data" / "test" / "no-person"

GARMENT_MARK = "NO PERSON"
HARD_MARK = "no person, no human"          # 否定词那一组
PERSON_MARK = "Keep the exact same person"
NEUTRAL_MARK = "SUBJECT — the source image alone decides"


def check(ok: bool, label: str, show: bool) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


# --------------------------------------------------------------------- 造图 --
def make_garment_only(path: Path, *, garment=(190, 40, 45)) -> Path:
    """白底平铺：一块矩形衣服 + 印花方框，没有任何皮肤色。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (720, 720), (238, 238, 236))
    draw = ImageDraw.Draw(image)
    draw.rectangle([180, 120, 540, 600], fill=garment)
    draw.ellipse([300, 90, 420, 190], fill=garment)
    draw.rectangle([280, 280, 440, 430], fill=(24, 24, 28))
    image.save(path)
    return path


def make_with_person(path: Path) -> Path:
    """同样一件衣服，但上半部分画一大块皮肤色（模拟模特脸+脖子+手臂）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    garment_only = make_garment_only(WORK_DIR / "_tmp_garment.png")
    image = Image.open(garment_only).convert("RGB")
    draw = ImageDraw.Draw(image)
    skin = (222, 168, 138)
    draw.ellipse([280, 30, 440, 200], fill=skin)      # 脸
    draw.rectangle([330, 180, 390, 250], fill=skin)    # 脖子
    draw.rectangle([150, 250, 230, 420], fill=skin)    # 手臂
    image.save(path)
    return path


def make_person_output(source: Path, path: Path, *, with_person: bool) -> Path:
    """成品：with_person=True 时在原背景区域画一大块皮肤色（= 又画了个人）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(source).convert("RGB")
    if with_person:
        draw = ImageDraw.Draw(image)
        skin = (226, 172, 142)
        draw.ellipse([60, 40, 300, 690], fill=skin)     # 左半张的「人」
        draw.ellipse([470, 60, 700, 660], fill=skin)    # 右半张的「人」
    else:
        # 只把衣服改个颜色，背景不动
        draw = ImageDraw.Draw(image)
        draw.rectangle([180, 120, 540, 600], fill=(28, 44, 120))
        draw.ellipse([300, 90, 420, 190], fill=(28, 44, 120))
    image.save(path)
    return path


# ------------------------------------------------------------------- 断言 ----
def main() -> int:
    show = "--show" in sys.argv
    live = "--live" in sys.argv
    wb = Workbench()
    skill = wb.skill
    garment = skill.type_by_alias("23-sorona-tee")
    color = garment.colors[0]

    print("① 判定：纯衣服 vs 真人")
    plain = make_garment_only(WORK_DIR / "garment-only.png")
    worn = make_with_person(WORK_DIR / "with-person.png")
    plain_stats = subject.measure(plain)
    worn_stats = subject.measure(worn)
    check(subject.detect(plain) == subject.AUTO, "无露肤图按原图保留，不据肤色断定无人", show)
    check(subject.detect(worn) == subject.PERSON, "有皮肤的模特图判为 person", show)
    check(plain_stats["skin"] <= subject.SKIN_ABSENT, "纯衣服图皮肤占比≈0", show)
    check(subject.detect(plain, "person") == subject.PERSON, "显式指定 person 时不被自动判定覆盖", show)
    check(subject.detect(worn, "garment") == subject.GARMENT, "显式指定 garment 时不被自动判定覆盖", show)

    print("② 提示词：garment 模式必须禁人")
    prompt_g = prompts.build_single_pass(skill, garment, color, subject="garment")
    negative_g = prompts.full_negative(skill, "preserve", garment, "garment")
    check(GARMENT_MARK in prompt_g, "一次到位提示词含 NO PERSON 段", show)
    check("never a neck or a chest" in prompt_g, "提示词写明领口不能露脖子/胸口", show)
    check(HARD_MARK in negative_g.lower(), "否定词含 no person / no human", show)
    check("no one wearing the garment" in negative_g.lower(), "否定词含 no one wearing the garment", show)

    print("③ 提示词：person / auto 不得禁人")
    prompt_p = prompts.build_single_pass(skill, garment, color, subject="person")
    negative_p = prompts.full_negative(skill, "preserve", garment, "person")
    check(PERSON_MARK in prompt_p, "person 模式保留「同一个人」约束", show)
    check(GARMENT_MARK not in prompt_p, "person 模式不含禁人段", show)
    check("no one wearing the garment" not in negative_p.lower(), "person 模式不加禁人否定词", show)
    prompt_auto = prompts.build_single_pass(skill, garment, color)
    check(NEUTRAL_MARK in prompt_auto, "自动模式用中性主体句", show)
    check(PERSON_MARK not in prompt_auto, "自动模式不再写死「同一个人」", show)

    print("④ 换色/改款/改图三条路径同样跟着主体走")
    recolor_g = prompts.build_recolor_only(color, garment=garment, subject="garment")
    style_g = prompts.build_style_only(garment, subject="garment")
    rework_g = refine.compose_template("把图案换成猪猪侠", "original", subject="garment")
    check(GARMENT_MARK in recolor_g, "换色提示词含禁人段", show)
    check(GARMENT_MARK in style_g, "改款提示词含禁人段", show)
    check(GARMENT_MARK in rework_g, "改图提示词含禁人段", show)
    rework_p = refine.compose_template("把图案换成猪猪侠", "original", subject="person")
    check(GARMENT_MARK not in rework_p, "真人改图不加禁人段", show)

    print("⑤ skill 原文不再写死「同一个人」")
    skill_text = (Path(wb.skill.root) / "SKILL.md").read_text(encoding="utf-8")
    check("Keep the exact same person" not in skill_text, "SKILL.md 已改成中性主体表述", show)
    check("stays empty" in skill_text or "empty and unoccupied" in skill_text,
          "SKILL.md 明确平铺图保持空衣服", show)

    print("⑥ 成品复查：原背景区域冒出皮肤色 → 报警")
    ok_out = make_person_output(plain, WORK_DIR / "out-clean.png", with_person=False)
    bad_out = make_person_output(plain, WORK_DIR / "out-person.png", with_person=True)
    check(not subject.person_alert(plain, ok_out)["alert"], "正常换色成品不报警", show)
    check(subject.person_alert(plain, bad_out)["alert"], "画了人的成品会报警", show)
    check(not subject.person_alert(worn, bad_out)["alert"], "真人图不参与该复查", show)
    buf = io.BytesIO()
    Image.open(bad_out).save(buf, format="PNG")
    check(subject.person_alert(plain, buf.getvalue())["alert"], "bytes 输入也能复查", show)

    print("⑦ 行程：plan() 会把主体写进任务")
    tasks_dir = WORK_DIR / "plan"
    if tasks_dir.is_dir():
        import shutil

        shutil.rmtree(tasks_dir, ignore_errors=True)
    (tasks_dir / "inputs" / "item-01").mkdir(parents=True, exist_ok=True)
    (tasks_dir / "jobs").mkdir(parents=True, exist_ok=True)
    (tasks_dir / "inputs" / "item-01" / "item-01.png").write_bytes(plain.read_bytes())
    from skillbridge.job import Job as JobModel, new_job_id
    from skillbridge.providers.mock import MockProvider
    from skillbridge.runner import JobRunner

    job = JobModel(
        id=new_job_id(),
        params={"mode": "single_pass", "provider": "mock", "concurrency": 1, "subject": "auto"},
    )
    job.root = tasks_dir
    runner = JobRunner(
        job,
        skill,
        garment,
        [color],
        MockProvider("mock", {}),
        data_dir=WORK_DIR,
    )
    runner.plan()
    first = job.tasks[0]
    check(first.subject == "auto", "任务记下按原图保留的 auto 决策", show)
    check(NEUTRAL_MARK in first.prompt and GARMENT_MARK not in first.prompt, "无露肤原图不套强制脱人段", show)
    check("Do not add a person" in runner.negative_for(garment, first.subject), "原图无人仍禁止新增人物", show)
    check(job.params.get("subjects", {}).get("auto") == 1, "params.subjects 统计按原图保留张数", show)

    if live:
        print("⑧ 真跑一张（a6api）：成品里不应出现人物")
        source = plain
        for arg in sys.argv:
            if arg.startswith("--source="):
                source = Path(arg.split("=", 1)[1]).resolve()
                print(f"  用真实平铺原图：{source}")
        failures = run_live(wb, skill, source, show)
        if failures:
            FAILURES.extend(failures)

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("全部通过：纯衣服原图不会再被生成出模特")
    return 0


def run_live(wb: Workbench, skill, source: Path, show: bool) -> list[str]:
    """用真实接口跑一张，验证成品没有人物（需要 a6api 的 key 与额度）。"""
    import asyncio
    import shutil

    from skillbridge.providers import build_providers
    from skillbridge.providers.base import EditRequest

    garment = skill.type_by_alias("23-sorona-tee")
    color = garment.color("red") or garment.colors[0]
    providers = build_providers(wb.config, wb.env)
    provider = providers.get("a6api")
    if provider is None:
        print("  [skip] 没有配置 a6api 接口")
        return []
    request = EditRequest(
        images=[source],
        prompt=prompts.build_single_pass(skill, garment, color, subject="garment"),
        negative=prompts.full_negative(skill, "preserve", garment, "garment"),
        size="1024x1024",
        quality="high",
        model=str(provider.cfg.get("model") or ""),
    )

    async def go() -> bytes:
        await provider.open()
        try:
            return await provider.edit(request, 180.0)
        finally:
            await provider.close()

    data = asyncio.run(go())
    out = WORK_DIR / "live-a6api.png"
    out.write_bytes(data)
    alert = subject.person_alert(source, data)
    stats = subject.measure(out)
    print(f"  成品：{out}（皮肤占比 {stats['skin']:.2%}，背景区新增皮肤 {alert['skin']:.2%}）")
    failures: list[str] = []
    if alert["alert"]:
        failures.append("真跑成品里出现了人物")
    if not show and not failures:
        print("  [ok] 成品干净")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
