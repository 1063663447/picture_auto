"""增量渲染验证：开一个 10 张的任务，统计「轮询是否导致图片被重复请求」。

用法：python tools/check_incremental.py [--base http://127.0.0.1:8789] [--colors 10] [--concurrency 1]
"""
from __future__ import annotations

import argparse
import io
import time

import httpx
from PIL import Image


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8789")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--colors", default="")
    args = parser.parse_args(argv)

    buf = io.BytesIO()
    Image.new("RGB", (768, 1024), (90, 120, 200)).save(buf, format="PNG")
    data = buf.getvalue()

    with httpx.Client(base_url=args.base, timeout=180, follow_redirects=False) as client:
        client.post("/login", data={"username": "admin", "password": "Asd123456."})
        form = {
            "garment_type": "short-sleeve",
            "provider": "mock",
            "model": "gpt-image-2",
            "size": "1024x1536",
            "output_format": "png",
            "start": "true",
            "concurrency": str(args.concurrency),
            "mode": "single_pass",
        }
        if args.colors:
            form["colors"] = [c for c in args.colors.split(",") if c]
        response = client.post(
            "/api/jobs", data=form, files=[("files", ("item-01_front.png", data, "image/png"))]
        )
        if response.status_code != 200:
            print("[x]", response.status_code, response.text[:300])
            return 1
        job = response.json()["job"]
        job_id = job["id"]
        print(f"job={job_id} tasks={len(job['tasks'])}")

        # 模拟前端轮询：每 1.2s 拉一次，记录每轮的 thumb 是否有变化
        seen_thumb: dict[str, str] = {}
        flips = 0
        polls = 0
        deadline = time.time() + 120
        while time.time() < deadline:
            time.sleep(1.2)
            live = client.get(f"/api/jobs/{job_id}").json()["job"]
            polls += 1
            changed = 0
            for task in live["tasks"]:
                thumb = task.get("thumb") or ""
                if thumb and seen_thumb.get(task["id"]) != thumb:
                    seen_thumb[task["id"]] = thumb
                    changed += 1
            flips += changed
            print(f"  poll#{polls} status={live['status']} 新图 {changed} 累计 {flips}/{len(live['tasks'])}")
            if live["status"] in {"done", "partial", "failed", "canceled"}:
                break
        print(f"总轮询 {polls} 次；图片 URL 变化次数 {flips}（增量渲染下每张图只应变化一次）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
