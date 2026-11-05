"""真接口验证：走完整链路（HTTP → service → runner → a6api）跑 3 个颜色，出对照图。

用客户那张带 06 印花的红 T 当原图，款式 10 弹力短袖，颜色 深酒红 / 粉色 / 花灰：
每次请求都是【原图 + 该色效果图】，看版型是否统一、印花是否保留。

用法：python tools/check_effect_ref_real.py --yes
花费：3 张 ≈ ¥0.02，约 60~90 秒。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8787"
SOURCE = ROOT / "data" / "jobs" / "20261105-145851-c74f" / "inputs" / "item-01" / "屏幕截图-2026-11-05-143652.png"
OUT = ROOT / "data" / "test" / "effect-ref"
COLORS = ["burgundy", "pink", "heather-grey"]
NICK = {"burgundy": "burgundy", "pink": "pink", "heather-grey": "heather-grey"}


def main() -> int:
    if "--yes" not in sys.argv:
        print("会真实调用接口（3 张 ≈ ¥0.02）。确认后再跑：python tools/check_effect_ref_real.py --yes")
        return 2
    if not SOURCE.is_file():
        print(f"原图不存在：{SOURCE}")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    selections = json.dumps(
        [{"garment_type": "10-stretch-tee", "colors": COLORS}], ensure_ascii=False
    )
    with httpx.Client(base_url=BASE, timeout=60.0, follow_redirects=True) as client:
        client.post("/login", data={"username": "admin", "password": "Asd123456.", "next": "/"})
        with SOURCE.open("rb") as handle:
            response = client.post(
                "/api/jobs",
                files={"files": (SOURCE.name, handle, "image/png")},
                data={
                    "selections": selections,
                    "provider": "a6api",
                    "subject": "garment",
                    "size": "auto",
                    "start": "true",
                },
            )
        payload = response.json()
        if not payload.get("ok"):
            print("建任务失败：", payload)
            return 2
        job_id = payload["job"]["id"]
        print(f"任务 {job_id} 已开工，等待出图 …")
        deadline = time.time() + 420
        job = payload["job"]
        while time.time() < deadline:
            time.sleep(4)
            job = client.get(f"/api/jobs/{job_id}").json()["job"]
            stats = job.get("stats") or {}
            print(f"  {stats.get('done', 0)}/{stats.get('total', 0)} done, "
                  f"{stats.get('failed', 0)} failed, stage={job.get('stage')}")
            if all((t.get("status") in {"done", "failed", "skipped", "canceled"}) for t in job["tasks"]):
                break
        print("任务状态：", job.get("status"))
        paths = {}
        for task in job["tasks"]:
            if task.get("status") != "done" or not task.get("out"):
                print(f"  [fail] {task['id']} {task.get('error')}")
                continue
            src = ROOT / "data" / "jobs" / job_id / str(task["out"])
            target = OUT / f"live-{task['color']}.png"
            shutil.copy2(src, target)
            paths[task["color"]] = target
            print(f"  存下 {target.name}")
        (OUT / "live-job.json").write_text(
            json.dumps({"job": job_id, "colors": list(paths)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 清理这单（成品已另存），避免污染历史列表
        shutil.rmtree(ROOT / "data" / "jobs" / job_id, ignore_errors=True)
        print("已清理任务记录")

    if not paths:
        return 1
    from PIL import Image, ImageDraw

    cell, gap, label_h = 400, 12, 24
    ordered = [c for c in COLORS if c in paths]
    width = cell * len(ordered) + gap * (len(ordered) + 1)
    height = cell + label_h + gap * 2
    sheet = Image.new("RGB", (width, height), (20, 24, 28))
    draw = ImageDraw.Draw(sheet)
    draw.text((gap + 4, gap // 2), "source + official effect image per colour (a6api)", fill=(235, 240, 246))
    for index, color in enumerate(ordered):
        x = gap + index * (cell + gap)
        with Image.open(paths[color]) as handle:
            image = handle.convert("RGB")
        scale = min(cell / image.width, cell / image.height)
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
        sheet.paste(image, (x + (cell - image.width) // 2, gap + label_h))
        draw.text((x + 4, gap + label_h - 12), NICK.get(color, color), fill=(150, 200, 180))
    out = OUT / "effect-ref-real.png"
    sheet.save(out)
    print(f"对照图：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
