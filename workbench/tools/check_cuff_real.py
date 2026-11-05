"""真接口验证袖口工艺：长袖 T 恤必须是平口，圆领卫衣必须留螺纹。

为什么单独一个脚本：这个问题只有真模型才复现得出来（mock 不画衣服），
而它又是「客户一眼看出不是这件衣服」级别的硬伤，所以值得留一条真接口回归。

跑一次 ≈ 2 张图 ≈ 0.08 元，所以要显式 --yes。
产物：data/test/cuff/real-cuffs.png（把两款的袖口/下摆裁出来拼一张，人眼看一眼）。

用法：
    python tools/check_cuff_real.py --yes
    python tools/check_cuff_real.py --yes --base http://127.0.0.1:8787
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "test" / "christmas-tee.jpg"
OUT_DIR = ROOT / "data" / "test" / "cuff"

#: 一个平口、一个螺纹，对照着看有没有改坏另一半
CASES = (
    ("24-long-sleeve", "红色", "长袖T恤-应平口", "plain"),
    ("03-crewneck-sweatshirt", "黑色", "圆领卫衣-应螺纹", "ribbed"),
)


def create_job(client: httpx.Client, garment: str, color: str) -> dict:
    files = {"files": (SRC.name, SRC.read_bytes(), "image/jpeg")}
    data = {
        "garment_type": garment,
        "colors": color,
        "provider": "terra",
        "model": "gpt-image-2",
        "output_format": "png",
        "start": "true",
        "concurrency": "auto",
        "mode": "single_pass",
        "send_reference": "true",
        "quality": "high",
    }
    response = client.post("/api/jobs", data=data, files=files)
    response.raise_for_status()
    return response.json()["job"]


def wait_job(client: httpx.Client, job_id: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        time.sleep(3)
        payload = client.get(f"/api/jobs/{job_id}").json()["job"]
        if payload["status"] != last:
            print(f"    status={payload['status']} {payload.get('stage') or ''}")
            last = payload["status"]
        if payload["status"] in {"done", "partial", "failed", "canceled"}:
            return payload
    raise TimeoutError(f"{job_id} 超时未完成")


def fetch_image(client: httpx.Client, job_id: str, task_id: str) -> bytes | None:
    response = client.get(f"/api/jobs/{job_id}/image/{task_id}")
    if response.status_code != 200 or len(response.content) < 2000:
        return None
    return response.content


def contact_sheet(rows: list[tuple[str, Path, str]]) -> Path:
    """把每张图的下半身（袖口+下摆）裁出来，按行拼一张对照图。"""
    width = 560
    tiles = []
    for label, path, cuff in rows:
        image = Image.open(path).convert("RGB")
        w, h = image.size
        crop = image.crop((0, int(h * 0.55), w, h))
        crop = crop.resize((width, int(crop.height * width / crop.width)))
        tiles.append((f"{label} [{cuff}] {w}x{h}", crop))
    row_height = max(tile.height for _label, tile in tiles)
    sheet = Image.new("RGB", (width, (row_height + 30) * len(tiles)), (18, 18, 22))
    draw = ImageDraw.Draw(sheet)
    for index, (label, tile) in enumerate(tiles):
        top = index * (row_height + 30)
        sheet.paste(tile, (0, top + 30))
        draw.text((8, top + 9), label, fill=(255, 214, 120))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "real-cuffs.png"
    sheet.save(out)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="真接口验证平口/螺纹")
    parser.add_argument("--base", default="http://127.0.0.1:8787")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="Asd123456.")
    parser.add_argument("--timeout", type=float, default=420)
    parser.add_argument("--yes", action="store_true", help="确认要花真钱")
    args = parser.parse_args(argv)

    if not args.yes:
        print("[!] 这会用真接口出图（约 0.08 元），确认后再加 --yes")
        return 2
    if not SRC.is_file():
        print(f"[x] 缺测试原图：{SRC}")
        return 2

    rows: list[tuple[str, Path, str]] = []
    failures: list[str] = []
    with httpx.Client(base_url=args.base, timeout=180, follow_redirects=False) as client:
        print("login:", client.post(
            "/login", data={"username": args.user, "password": args.password}
        ).status_code)
        for garment, color, label, cuff in CASES:
            print(f"\n[{garment}] {color}")
            job = create_job(client, garment, color)
            print(f"    job={job['id']} tasks={len(job['tasks'])}")
            done = wait_job(client, job["id"], args.timeout)
            for task in done["tasks"]:
                print(f"    {task['id']:<26} {task['status']:<7} {(task.get('error') or '')[:60]}")
                if task["status"] != "done":
                    failures.append(f"{garment} {task['id']} 未出图：{task.get('error')}")
                    continue
                data = fetch_image(client, job["id"], task["id"])
                if data is None:
                    failures.append(f"{garment} {task['id']} 取图失败")
                    continue
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                target = OUT_DIR / f"real-{garment}-{task['color']}.png"
                target.write_bytes(data)
                rows.append((f"{label} · {task['color']}", target, cuff))

    if rows:
        sheet = contact_sheet(rows)
        print(f"\n对照图：{sheet}")
    if failures:
        print("\n失败：")
        for item in failures:
            print("  -", item)
        return 1
    print("\n出图完成：打开对照图确认 长袖=平口折边、卫衣=罗纹带")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
