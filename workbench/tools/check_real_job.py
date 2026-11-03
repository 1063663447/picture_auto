"""真接口跑一单：登录 → 建任务 → 等到出图 → 检查账号记到用量/飞书。

用法：python tools/check_real_job.py --base http://127.0.0.1:8788 --colors 黑色 --timeout 240
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

SRC = Path("data/tmp/item-01_front.png")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="真接口跑一单并核对账号")
    parser.add_argument("--base", default="http://127.0.0.1:8788")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="Apparel@2026")
    parser.add_argument("--garment", default="short-sleeve")
    parser.add_argument("--colors", default="黑色")
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--size", default="1024x1536")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args(argv)

    if not SRC.is_file():
        print(f"[x] 先准备测试图 {SRC}")
        return 2

    with httpx.Client(base_url=args.base, timeout=120, follow_redirects=False) as client:
        r = client.post("/login", data={"username": args.user, "password": args.password})
        print("login:", r.status_code, "cookie:", "wb_session" in client.cookies)
        if r.status_code != 302:
            return 1
        r = client.get("/api/session")
        print("session:", r.json())

        with SRC.open("rb") as handle:
            files = {"files": (SRC.name, handle.read(), "image/png")}
        data = {
            "garment_type": args.garment,
            "colors": args.colors,
            "provider": "terra",
            "model": args.model,
            "size": args.size,
            "output_format": "png",
            "start": "true",
            "concurrency": "auto",
            "mode": "single_pass",
            "send_reference": "true",
            "quality": "high",
        }
        r = client.post("/api/jobs", data=data, files=files)
        if r.status_code != 200:
            print("[x] 建任务失败", r.status_code, r.text[:400])
            return 1
        job = r.json()["job"]
        job_id = job["id"]
        print("job:", job_id, "tasks:", len(job["tasks"]),
              "account:", job["params"].get("account"),
              "estimate:", (job["params"].get("estimate") or {}).get("total"))

        deadline = time.time() + args.timeout
        last = None
        while time.time() < deadline:
            time.sleep(3)
            payload = client.get(f"/api/jobs/{job_id}").json()["job"]
            status = payload["status"]
            if status != last:
                print(f"  status={status} stage={payload.get('stage')}")
                last = status
            if status in {"done", "partial", "failed", "canceled"}:
                for task in payload["tasks"]:
                    print(f"   {task['id']:<24} {task['status']:<8} {(task.get('error') or '')[:60]}")
                break
        else:
            print("[!] 超时未完成")
            return 1

        usage = client.get("/api/usage").json()
        print("usage today:", usage["today"], "accounts:",
              [(a["account"], a["images"], a["cost"]) for a in usage["accounts"]])
        accounts = client.get("/api/accounts").json()
        print("accounts source:", accounts["source"], accounts["usage_source"])
        for row in accounts["accounts"]:
            print("  ", row["username"], row["images"], row["cost"], row["jobs"],
                  [x["detail"] for x in row["recent"][:3]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
