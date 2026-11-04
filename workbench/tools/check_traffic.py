"""验证三件事：飞书自动同步、缩略图省流量、gzip。"""
from __future__ import annotations

import asyncio
import io
import sys

import httpx
from PIL import Image, ImageDraw

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8799"


def photo_bytes(size=(1024, 1536)) -> bytes:
    """造一张"像照片"的图（有噪点，PNG 不会太小）。"""
    import random

    image = Image.new("RGB", size, (110, 130, 110))
    draw = ImageDraw.Draw(image)
    random.seed(11)
    for _ in range(12000):
        x = random.randrange(size[0])
        y = random.randrange(size[1])
        draw.point((x, y), (random.randrange(256), random.randrange(256), random.randrange(256)))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


async def main() -> int:
    data = photo_bytes()
    print(f"测试图 {len(data)/1024:.0f} KB")
    with httpx.Client(base_url=BASE, timeout=180, follow_redirects=False) as client:
        client.post("/login", data={"username": "admin", "password": "Asd123456."})
        form = {
            "garment_type": "03-crewneck-sweatshirt",
            "colors": ["black", "white"],
            "provider": "mock",
            "model": "gpt-image-2",
            "size": "1024x1536",
            "start": "true",
            "concurrency": "2",
        }
        response = client.post(
            "/api/jobs", data=form,
            files=[("files", ("item-01_front.png", data, "image/png"))],
        )
        if response.status_code != 200:
            print("[x]", response.status_code, response.text[:300])
            return 1
        job = response.json()["job"]
        job_id = job["id"]
        print("job:", job_id, "tasks:", len(job["tasks"]))

        # 等完成 + 等自动同步（线程池里跑，最多等 40s）
        synced = 0
        for _ in range(40):
            await asyncio.sleep(1)
            live = client.get(f"/api/jobs/{job_id}").json()["job"]
            if live["status"] in {"done", "partial", "failed", "canceled"}:
                synced = int(live["params"].get("feishu_rows") or 0)
                if synced:
                    print("自动同步成功：", synced, "行")
                    break
        if not synced:
            live = client.get(f"/api/jobs/{job_id}").json()["job"]
            print("自动同步未完成，日志尾部：")
            for entry in live["log"][-6:]:
                print("   ", entry["level"], entry["msg"][:120])
            print("改为手动补写：")
            fix = client.post(f"/api/jobs/{job_id}/feishu").json()
            print("   补写结果:", fix)

        live = client.get(f"/api/jobs/{job_id}").json()["job"]
        task = next(t for t in live["tasks"] if t.get("thumb"))
        full = client.get(task["thumb"])
        small = client.get(task["thumb_small"])
        print(f"原图  {len(full.content)/1024:.0f} KB  cache={full.headers.get('cache-control')}")
        print(f"缩略图 {len(small.content)/1024:.0f} KB  type={small.headers.get('content-type')}"
              f"  cache={small.headers.get('cache-control')}")
        if full.content:
            print(f"节省 {100 - len(small.content)/len(full.content)*100:.1f}%")

        # gzip：拿 JSON 看内容编码
        r = client.get("/api/jobs", headers={"Accept-Encoding": "gzip"})
        print("gzip:", r.headers.get("content-encoding"), "| 压缩后", len(r.content), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
