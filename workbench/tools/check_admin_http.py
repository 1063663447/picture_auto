"""管理后台 HTTP 自检：/admin 页面 + 账号增删改 + 额度拦截 + 权限。

用法：python tools/check_admin_http.py [--base http://127.0.0.1:8789]
"""
from __future__ import annotations

import argparse
import sys

import httpx


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8789")
    parser.add_argument("--admin", default="admin")
    parser.add_argument("--password", default="Asd123456.")
    args = parser.parse_args(argv)

    with httpx.Client(base_url=args.base, timeout=180, follow_redirects=False) as client:
        # 未登录访问 /admin → 跳登录
        r = client.get("/admin")
        print("anon /admin ->", r.status_code, r.headers.get("location"))

        r = client.post("/login", data={"username": args.admin, "password": args.password, "next": "/admin"})
        print("login admin ->", r.status_code, r.headers.get("location"))
        if r.status_code != 302:
            return 1

        r = client.get("/admin")
        print("/admin ->", r.status_code, "含账号表:", "accountRows" in r.text, "字数:", len(r.text))
        r = client.get("/api/session")
        print("session:", r.json())

        o = client.get("/api/admin/overview").json()
        print("overview:", o["totals"], "usage_source:", o["usage_source"])

        # 建账号
        body = {"username": "check_user", "password": "Check@2026", "display": "自检账号",
                "role": "member", "quota": 0.05}
        r = client.post("/api/accounts", json=body)
        print("create:", r.status_code, r.json().get("account") or r.json().get("error"))

        # 重复建 → 400
        r = client.post("/api/accounts", json=body)
        print("create dup:", r.status_code, (r.json() or {}).get("error"))

        # 改额度
        r = client.patch("/api/accounts/check_user", json={"quota": 1.5, "display": "自检账号B"})
        print("patch quota:", r.status_code, (r.json().get("account") or {}).get("quota"))

        # 改密码
        r = client.post("/api/accounts/check_user/password", json={"password": "New@2026"})
        print("passwd:", r.status_code, r.json().get("error") or "ok")

        # 新账号能登录
        with httpx.Client(base_url=args.base, timeout=60, follow_redirects=False) as sub:
            r = sub.post("/login", data={"username": "check_user", "password": "New@2026"})
            print("check_user login:", r.status_code)
            r = sub.get("/api/session")
            print("  session:", r.json().get("account"), r.json().get("role"))
            r = sub.get("/admin")
            print("  /admin as member ->", r.status_code, r.headers.get("location"))
            r = sub.get("/api/admin/overview")
            print("  overview as member ->", r.status_code)

        # 额度过低 → 建任务被拦
        with httpx.Client(base_url=args.base, timeout=120, follow_redirects=False) as sub:
            sub.post("/login", data={"username": "check_user", "password": "New@2026"})
            sub.patch("/api/accounts/check_user", json={"quota": 0.01})  # 会 403，忽略
        client.patch("/api/accounts/check_user", json={"quota": 0.01})
        png = sys.path[0]
        from pathlib import Path

        src = Path("data/tmp/item-01_front.png")
        with httpx.Client(base_url=args.base, timeout=180, follow_redirects=False) as sub:
            sub.post("/login", data={"username": "check_user", "password": "New@2026"})
            with src.open("rb") as handle:
                r = sub.post(
                    "/api/jobs",
                    data={"garment_type": "short-sleeve", "colors": "黑色", "provider": "mock",
                          "model": "gpt-image-2", "size": "1024x1536", "start": "false"},
                    files={"files": (src.name, handle.read(), "image/png")},
                )
            print("低额度建任务:", r.status_code, (r.json() or {}).get("error"))

        # 清理
        r = client.delete("/api/accounts/check_user")
        print("delete:", r.status_code, r.json().get("error") or "ok")
        r = client.delete(f"/api/accounts/{args.admin}")
        print("delete self ->", r.status_code, (r.json() or {}).get("error"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
