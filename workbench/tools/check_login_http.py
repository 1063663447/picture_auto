"""登录 / 会话 / 账号用量 HTTP 自检（进程内 TestClient）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from skillbridge.api import create_app  # noqa: E402

app = create_app()
with TestClient(app) as client:
    r = client.get("/api/health")
    print("health", r.status_code)

    r = client.get("/api/session")
    print("session(anon)", r.status_code, r.json())

    r = client.get("/")
    print("root anon -> login page:", r.status_code, "「登录」in html:", "登录" in r.text)

    r = client.get("/api/pricing")
    print("pricing anon", r.status_code, r.json().get("error"))

    r = client.post("/login", data={"username": "admin", "password": "wrong"}, follow_redirects=False)
    print("login bad ->", r.status_code, r.headers.get("location"))

    r = client.post("/login", data={"username": "admin", "password": "Asd123456."},
                    follow_redirects=False)
    print("login ok ->", r.status_code, r.headers.get("location"),
          "cookie:", bool(r.cookies.get("wb_session")))

    r = client.get("/api/session")
    print("session(auth)", r.json())

    r = client.get("/")
    print("root auth -> app page:", "<title>" in r.text, r.text[:60].replace("\n", " "))

    r = client.get("/api/pricing")
    data = r.json()
    print("pricing auth:", data.get("source"), data.get("currency"),
          len(data.get("models") or []), "usage accounts:", len(data["usage"]["accounts"]))
    print("  sample model:", data["models"][0]["model"], data["models"][0]["unit"])

    r = client.get("/api/accounts")
    print("accounts", r.status_code, r.json().get("source"),
          [(a["username"], a["images"], a["cost"]) for a in r.json().get("accounts", [])])

    r = client.post("/api/logout")
    print("logout", r.status_code, "session cleared:",
          client.get("/api/session").json().get("authenticated"))
