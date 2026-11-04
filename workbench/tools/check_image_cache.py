"""验证：界面只剩款式库的 10 款 + 成品图带长缓存头。"""
from __future__ import annotations

import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8795"


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=180, follow_redirects=False) as client:
        client.post("/login", data={"username": "admin", "password": "Asd123456."})
        skill = client.get("/api/skill").json()
        print("界面款式数:", len(skill["types"]))
        print("选项:", [g["display_name"] for g in skill["types"].values()])
        print("隐藏:", skill.get("hidden"))

        jobs = client.get("/api/jobs").json()["jobs"]
        if not jobs:
            print("[i] 没有历史任务，跳过图片缓存头检查")
            return 0
        job = jobs[0]
        live = client.get(f"/api/jobs/{job['id']}").json()["job"]
        thumb = next((t["thumb"] for t in live["tasks"] if t.get("thumb")), "")
        print("任务:", job["id"], job["status"], "| 首图:", thumb)
        if not thumb:
            return 0
        first = client.get(thumb)
        second = client.get(thumb)
        print("第一次:", first.status_code, first.headers.get("cache-control"),
              len(first.content), "bytes")
        print("第二次:", second.status_code, second.headers.get("cache-control"),
              "| ETag 一致:", first.headers.get("etag") == second.headers.get("etag"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
