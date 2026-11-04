"""验证下载/看图省流量：原图 vs JPG 压缩版 vs 缩略图 vs 大图，以及 ZIP 体积。"""
from __future__ import annotations

import asyncio
import io
import sys

import httpx
from PIL import Image, ImageDraw

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8801"


def photo(size=(1536, 2048)) -> bytes:
    import random

    image = Image.new("RGB", size, (110, 130, 110))
    draw = ImageDraw.Draw(image)
    random.seed(3)
    for _ in range(60000):
        x = random.randrange(size[0])
        y = random.randrange(size[1])
        draw.point((x, y), (random.randrange(256), random.randrange(256), random.randrange(256)))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


async def main() -> int:
    data = photo()
    print(f"源图 {len(data)/1024/1024:.2f} MB")
    with httpx.Client(base_url=BASE, timeout=180, follow_redirects=False) as client:
        client.post("/login", data={"username": "admin", "password": "Asd123456."})
        response = client.post(
            "/api/jobs",
            data={
                "garment_type": "03-crewneck-sweatshirt",
                "colors": ["black"],
                "provider": "mock",
                "model": "gpt-image-2",
                "size": "1024x1536",
                "start": "true",
            },
            files=[("files", ("item-01_front.png", data, "image/png"))],
        )
        job = response.json()["job"]
        job_id = job["id"]
        for _ in range(40):
            await asyncio.sleep(1)
            live = client.get(f"/api/jobs/{job_id}").json()["job"]
            if live["status"] in {"done", "partial", "failed", "canceled"}:
                break
        live = client.get(f"/api/jobs/{job_id}").json()["job"]
        task = next(t for t in live["tasks"] if t.get("thumb"))
        print("任务:", job_id, live["status"])

        def size(path: str) -> tuple[int, str]:
            r = client.get(path)
            return len(r.content), r.headers.get("content-type", "")

        rows = [
            ("缩略图 w=520", task["thumb_small"]),
            ("大图   w=1600", task["preview"]),
            ("下载 JPG(原尺寸)", f"{task['thumb']}?download=true&fmt=jpg&q=88"),
            ("下载 JPG w=1600", f"{task['thumb']}?download=true&fmt=jpg&w=1600&q=88"),
            ("下载 原图 PNG", f"{task['thumb']}?download=true"),
        ]
        for label, path in rows:
            length, ctype = size(path)
            print(f"  {label:<20} {length/1024:>8.0f} KB  {ctype}")

        zip_png = client.get(f"/api/jobs/{job_id}/export")
        zip_jpg = client.get(f"/api/jobs/{job_id}/export?fmt=jpg")
        print(f"  ZIP 原图             {len(zip_png.content)/1024:>8.0f} KB")
        print(f"  ZIP JPG 压缩         {len(zip_jpg.content)/1024:>8.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
