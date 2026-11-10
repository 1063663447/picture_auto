"""Self-check: exercise the whole pipeline against the mock provider.

    python tools/selftest.py

Covers: skill parsing, color reference scanning, single_pass / two_phase delivery
structure, per-task naming, retry of failed tasks, the 2,000,000-byte packaging
rule, and the ZIP layout. No API key and no network needed.
"""

from __future__ import annotations

import asyncio
import io
import json
import random
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from skillbridge.runner import JobRunner  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

SANDBOX = ROOT / "data" / "_selftest"
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' — ' + detail) if detail and not condition else ''}")
    if not condition:
        FAILED.append(label)


def make_photo(path: Path, size: tuple[int, int] = (720, 960), noisy: bool = False) -> None:
    width, height = size
    if noisy:
        rng = random.Random(7)
        image = Image.new("RGB", size)
        pixels = image.load()
        for y in range(0, height, 2):
            for x in range(0, width, 2):
                color = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
                pixels[x, y] = color
                if x + 1 < width:
                    pixels[x + 1, y] = color
                if y + 1 < height:
                    pixels[x, y + 1] = color
                    if x + 1 < width:
                        pixels[x + 1, y + 1] = color
        image.save(path, format="PNG")
        return

    image = Image.new("RGB", size, (232, 228, 220))
    draw = ImageDraw.Draw(image)
    draw.rectangle([width * 0.2, height * 0.45, width * 0.8, height * 0.95], fill=(38, 52, 84))
    draw.rounded_rectangle(
        [width * 0.18, height * 0.2, width * 0.82, height * 0.55], 40, fill=(212, 214, 220)
    )
    draw.ellipse([width * 0.4, height * 0.04, width * 0.6, height * 0.2], fill=(226, 190, 166))
    draw.ellipse([width * 0.34, height * 0.3, width * 0.66, height * 0.42], fill=(20, 20, 24))
    image.save(path, format="PNG")


def make_print_garment(path: Path) -> None:
    """A blue garment with a hard-edged print, for the local-recolor test."""
    width, height = 640, 860
    image = Image.new("RGB", (width, height), (236, 232, 225))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        [width * 0.12, height * 0.16, width * 0.88, height * 0.74],
        40,
        fill=(28, 62, 210),
    )
    draw.ellipse([width * 0.38, height * 0.02, width * 0.62, height * 0.20], fill=(228, 190, 166))
    # artwork: a dark rectangle with a white bar, like a chest print
    draw.rectangle([width * 0.30, height * 0.31, width * 0.70, height * 0.42], fill=(22, 22, 26))
    draw.rectangle([width * 0.34, height * 0.44, width * 0.66, height * 0.47], fill=(250, 250, 250))
    draw.rectangle([width * 0.20, height * 0.80, width * 0.80, height * 0.95], fill=(42, 55, 88))
    image.save(path, format="PNG")


def build_bench(
    name: str, *, fail_rate: float = 0.0, extra_providers: dict | None = None,
    library: bool = False,
) -> Workbench:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    config["data_dir"] = str(SANDBOX / name / "data")
    config["skill_root"] = str(ROOT.parent / "apparel-style-recolor")
    # 结构类断言只看原 skill；款式库单独在第 1.5 组测
    config["library"] = {"enabled": library, "dir": str(ROOT / "library")}
    config["providers"] = {
        "mock": {"type": "mock", "enabled": True, "mock_delay_sec": 0.01,
                 "mock_fail_rate": fail_rate, "concurrency": 8}
    }
    if extra_providers:
        config["providers"].update(extra_providers)
    config_path = SANDBOX / name / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
    return Workbench(config_path)


async def run_job(bench: Workbench, job, *, retry_only: bool = False) -> None:
    garment = bench.skill.type_by_alias(job.params["garment_type"])
    runner = JobRunner(
        job, bench.skill, garment, garment.resolve_colors(job.params["colors"]),
        bench.get_provider(job.params["provider"]), data_dir=bench.data_dir,
    )
    await runner.run(retry_failed_only=retry_only)
    # 9.6.0 起多色批次先出「基准图」，等确认后才按基准改色。
    # 自检要的是「整批能跑完」，所以这里扮演运营点一下确认（与 /approve 等价）。
    if job.status == "review":
        for task in job.tasks:
            if task.master:
                task.approved = True
        job.params["phase"] = "derived"
        job.save()
        runner = JobRunner(
            job, bench.skill, garment, garment.resolve_colors(job.params["colors"]),
            bench.get_provider(job.params["provider"]), data_dir=bench.data_dir,
        )
        await runner.run()


def zip_entries(job) -> list[str]:
    path = job.root / job.zip_rel if job.zip_rel else None
    if not path or not path.is_file():
        return []
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def main() -> int:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)

    print("\n[1] skill 解析")
    bench = build_bench("structure")
    check("三种目标类型", len(bench.skill.types) == 3, str(list(bench.skill.types)))
    check("圆领卫衣 5 色", len(bench.skill.types["crewneck-sweatshirt"].colors) == 5)
    check("连帽卫衣 3 色", len(bench.skill.types["hoodie"].colors) == 3)
    check("短袖 10 色", len(bench.skill.types["short-sleeve"].colors) == 10)
    check("解析到提示词模板", "[TARGET_GARMENT_TYPE]" in bench.skill.prompt_template)
    check("解析到负面提示词", "Do not change the subject" in bench.skill.negative_prompt)
    check("2MB 限制", bench.skill.max_bytes == 2_000_000, str(bench.skill.max_bytes))
    check("解析无警告", not bench.skill.warnings, str(bench.skill.warnings))

    print("\n[1.5] 款式库（菲律宾款式代号表 24 款 + 手工建档 30 扎染T = 25 款 115 色）")
    lib_bench = build_bench("library", library=True)
    lib_types = [s for s in lib_bench.skill.types if s[0].isdigit()]
    check("叠加 25 个款式", len(lib_types) == 25, str(lib_types))
    total_colors = sum(len(lib_bench.skill.types[s].colors) for s in lib_types)
    check("共 115 个颜色", total_colors == 115, str(total_colors))
    missing = [
        f"{slug}/{c.label}"
        for slug in lib_types
        for c in lib_bench.skill.types[slug].colors
        if not c.path.is_file()
    ]
    check("每色都有参考图", not missing, str(missing[:4]))
    missing_effect = [
        f"{slug}/{c.label}"
        for slug in lib_types
        for c in lib_bench.skill.types[slug].colors
        if c.effect is None or not c.effect.is_file()
    ]
    check("每色都有官方效果图", not missing_effect, str(missing_effect[:4]))
    kinds = {lib_bench.skill.types[s].kind for s in lib_types}
    check("品类覆盖上衣/背心", kinds == {"top", "vest"}, str(kinds))
    check(
        "版型短语非空",
        all(lib_bench.skill.types[s].phrase for s in lib_types),
    )
    check(
        "中文别名可选中",
        lib_bench.skill.type_by_alias("23 索罗娜短袖").slug == "23-sorona-tee",
    )

    print("\n[1.6] 文件名编码：款式表代号 + 色号")
    from skillbridge.job import Task  # noqa: E402

    long_sleeve = lib_bench.skill.types["24-long-sleeve"]
    white = long_sleeve.color("white")
    check("24 长袖成人 → 24CXCR", long_sleeve.file_key == "24CXCR", long_sleeve.file_key)
    check("白色 → WH[0]", white is not None and white.file_code == "WH[0]",
          white.file_code if white else "None")
    sample = Task(
        id="item-01__white", item="item-01", view="", color="white", color_hex="#fff",
        color_name="白色", garment="24-long-sleeve", garment_name=long_sleeve.display_name,
    )
    stem = sample.file_stem(style_key=long_sleeve.file_key, color_key=white.file_code,
                            stamp="20261105abcd")
    check("成品名 = 24CXCR_WH[0]_20261105abcd", stem == "24CXCR_WH[0]_20261105abcd", stem)
    check("批次号 = 日期 + job 尾号",
          __import__("skillbridge.job", fromlist=["job_stamp"]).job_stamp("20261105-102305-2219")
          == "202611052219",
          "20261105-102305-2219")

    inputs = SANDBOX / "inputs"
    inputs.mkdir()
    make_photo(inputs / "item-01.png")
    make_photo(inputs / "item-02.png", size=(900, 1200))

    print("\n[2] 一次到位：2 图 × 3 色")
    uploads = [(p.name, p.read_bytes()) for p in sorted(inputs.iterdir())]
    job = bench.create_job(
        uploads, garment_type="圆领卫衣", colors=["black", "white", "apricot"],
        provider="mock", concurrency=3,
    )
    asyncio.run(run_job(bench, job))
    check("任务完成", job.status == "done", job.stage)
    check("6 张全部成功", job.counts()["done"] == 6, str(job.counts()))
    entries = zip_entries(job)
    check("ZIP 6 个文件", len(entries) == 6, str(entries))
    check("ZIP 按款式分层（同款式同文件夹）",
          all(e.startswith("crewneck-sweatshirt-style-recolor-batch/03YLWY/") for e in entries),
          str(entries[:3]))
    # 7.6.3 起文件名 = 款式英文代号_色号编码_批次号（客户要英文编码，能对回款式表）
    import re as _re
    name_re = _re.compile(r"^03YLWY_[A-Z]{1,5}\[\d+\](_[a-z0-9-]+)?_\d{8}[0-9a-z]{4}\.png$")
    check("命名=款式代号_色号[_补充]_批次号",
          all(name_re.match(e.rsplit("/", 1)[-1]) for e in entries), str(entries))
    check("2 张原图时补 item 标签防撞名",
          any("_item-01_" in e for e in entries) and any("_item-02_" in e for e in entries),
          str(entries[:2]))
    check("命名是英文编码（不再是中文）",
          not any(_re.search(r"[\u4e00-\u9fff]", e) for e in entries), str(entries[:2]))
    check("命名里带了批次号（同一天多单不撞名）",
          all(_re.search(r"_\d{8}[0-9a-z]{4}\.png$", e) for e in entries), str(entries[:2]))
    check("没有中间文件进 ZIP",
          not any(("_base" in e or "_clean" in e or "/work/" in e) for e in entries))

    print("\n[2.5] 多视角：同一款同色两张不能互相覆盖")
    views_uploads = [
        ("item-01.jpg", (inputs / "item-01.png").read_bytes()),
        ("item-01_view-02.png", (inputs / "item-02.png").read_bytes()),
    ]
    jobv = bench.create_job(
        views_uploads, garment_type="圆领卫衣", colors=["black"], provider="mock", concurrency=2,
    )
    asyncio.run(run_job(bench, jobv))
    check("多视角完成", jobv.status == "done", jobv.stage)
    check("2 张都出图", jobv.counts()["done"] == 2, str(jobv.counts()))
    outs = sorted(str(t.out) for t in jobv.tasks if t.out)
    check("两张成品文件不同", len(set(outs)) == 2, str(outs))
    check("撞名时补视角标签", any("view-02" in path for path in outs), str(outs))
    check("两张都在同一款式文件夹",
          len({path.rsplit("/", 2)[-2] for path in outs}) == 1, str(outs))
    check("任务 id 也区分视角",
          len({t.id for t in jobv.tasks}) == 2, str([t.id for t in jobv.tasks]))
    check("多视角 ZIP 2 个文件", len(zip_entries(jobv)) == 2, str(zip_entries(jobv)))

    print("\n[3] 两段式：1 图 × 3 色")
    job2 = bench.create_job(
        [("item-01.png", (inputs / "item-01.png").read_bytes())],
        garment_type="连帽卫衣", provider="mock", mode="two_phase", concurrency=2,
    )
    asyncio.run(run_job(bench, job2))
    check("两段式完成", job2.status == "done", job2.stage)
    check("产出 3 张", job2.counts()["done"] == 3, str(job2.counts()))
    check("改款中间图在 work/ 内", (job2.root / "work" / "item-01.png").is_file())
    check("两段式 ZIP 3 个文件", len(zip_entries(job2)) == 3)

    print("\n[3.5] 改图必须用「对话框里选的接口」，不能退回原接口")
    # 造一个第二接口（复制一份 mock，只改名字），验证改图会切过去
    import copy as _copy
    alt = _copy.copy(bench.providers["mock"])
    alt.name = "mock-alt"
    bench.providers["mock-alt"] = alt
    done_ids = [t.id for t in job.tasks if t.status == "done"][:1]
    rework = asyncio.run(
        bench.create_rework_job(job.id, done_ids, "把袖口改窄一点", provider="mock-alt",
                                model="mock-image")
    )
    check("改图 params.provider = 指定接口", rework.params["provider"] == "mock-alt",
          rework.params["provider"])
    check("runner 用的是指定接口", bench._runner_for(rework).provider.name == "mock-alt",
          bench._runner_for(rework).provider.name)
    check("换了接口就记 model_switched", rework.params.get("model_switched") is True,
          str(rework.params.get("model_switched")))
    rework_same = asyncio.run(bench.create_rework_job(job.id, done_ids, "把袖口改窄一点"))
    check("不指定接口时沿用原任务接口",
          rework_same.params["provider"] == job.params["provider"],
          f'{rework_same.params["provider"]} vs {job.params["provider"]}')

    print("\n[4] 失败重试")
    flaky = build_bench("flaky", fail_rate=0.9)
    job3 = flaky.create_job(
        [("item-01.png", (inputs / "item-01.png").read_bytes())],
        garment_type="短袖", colors=["black", "white", "blue"],
        provider="mock", concurrency=2, max_retries=1,
    )
    asyncio.run(run_job(flaky, job3))
    check("出现失败任务", job3.counts()["failed"] > 0, str(job3.counts()))
    check("失败任务带错误信息",
          all(t.error for t in job3.tasks if t.status == "failed"))
    job3.params["provider"] = "mock"
    flaky.providers["mock"].cfg["mock_fail_rate"] = 0.0
    asyncio.run(run_job(flaky, job3, retry_only=True))
    check("重试后全部成功", job3.counts()["failed"] == 0 and job3.counts()["done"] == 3,
          str(job3.counts()))

    print("\n[5] 超过 2MB 自动压缩")
    big = SANDBOX / "big.png"
    make_photo(big, size=(2200, 2200), noisy=True)
    size_bytes = big.stat().st_size
    check("测试图 > 2MB", size_bytes >= 2_000_000, f"{size_bytes} bytes")
    job4 = bench.create_job(
        [("item-01.png", big.read_bytes())], garment_type="短袖",
        colors=["black"], provider="mock", concurrency=1,
    )
    asyncio.run(run_job(bench, job4))
    packaged = list((job4.root / "outputs").rglob("*.png"))
    check("打包后 < 2MB",
          bool(packaged) and packaged[0].stat().st_size < 2_000_000,
          str([p.stat().st_size for p in packaged]))

    print("\n[6] 并发限流")
    conc = build_bench("concurrency")
    conc.providers["mock"].cfg["mock_delay_sec"] = 0.5
    job5 = conc.create_job(
        [("item-01.png", (inputs / "item-01.png").read_bytes())],
        garment_type="短袖",
        colors=["black", "white", "blue", "red", "pink", "gray", "brown", "apricot"],
        provider="mock", concurrency=3,
    )
    started = time.monotonic()
    asyncio.run(run_job(conc, job5))
    elapsed = time.monotonic() - started
    peak = conc.providers["mock"].peak
    check("8 个任务完成", job5.counts()["done"] == 8, str(job5.counts()))
    check("并发峰值不超过设定值", peak <= 3, f"peak={peak}")
    check("并发确实生效（峰值>1）", peak >= 2, f"peak={peak}")
    check("耗时符合 3 并发", elapsed < 4.0, f"{elapsed:.2f}s")

    print("\n[7] 本地换色（图案零失真）")
    local = build_bench("local", extra_providers={"local": {"type": "local_recolor", "enabled": True}})
    garment_img = SANDBOX / "blue_garment.png"
    make_print_garment(garment_img)
    job6 = local.create_job(
        [("item-01.png", garment_img.read_bytes())],
        garment_type="圆领卫衣",
        colors=["white"],
        provider="local",
        mode="recolor_only",
        concurrency=1,
    )
    asyncio.run(run_job(local, job6))
    check("本地换色完成", job6.status == "done", f"{job6.status} {job6.tasks[0].error if job6.tasks else ''}")
    produced = list((job6.root / "outputs").rglob("*.png"))
    check("产出 1 张", len(produced) == 1, str(produced))
    if produced:
        with Image.open(garment_img) as a, Image.open(produced[0]) as b:
            a = a.convert("RGB")
            b = b.convert("RGB")
            same_size = a.size == b.size
            check("尺寸与原图一致", same_size, f"{a.size} vs {b.size}")
            if same_size:
                width, height = a.size
                box = (
                    int(width * 0.32),
                    int(height * 0.32),
                    int(width * 0.68),
                    int(height * 0.41),
                )
                pa = list(a.crop(box).getdata())
                pb = list(b.crop(box).getdata())
                identical = sum(1 for x, y in zip(pa, pb) if x == y)
                ratio = identical / max(1, len(pa))
                check("印花像素基本原样保留", ratio > 0.9, f"{ratio * 100:.1f}% 相同")
                patch = b.crop(
                    (int(width * 0.08), int(height * 0.62), int(width * 0.22), int(height * 0.72))
                )
                mean = sum(sum(p) / 3 for p in patch.getdata()) / max(1, len(list(patch.getdata())))
                check("面料已换成浅色", mean > 150, f"mean={mean:.0f}")

    print("\n[8] harness（对话框 · 流式 · 需求合并）")
    from skillbridge import harness, prompts as prompts_mod

    base = prompts_mod.build_single_pass(bench.skill, bench.skill.types["crewneck-sweatshirt"],
                                         bench.skill.types["crewneck-sweatshirt"].colors[1])
    frames = []
    merged = ""

    async def collect(mode, instruction):
        out = {"text": "", "prompt": "", "events": 0}
        async for event in harness.stream(
            mode=mode, instruction=instruction, base_prompt=base,
            garment="圆领卫衣", color="黑色", count=2, delay=0,
        ):
            out["events"] += 1
            if event["event"] == "delta":
                out["text"] += event["data"]["text"]
            elif event["event"] == "prompt":
                out["prompt"] = event["data"]["prompt"]
                out["requirements"] = event["data"]["requirements"]
        return out

    with_req = asyncio.run(collect("intake", "图案必须原样，背景别动"))
    check("流式输出有多个帧", with_req["events"] >= 4, str(with_req["events"]))
    check("分析文本非空", len(with_req["text"]) > 20)
    check(
        "需求写成权威指令（不是贴末尾）",
        "OPERATOR REQUEST" in with_req["prompt"] and "authoritative" in with_req["prompt"],
        with_req["prompt"][-80:],
    )
    check("需求被归类执行", "KEEP UNCHANGED" in with_req["prompt"])
    check("需求回传到前端", with_req["requirements"].startswith("图案必须原样"))

    # 用户要求「每色一套图案」时必须撤销图案锁（用户 > 默认硬规则）
    variant = asyncio.run(collect("intake", "让生成出来每个颜色对应不同的图案"))
    check("每色一图案：撤销图案锁", "PRINT LOCK" not in variant["prompt"])
    check("每色一图案：写入图案策略", "PATTERN VARIETY" in variant["prompt"])
    check("每色一图案：提示锁已放开", "LIFTED" in variant["prompt"])
    check("每色一图案：提示词真的变了", variant["prompt"] != with_req["prompt"])

    no_req = asyncio.run(collect("intake", ""))
    check("无需求时保持默认提示词", no_req["prompt"] == base)
    check("无需求时 requirements 为空", no_req["requirements"] == "")
    check("无需求时仍保留图案锁", "PRINT LOCK" in no_req["prompt"])

    rework = asyncio.run(collect("rework", "把图案改小 10%"))
    check("返工提示词保留 REVISION REQUEST", "REVISION REQUEST" in rework["prompt"])
    check("返工提示词保留原始画布规则", "ORIGINAL UPLOAD COMPOSITION" in rework["prompt"])
    check("返工提示词含需求", "10%" in rework["prompt"])

    print("\n[9] 输入校验")
    errors = 0
    try:
        bench.create_job([("a.txt", b"x")], garment_type="短袖")
    except Exception:
        errors += 1
    try:
        bench.create_job(uploads, garment_type="短袖", colors=["不存在色"])
    except Exception:
        errors += 1
    try:
        bench.skill.type_by_alias("连衣裙")
    except KeyError:
        errors += 1
    check("非法输入被拦下", errors == 3, f"{errors}/3")

    print()
    if FAILED:
        _say(f"FAILED: {len(FAILED)} 项失败：{FAILED}", "X")
        return 1
    _say("全部通过", "OK")
    return 0


def _say(text, tag=None):
    """控制台可能是 GBK（cmd 默认），emoji 会抛 UnicodeEncodeError 把退出码搞脏。"""
    line = f"[{tag}] {text}" if tag else text
    try:
        print(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(line.encode(enc, "replace").decode(enc, "replace"))


if __name__ == "__main__":
    raise SystemExit(main())
