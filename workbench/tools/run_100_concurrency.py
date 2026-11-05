"""100 并发实测：2 张图 × 49 组款式/颜色 + 第 3 张图 × 2 组 = 正好 100 个任务（真 100 并发）。

为什么拆成两个任务：workbench 里「选中的款式×颜色」对每张原图全量生效，
所以 2 张图只能出 98 个任务；补 2 个任务到第 3 张旧图上，两个任务同时开跑，
靠 provider 的 global_concurrency=100 合起来就是 100 并发。

产物：data/test/a6api-100-v2/{images, by-style, a6api-100.zip, contact-sheet.png, report.json}
"""
from __future__ import annotations

import asyncio
import json
import shutil
import statistics
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

OUT = ROOT / "data" / "test" / "a6api-100-v2"
PORT = 8785
TOKEN = "test-100v2"
NEW_IMAGES = [
    ROOT / "data" / "test" / "inputs-new" / "new-01-white-long-sleeve.png",
    ROOT / "data" / "test" / "inputs-new" / "new-02-black-roblox.png",
]
EXTRA_IMAGE = ROOT / "data" / "test" / "christmas-tee.jpg"
EXTRA_PAIRS = [("24-long-sleeve", ["white"]), ("23-sorona-tee", ["black"])]


def library_pairs() -> list[dict]:
    from skillbridge.service import Workbench

    wb = Workbench(ROOT / "config.json")
    selections = []
    for slug in sorted(wb.skill.types):
        if not slug[0].isdigit():
            continue
        garment = wb.skill.types[slug]
        selections.append({"garment_type": slug, "colors": [c.label for c in garment.colors]})
    return selections


def write_config() -> Path:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    cfg["data_dir"] = "data-test-100v2"
    cfg["server"]["port"] = PORT
    cfg["server"]["auth_token"] = TOKEN
    cfg["feishu"] = {**(cfg.get("feishu") or {}), "enabled": False}
    cfg["defaults"]["gate"] = {**(cfg["defaults"].get("gate") or {}), "enabled": False}
    cfg["defaults"]["notify"] = {**(cfg["defaults"].get("notify") or {}), "enabled": False}
    prov = cfg["providers"]["a6api"]
    prov["max_concurrency"] = 100
    prov["global_concurrency"] = 100
    path = ROOT / "config.test-100v2.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def create_job(client: httpx.AsyncClient, images: list[Path], selections: list[dict]) -> str:
    payload = [
        ("provider", (None, "a6api")),
        ("model", (None, "gpt-image-2.5-sunburst")),
        ("start", (None, "true")),
        ("concurrency", (None, "auto")),
        ("mode", (None, "single_pass")),
        ("quality", (None, "high")),
        ("selections", (None, json.dumps(selections, ensure_ascii=False))),
    ]
    for index, src in enumerate(images, start=1):
        mime = "image/png" if src.suffix.lower() == ".png" else "image/jpeg"
        payload.append(("files", (f"item-{index:02d}{src.suffix.lower()}", src.read_bytes(), mime)))
    response = await client.post(f"/api/jobs?token={TOKEN}", files=payload)
    if response.status_code != 200:
        raise RuntimeError(f"建任务失败 {response.status_code}: {response.text[:300]}")
    job = response.json()["job"]
    print(f"[*] {job['id']} · {len(job['tasks'])} 张 · 并发 {job['params'].get('concurrency')} · "
          f"预估 {(job['params'].get('estimate') or {}).get('total')}")
    return job["id"]


async def wait_jobs(client: httpx.AsyncClient, ids: list[str]) -> dict[str, dict]:
    started = time.time()
    last = ""
    while True:
        await asyncio.sleep(5)
        jobs = {}
        for job_id in ids:
            jobs[job_id] = (await client.get(f"/api/jobs/{job_id}?token={TOKEN}")).json()["job"]
        done = sum(j["stats"]["done"] for j in jobs.values())
        total = sum(j["stats"]["total"] for j in jobs.values())
        failed = sum(j["stats"]["failed"] for j in jobs.values())
        running = sum(j["stats"]["running"] for j in jobs.values())
        line = f"  {round(time.time()-started):>3}s 完成 {done}/{total} 生成中 {running} 失败 {failed}"
        if line != last:
            print(line)
            last = line
        if all(j["status"] in {"done", "partial", "failed", "canceled"} for j in jobs.values()):
            return jobs, round(time.time() - started, 1)
        if time.time() - started > 1800:
            raise TimeoutError("30 分钟还没跑完")


def peak(tasks: list[dict]) -> int:
    events = []
    for task in tasks:
        if task.get("started_at") and task.get("finished_at"):
            events.append((task["started_at"], 1))
            events.append((task["finished_at"], -1))
    events.sort()
    current = best = 0
    for _ts, delta in events:
        current += delta
        best = max(best, current)
    return best


def collect(job_roots: list[tuple[Path, dict]]) -> dict:
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    images_dir = OUT / "images"
    images_dir.mkdir(parents=True)
    copied: list[Path] = []
    rows: list[dict] = []
    for root, job in job_roots:
        for task in job["tasks"]:
            if task["status"] != "done" or not task.get("out"):
                continue
            source = root / task["out"]
            if not source.is_file():
                continue
            target = images_dir / source.name
            if target.exists():
                target = images_dir / f"{task['item']}__{source.name}"
            shutil.copyfile(source, target)
            copied.append(target)
            rows.append({"file": target.name, "style": source.parent.name,
                         "task": task["id"], "garment": task.get("garment", "")})
    zip_path = OUT / "a6api-100.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for target in sorted(copied):
            archive.write(target, Path(target.parent.name if False else "") / target.name
                          if False else Path(target.name.split("_")[0]) / target.name)
    # 总览图（按款式分组的顺序）
    order = {"24CXCR": 0, "03YLWY": 1, "23SLNDX": 2}
    ordered = sorted(copied, key=lambda p: (order.get(p.name.split("_")[0], 9), p.name))
    tiles = []
    for target in ordered:
        with Image.open(target) as image:
            thumb = image.convert("RGB")
            thumb.thumbnail((210, 280))
            tiles.append((target.name, thumb.copy()))
    cols = 10
    sheet_rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 220, sheet_rows * 300), (16, 16, 20))
    draw = ImageDraw.Draw(sheet)
    for index, (name, thumb) in enumerate(tiles):
        x = (index % cols) * 220
        y = (index // cols) * 300
        sheet.paste(thumb, (x + 5, y + 18))
        draw.text((x + 6, y + 4), name[:26], fill=(255, 214, 120))
    sheet.save(OUT / "contact-sheet.png")
    (OUT / "files.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"copied": len(copied), "zip": str(zip_path), "sheet": str(OUT / "contact-sheet.png")}


async def main() -> int:
    for image in NEW_IMAGES + [EXTRA_IMAGE]:
        if not image.is_file():
            print(f"[x] 缺原图：{image}")
            return 2
    selections = library_pairs()
    combos = sum(len(item["colors"]) for item in selections)
    print(f"[*] 款式/颜色组合 {combos} 组；2 张新图 → {combos * 2} 任务，"
          f"再补 {100 - combos * 2} 个任务到第 3 张（合计 100）")

    cfg_path = write_config()
    server = subprocess.Popen(
        [sys.executable, "serve.py", "--config", cfg_path.name],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(7)
        if server.poll() is not None:
            raise RuntimeError(f"临时实例没起来（exit={server.returncode}），端口 {PORT} 可能被占用")
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{PORT}", timeout=300) as client:
            ids = [
                await create_job(client, NEW_IMAGES, selections),
                await create_job(client, [EXTRA_IMAGE], [
                    {"garment_type": slug, "colors": colors} for slug, colors in EXTRA_PAIRS
                ]),
            ]
            jobs, wall = await wait_jobs(client, ids)
        tasks = [t for job in jobs.values() for t in job["tasks"]]
        done = [t for t in tasks if t["status"] == "done"]
        failed = [t for t in tasks if t["status"] != "done"]
        secs = sorted(
            (t["finished_at"] - t["started_at"]) for t in done
            if t.get("started_at") and t.get("finished_at")
        )
        roots = [(ROOT / "data-test-100v2" / "jobs" / job_id, job) for job_id, job in jobs.items()]
        files = collect(roots)
        summary = {
            "jobs": list(jobs),
            "tasks": len(tasks),
            "done": len(done),
            "failed": len(failed),
            "rate_limited": sum(1 for t in tasks if t.get("error_kind") == "rate_limit"),
            "peak_concurrency": peak(tasks),
            "wall_sec": wall,
            "per_min": round(len(done) / max(1, wall) * 60, 1),
            "latency_p50": round(statistics.median(secs), 1) if secs else None,
            "latency_p95": round(secs[max(0, int(len(secs) * 0.95) - 1)], 1) if secs else None,
            "latency_max": round(secs[-1], 1) if secs else None,
            "estimate_unit": (list(jobs.values())[0]["params"].get("estimate") or {}).get("unit"),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sources": [str(p.relative_to(ROOT)) for p in NEW_IMAGES + [EXTRA_IMAGE]],
            "errors": [
                {"task": t["id"], "kind": t.get("error_kind"), "error": (t.get("error") or "")[:160]}
                for t in failed[:10]
            ],
            **{k: v for k, v in files.items()},
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
        print("\n========== 结果 ==========")
        for key in ("tasks", "done", "failed", "rate_limited", "peak_concurrency", "wall_sec",
                    "per_min", "latency_p50", "latency_p95", "latency_max", "estimate_unit"):
            print(f"  {key:<18} {summary.get(key)}")
        print(f"  图片              {files['copied']} 张 → {files['zip']}")
        print(f"  总览图            {files['sheet']}")
        print(f"  报告              {OUT / 'report.json'}")
        if failed:
            print("  失败样例：", summary["errors"][:4])
        return 0 if not failed else 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
