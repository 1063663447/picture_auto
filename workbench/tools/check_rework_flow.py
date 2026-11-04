"""验证改图（rework）提交耗时 + 响应里是否带 seed 任务（前端可立即渲染）。"""
from __future__ import annotations

import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8790"
SOURCE_JOB = sys.argv[2] if len(sys.argv) > 2 else "20261104-103511-8cb8"


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=180, follow_redirects=False) as client:
        client.post("/login", data={"username": "admin", "password": "Asd123456."})
        job = client.get(f"/api/jobs/{SOURCE_JOB}").json()["job"]
        done = [t["id"] for t in job["tasks"] if t["status"] == "done"][:3]
        print("选中改图:", done)
        started = time.time()
        response = client.post(
            f"/api/jobs/{SOURCE_JOB}/rework",
            data={
                "task_ids": done,
                "instruction": "图案缩小一点",
                "provider": "mock",
                "model": "gpt-image-2",
                "size": "1024x1536",
                "quality": "high",
                "start": "true",
            },
        )
        elapsed = time.time() - started
        print(f"rework HTTP {response.status_code} 用时 {elapsed:.2f}s")
        if response.status_code != 200:
            print(response.text[:400])
            return 1
        new_job = response.json()["job"]
        print("新任务:", new_job["id"], "| 任务数:", len(new_job["tasks"]),
              "| 状态:", new_job["status"], "| 带 thumb 的任务:",
              sum(1 for t in new_job["tasks"] if t.get("thumb")))
        t1 = time.time()
        live = client.get(f"/api/jobs/{new_job['id']}").json()["job"]
        print(f"首帧轮询 {time.time() - t1:.2f}s", live["stats"])
        for task in live["tasks"]:
            print("  ", task["id"], task["status"], f"{task.get('duration') or '-'}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
