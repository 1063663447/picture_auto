"""验证轮询省流量：ETag/304 + 响应体大小对比。"""
from __future__ import annotations

import io
import sys
import time

import httpx
from PIL import Image

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8800"


def main() -> int:
    buf = io.BytesIO()
    Image.new("RGB", (768, 1024), (100, 120, 140)).save(buf, format="PNG")
    with httpx.Client(base_url=BASE, timeout=180, follow_redirects=False) as client:
        client.post("/login", data={"username": "admin", "password": "Asd123456."})
        response = client.post(
            "/api/jobs",
            data={
                "garment_type": "03-crewneck-sweatshirt",
                "colors": ["black", "white", "heather-grey"],
                "provider": "mock",
                "model": "gpt-image-2",
                "size": "1024x1536",
                "start": "true",
                "concurrency": "3",
            },
            files=[("files", ("item-01_front.png", buf.getvalue(), "image/png"))],
        )
        job = response.json()["job"]
        job_id = job["id"]
        print("job:", job_id, "tasks:", len(job["tasks"]))

        first = client.get(f"/api/jobs/{job_id}")
        etag = first.headers.get("etag")
        print(f"第一次: {first.status_code} {len(first.content)} bytes etag={etag}")
        second = client.get(f"/api/jobs/{job_id}", headers={"If-None-Match": etag})
        print(f"带 ETag 再请求: {second.status_code} {len(second.content)} bytes")
        time.sleep(2)
        third = client.get(f"/api/jobs/{job_id}", headers={"If-None-Match": etag})
        print(f"2 秒后（内容可能变了）: {third.status_code} {len(third.content)} bytes")

        # 等完成
        for _ in range(30):
            live = client.get(f"/api/jobs/{job_id}").json()["job"]
            if live["status"] in {"done", "partial", "failed", "canceled"}:
                break
            time.sleep(1)
        live = client.get(f"/api/jobs/{job_id}").json()["job"]
        print("完成:", live["status"], live["stats"])
        print("任务里是否带提示词:", "prompt" in live["tasks"][0])
        with_prompt = client.get(f"/api/jobs/{job_id}?prompts=true").json()["job"]
        print("?prompts=true 带提示词:", "prompt" in with_prompt["tasks"][0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
