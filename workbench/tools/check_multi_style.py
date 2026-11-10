"""多款式（一次出多个款式 × 各自全色）回归测试。

场景：1 张原图 → 同时出「06 连帽卫衣 3 色」+「23 索罗娜短袖 2 色」= 5 个任务。
验证：
  * 任务规划正确（数量、id 不撞号、每个任务带自己的款式）
  * staging 目录按「item__款式」分桶，同名颜色不会互相覆盖
  * 打包成功，ZIP 里 2 个款式目录、5 张图
  * 老的单款式路径（不传 selections）行为不变
运行：python tools/check_multi_style.py
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from skillbridge.runner import JobRunner  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = Path(tempfile.mkdtemp(prefix="wb-multi-"))
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILED.append(label)


def make_png(path: Path) -> None:
    width, height = 640, 860
    image = Image.new("RGB", (width, height), (236, 232, 225))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([width * 0.12, height * 0.16, width * 0.88, height * 0.74], 40, fill=(28, 62, 210))
    draw.ellipse([width * 0.38, height * 0.02, width * 0.62, height * 0.20], fill=(228, 190, 166))
    draw.rectangle([width * 0.30, height * 0.31, width * 0.70, height * 0.42], fill=(22, 22, 26))
    image.save(path, format="PNG")


def build_bench() -> Workbench:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    config["data_dir"] = str(SANDBOX / "data")
    config["skill_root"] = str(ROOT.parent / "apparel-style-recolor")
    config["library"] = {"enabled": True, "dir": str(ROOT / "library"), "hide_original_types": True}
    config["providers"] = {
        "mock": {"type": "mock", "enabled": True, "mock_delay_sec": 0.01, "concurrency": 8}
    }
    config["feishu"] = {"enabled": False}
    path = SANDBOX / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
    return Workbench(path)


def _runner(bench: Workbench, job) -> JobRunner:
    selections = []
    for raw in job.params.get("selections") or []:
        garment = bench.skill.type_by_alias(raw["garment_type"])
        colors = garment.resolve_colors(raw["colors"])
        if colors:
            selections.append((garment, colors))
    if selections:
        garment, colors = selections[0]
    else:
        garment = bench.skill.type_by_alias(job.params["garment_type"])
        colors = garment.resolve_colors(job.params["colors"])
    return JobRunner(
        job, bench.skill, garment, colors,
        bench.get_provider(job.params["provider"]),
        selections=selections or None,
        data_dir=bench.data_dir,
    )


async def run_job(bench: Workbench, job) -> None:
    await _runner(bench, job).run()
    # 9.6.0 起多色批次先出基准图，确认后才按基准改色；自检要整批跑完，这里扮演运营确认
    if job.status == "review":
        for task in job.tasks:
            if task.master:
                task.approved = True
        job.params["phase"] = "derived"
        job.save()
        await _runner(bench, job).run()


def zip_entries(job) -> list[str]:
    path = (job.root / job.zip_rel) if job.zip_rel else None
    if not path or not path.is_file():
        return []
    with zipfile.ZipFile(path) as archive:
        return sorted(n for n in archive.namelist() if not n.endswith("/"))


def main() -> int:
    uploads = [("item-01.png", (lambda: (make_png(SANDBOX / "src.png"),
                                        (SANDBOX / "src.png").read_bytes())[1])())]
    bench = build_bench()

    print("[1] 多款式：06 连帽卫衣 3 色 + 23 索罗娜短袖 2 色")
    hoodie = bench.skill.type_by_alias("06-hoodie")
    tee = bench.skill.type_by_alias("23-sorona-tee")
    hoodie_colors = ["white", "black", "heather-grey"]
    hoodie_labels = {c.label for c in hoodie.colors}
    # 只挑连帽卫衣里没有的颜色 —— 老代码会在这里报「颜色参考缺失」（或悄悄用错参考图）
    tee_colors = [c.label for c in tee.colors if c.label not in hoodie_labels][:2]
    assert len(tee_colors) == 2, [c.label for c in tee.colors]
    job = asyncio.run(_create(bench, uploads, [
        {"garment_type": "06-hoodie", "colors": hoodie_colors},
        {"garment_type": "23-sorona-tee", "colors": tee_colors},
    ]))
    check("款式数 = 2", job.params.get("style_count") == 2, str(job.params.get("style_count")))
    check("颜色总数 = 5", job.params.get("total_colors") == 5, str(job.params.get("total_colors")))
    check("任务数 = 5", len(job.tasks) == 5, str(len(job.tasks)))
    check("任务 id 唯一", len({t.id for t in job.tasks}) == 5, ",".join(t.id for t in job.tasks))
    garments = {t.garment for t in job.tasks}
    check("任务各自带款式", garments == {"06-hoodie", "23-sorona-tee"}, ",".join(sorted(garments)))
    check("批次名按款式数命名", job.batch_name.endswith("2styles-recolor-batch"), job.batch_name)

    print("[1.1] 颜色参考必须来自任务自己的款式")
    runner = _runner(bench, job)
    all_ok = True
    for task in job.tasks:
        garment = runner.garment_for_task(task)
        color = garment.color(task.color)
        if garment.slug != task.garment or color is None or not color.path.is_file():
            all_ok = False
            print(f"    ✗ {task.id} → {garment.slug} / {task.color} / "
                  f"{(color.path if color else 'None')}")
    check("每个任务都能拿到本款式的参考图", all_ok)
    check("两款颜色确实不相交", not (set(tee_colors) & hoodie_labels), ",".join(tee_colors))

    asyncio.run(run_job(bench, job))
    check("任务完成", job.status in {"done", "partial"}, job.status)
    check("5 张全部成功", job.counts()["done"] == 5, json.dumps(job.counts(), ensure_ascii=False))
    staging_dirs = sorted(p.name for p in (job.root / "staging").iterdir() if p.is_dir())
    check("staging 按款式分目录（英文代号）", staging_dirs == ["06LMWY", "23SLNDX"],
          ",".join(staging_dirs))
    entries = zip_entries(job)
    check("ZIP 里 5 张", len(entries) == 5, str(len(entries)))
    check("ZIP 里两个款式目录",
          all(any(f"/{key}/" in e for e in entries) for key in ("06LMWY", "23SLNDX")),
          "; ".join(entries[:2]))
    check("ZIP 没有串味（卫衣目录里没有短袖的颜色）",
          not any("/06LMWY/" in e and any(c in e for c in tee_colors if c not in hoodie_colors)
                  for e in entries))
    check("打包没报错", not job.error, str(job.error)[:80])

    print("[2] 老路径：不传 selections（单款式 3 色）")
    job2 = asyncio.run(_create(
        bench, uploads, None, garment_type="06-hoodie", colors=hoodie_colors
    ))
    check("任务数 = 3", len(job2.tasks) == 3, str(len(job2.tasks)))
    asyncio.run(run_job(bench, job2))
    dirs2 = sorted(p.name for p in (job2.root / "staging").iterdir() if p.is_dir())
    check("单款式也用款式代号命名目录", dirs2 == ["06LMWY"], ",".join(dirs2))
    check("ZIP 里 3 张", len(zip_entries(job2)) == 3, str(len(zip_entries(job2))))

    print("[3] 同款式加两次 → 合并颜色不重复排任务")
    job3 = asyncio.run(_create(bench, uploads, [
        {"garment_type": "06-hoodie", "colors": hoodie_colors[:2]},
        {"garment_type": "06-hoodie", "colors": [hoodie_colors[-1]]},
    ]))
    check("合并成一个款式", job3.params.get("style_count") == 1, str(job3.params.get("style_count")))
    check("颜色合并成 3", job3.params.get("total_colors") == 3, str(job3.params.get("total_colors")))
    check("任务数 = 3", len(job3.tasks) == 3, str(len(job3.tasks)))

    print("[4] 选中导出（批量导出）也要按款式分文件夹")
    export_path = bench.export_zip(job.id, [])
    with zipfile.ZipFile(export_path) as archive:
        exported = [n for n in archive.namelist() if not n.endswith("/")]
    check("导出的 ZIP 按款式分文件夹",
          all(("/06LMWY/" in n) or ("/23SLNDX/" in n) for n in exported), str(exported))
    check("导出也不再平铺在根目录",
          not any(n.count("/") < 2 for n in exported), str(exported[:2]))
    check("导出包含两个款式文件夹",
          bool({n.split("/")[1] for n in exported}) and len({n.split("/")[1] for n in exported}) == 2,
          str(sorted({n.split("/")[1] for n in exported})))

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项失败：{FAILED}")
        return 1
    print("[OK] 全部通过")
    return 0


async def _create(bench: Workbench, uploads, selections, *, garment_type=None, colors=None):
    return await asyncio.to_thread(
        bench.create_job,
        uploads,
        garment_type=garment_type,
        colors=colors,
        selections=selections,
        provider="mock",
        model="mock-image",
        size="auto",
        quality="low",
        output_format="png",
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(SANDBOX, ignore_errors=True)
