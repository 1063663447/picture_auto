"""100 并发实测（真接口）：用以往的几个衣服，一次打 100 张，出图保存 + 统计。

流程：
1. 从 `data/jobs/*/inputs/` 里挑 5 张以前用过的真实衣服照片（去重）；
2. 3 个款式 × 各自颜色 = 20 种组合 × 5 张原图 = **100 个任务**；
3. 临时实例（关飞书闸门 / 关飞书记录 / 关通知）+ provider max_concurrency=100，
   一次全推，统计 429、失败、耗时、吞吐；
4. 把 100 张成品复制到 `data/test/a6api-100/images/`，打 ZIP + 拼一张总览图。

用法：
    python tools/check_a6api_100.py            # 真花钱（100 × 单价）
    python tools/check_a6api_100.py --dry      # 只规划任务，不调接口
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import shutil
import statistics
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

OUT = ROOT / "data" / "test" / "a6api-100"
JOBS = ROOT / "data" / "jobs"
PORT = 8786
TOKEN = "test-100"

#: 三款式 × 各自颜色 = 20 种组合（×5 张原图 = 100 张）
PLAN = [
    ("24-long-sleeve", ["white", "black", "royal-blue", "red", "forest-green"]),
    ("03-crewneck-sweatshirt", ["white", "black", "heather-grey", "burgundy", "apricot"]),
    ("23-sorona-tee", ["black", "white", "pink", "apricot", "grey",
                       "red", "heather-grey", "brown", "blue", "forest-green"]),
]


def pick_sources(limit: int = 5) -> list[Path]:
    """以前用过的真实衣服照片（按体积/尺寸筛掉占位图，去重）。

    占位图（mock 出的纯色块）颜色数极少，用「量化后颜色种类」把它们过滤掉。
    """
    seen: dict[str, Path] = {}
    rows: list[tuple[int, Path]] = []
    for job in sorted(JOBS.glob("*/job.json")):
        inputs = job.parent / "inputs"
        if not inputs.is_dir():
            continue
        for src in inputs.glob("*/*"):
            if src.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            try:
                digest = hashlib.sha1(src.read_bytes()).hexdigest()[:10]
                with Image.open(src) as image:
                    width, height = image.size
                    if min(width, height) < 300:
                        continue
                    small = image.convert("RGB").resize((96, 96))
                    colors = len(set(small.getdata()))
            except Exception:  # noqa: BLE001
                continue
            if digest in seen or colors < 900:  # 纯色/近纯色占位图直接跳过
                continue
            seen[digest] = src
            rows.append((width * height, src))
    rows.sort(reverse=True)
    picked, used = [], set()
    for _area, src in rows:
        # 同一个 job 目录只取一张，尽量覆盖不同来源
        if src.parent.parent.parent.name in used:
            continue
        used.add(src.parent.parent.parent.name)
        picked.append(src)
        if len(picked) >= limit:
            break
    return picked


def all_pairs() -> list[tuple[str, str]]:
    """库里所有款式 × 各自颜色（49 组）。"""
    from skillbridge.service import Workbench

    wb = Workbench(ROOT / "config.json")
    pairs: list[tuple[str, str]] = []
    for slug in sorted(wb.skill.types):
        garment = wb.skill.types[slug]
        if not slug[0].isdigit():      # 只用款式表里的款式（隐藏的原生 3 款不参与）
            continue
        for ref in garment.colors:
            pairs.append((slug, ref.label))
    return pairs


def write_config(sources: list[Path]) -> Path:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    cfg["data_dir"] = "data-test-100"
    cfg["server"]["port"] = PORT
    cfg["server"]["auth_token"] = TOKEN
    cfg["feishu"] = {**(cfg.get("feishu") or {}), "enabled": False}
    cfg["defaults"]["gate"] = {**(cfg["defaults"].get("gate") or {}), "enabled": False}
    cfg["defaults"]["notify"] = {**(cfg["defaults"].get("notify") or {}), "enabled": False}
    prov = cfg["providers"]["a6api"]
    prov["max_concurrency"] = 100
    prov["global_concurrency"] = 100
    path = ROOT / "config.test-100.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def peak_concurrency(tasks: list[dict]) -> int:
    events = []
    for task in tasks:
        started = task.get("started_at")
        finished = task.get("finished_at")
        if started and finished:
            events.append((started, 1))
            events.append((finished, -1))
    events.sort()
    current = peak = 0
    for _ts, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


async def run_job(sources: list[Path], cfg_path: Path) -> dict:
    import httpx

    base = f"http://127.0.0.1:{PORT}"
    files = []
    for index, src in enumerate(sources, start=1):
        files.append(("files", (f"item-{index:02d}{src.suffix.lower()}", src.read_bytes(),
                                "image/png" if src.suffix.lower() == ".png" else "image/jpeg")))
    payload = [
        ("provider", (None, "a6api")),
        ("model", (None, "gpt-image-2.5-sunburst")),
        ("start", (None, "true")),
        ("concurrency", (None, "auto")),
        ("mode", (None, "single_pass")),
        ("quality", (None, "high")),
        ("selections", (None, json.dumps(
            [{"garment_type": slug, "colors": colors} for slug, colors in PLAN],
            ensure_ascii=False,
        ))),
    ]
    async with httpx.AsyncClient(base_url=base, timeout=300) as client:
        response = await client.post(f"/api/jobs?token={TOKEN}", files=payload + files)
        if response.status_code != 200:
            raise RuntimeError(f"建任务失败 {response.status_code}: {response.text[:300]}")
        job = response.json()["job"]
        job_id = job["id"]
        print(f"[*] 任务 {job_id}：{len(job['tasks'])} 张，"
              f"并发 {job['params'].get('concurrency')}，"
              f"预估 {(job['params'].get('estimate') or {}).get('total')}")
        started = time.time()
        last = ""
        while True:
            await asyncio.sleep(5)
            payload_job = (await client.get(f"/api/jobs/{job_id}?token={TOKEN}")).json()["job"]
            stats = payload_job["stats"]
            line = (f"  {round(time.time()-started):>3}s 完成 {stats['done']}/{stats['total']} "
                    f"排队 {stats.get('queued',0)} 生成中 {stats['running']} "
                    f"失败 {stats['failed']}")
            if line != last:
                print(line)
                last = line
            if payload_job["status"] in {"done", "partial", "failed", "canceled"}:
                return {"job": payload_job, "wall": round(time.time() - started, 1)}
            if time.time() - started > 1800:
                raise TimeoutError("30 分钟还没跑完")


def collect(job: dict) -> dict:
    """把成品复制出来、打 ZIP、拼总览图。"""
    root = Path(job["_root"])
    images_dir = OUT / "images"
    if images_dir.exists():
        shutil.rmtree(images_dir)
    images_dir.mkdir(parents=True)
    copied = []
    for task in job["tasks"]:
        out = task.get("out")
        if task.get("status") != "done" or not out:
            continue
        src = root / out
        if not src.is_file():
            continue
        name = f"{task['color']}__{src.name}" if False else src.name
        target = images_dir / name
        if target.exists():  # 同款同色撞名（多原图）时带上 item
            target = images_dir / f"{task['item']}__{src.name}"
        shutil.copyfile(src, target)
        copied.append(target)

    zip_path = OUT / "a6api-100.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for target in sorted(copied):
            archive.write(target, Path("images") / target.name)

    # 总览图：最多 100 张缩略图
    tiles = []
    for target in sorted(copied)[:100]:
        with Image.open(target) as image:
            thumb = image.convert("RGB")
            thumb.thumbnail((210, 280))
            tiles.append((target.name, thumb.copy()))
    cols = 10
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 220, rows * 300), (16, 16, 20))
    from PIL import ImageDraw

    draw = ImageDraw.Draw(sheet)
    for index, (name, thumb) in enumerate(tiles):
        x = (index % cols) * 220
        y = (index // cols) * 300
        sheet.paste(thumb, (x + 5, y + 18))
        draw.text((x + 6, y + 4), name[-22:], fill=(255, 214, 120))
    sheet_path = OUT / "contact-sheet.png"
    sheet.save(sheet_path)
    return {"copied": len(copied), "zip": str(zip_path), "sheet": str(sheet_path)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="备用接口 100 并发实测")
    parser.add_argument("--dry", action="store_true", help="只规划，不调接口")
    parser.add_argument("--sources", type=int, default=5)
    parser.add_argument("--images", default="", help="指定原图，逗号分隔（相对 workbench 目录或绝对路径）")
    parser.add_argument("--tasks", type=int, default=0, help="目标任务数（默认 = 全部组合）")
    args = parser.parse_args(argv)

    if args.images:
        sources = []
        for raw in args.images.split(","):
            raw = raw.strip()
            if not raw:
                continue
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = ROOT / raw
            if not candidate.is_file():
                print(f"[x] 找不到原图：{candidate}")
                return 2
            sources.append(candidate)
    else:
        sources = pick_sources(args.sources)
    if not sources:
        print("[x] data/jobs 里找不到可用的原图")
        return 2
    total = sum(len(colors) for _slug, colors in PLAN) * len(sources)
    print(f"[*] 原图 {len(sources)} 张 → 每种款式颜色组合 "
          f"{sum(len(c) for _s, c in PLAN)} 个 → 共 {total} 个任务")
    for src in sources:
        print("   ", src.relative_to(ROOT))
    if args.dry:
        return 0
    if total > 100:
        print("[!] 任务数超过 100，请调小 --sources")
        return 2

    cfg_path = write_config(sources)
    server = subprocess.Popen(
        [sys.executable, "serve.py", "--config", cfg_path.name],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(7)
        if server.poll() is not None:
            raise RuntimeError(f"临时实例没起来（exit={server.returncode}），多半是端口 {PORT} 被占用")
        result = asyncio.run(run_job(sources, cfg_path))
        job = result["job"]
        job["_root"] = str(ROOT / "data-test-100" / "jobs" / job["id"])
        tasks = job["tasks"]
        done = [t for t in tasks if t["status"] == "done"]
        failed = [t for t in tasks if t["status"] != "done"]
        secs = sorted(
            (t["finished_at"] - t["started_at"]) for t in done
            if t.get("started_at") and t.get("finished_at")
        )
        summary = {
            "job": job["id"],
            "tasks": len(tasks),
            "done": len(done),
            "failed": len(failed),
            "rate_limited": sum(1 for t in tasks if t.get("error_kind") == "rate_limit"),
            "peak_concurrency": peak_concurrency(tasks),
            "wall_sec": result["wall"],
            "per_min": round(len(done) / max(1, result["wall"]) * 60, 1),
            "latency_p50": round(statistics.median(secs), 1) if secs else None,
            "latency_p95": round(secs[max(0, int(len(secs) * 0.95) - 1)], 1) if secs else None,
            "latency_max": round(secs[-1], 1) if secs else None,
            "estimate_unit": (job["params"].get("estimate") or {}).get("unit"),
            "estimate_total": (job["params"].get("estimate") or {}).get("total"),
            "provider": job["params"].get("provider"),
            "model": job["params"].get("model"),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sources": [str(src.relative_to(ROOT)) for src in sources],
            "errors": [
                {"task": t["id"], "kind": t.get("error_kind"), "error": (t.get("error") or "")[:180]}
                for t in failed[:20]
            ],
        }
        files = collect(job)
        summary.update(files)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "report.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print("\n========== 结果 ==========")
        for key in ("tasks", "done", "failed", "rate_limited", "peak_concurrency",
                    "wall_sec", "per_min", "latency_p50", "latency_p95", "latency_max",
                    "estimate_unit", "estimate_total"):
            print(f"  {key:<18} {summary.get(key)}")
        print(f"  图片              {files['copied']} 张 → {files['zip']}")
        print(f"  总览图            {files['sheet']}")
        print(f"  报告              {OUT / 'report.json'}")
        if failed:
            print("\n  失败样例：")
            for row in summary["errors"][:6]:
                print("   ", row["task"], row["kind"], row["error"][:110])
        return 0 if not failed else 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
