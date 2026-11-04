"""用 mock 接口在工作台里建一个任务（只为了看 UI 的成品卡/进度，不花钱）。"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8789"


def png(color=(200, 90, 80), size=(768, 1024)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=180, follow_redirects=False) as client:
        print("login:", client.post("/login", data={"username": "admin", "password": "Asd123456."}).status_code)
        data = png()
        files = [("files", (f"item-01_view-{i:02d}.png", data, "image/png")) for i in range(1, 4)]
        form = {
            "garment_type": "short-sleeve",
            "colors": ["黑色", "白色", "杏色"],
            "provider": "mock",
            "model": "gpt-image-2",
            "size": "1024x1536",
            "output_format": "png",
            "start": "true",
            "concurrency": "3",
            "mode": "single_pass",
            "quality": "high",
        }
        response = client.post("/api/jobs", data=form, files=files)
        if response.status_code != 200:
            print("创建失败", response.status_code, response.text[:300])
            return 1
        job = response.json()["job"]
        print("job:", job["id"], "tasks:", len(job["tasks"]))
        for _ in range(40):
            time.sleep(1)
            live = client.get(f"/api/jobs/{job['id']}").json()["job"]
            if live["status"] in {"done", "partial", "failed", "canceled"}:
                print("status:", live["status"], live["stats"])
                break
        print("打开：", f"{BASE}/?token=1_JiQgjmfST8&job={job['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
