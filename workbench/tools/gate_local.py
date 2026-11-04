"""本地假阀门（调试用，不上云也能试通整套排队逻辑）。

和 `deploy/cloudflare-gate/worker.js` 同一套协议，只是跑在本机：
    python tools/gate_local.py --port 8791 --limit 2
然后 .env 里写：
    GATE_URL=http://127.0.0.1:8791/
    GATE_TOKEN=dev
重启工作台即可看到「并发数」下面的 `全局阀门 x/y`，任务日志里也会有排队提示。

用途：在没有 Cloudflare 的机器上验证「多开两个工作台也不会超过额度」。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]) if False else [])

LEASES: dict[str, dict] = {}
LOCK = threading.Lock()
STATS = {"granted": 0, "released": 0, "denied": 0, "peak": 0}
CONFIG = {"limit": 2, "ttl": 180.0, "token": ""}


def gc(now: float) -> None:
    for lease, info in list(LEASES.items()):
        if info["expires"] <= now:
            LEASES.pop(lease, None)


def handle(body: dict) -> tuple[int, dict]:
    now = time.time()
    limit = CONFIG["limit"]
    with LOCK:
        gc(now)
        action = body.get("action")
        if action == "status":
            return 200, {
                "ok": True,
                "in_use": len(LEASES),
                "limit": limit,
                "stats": dict(STATS),
                "leases": [
                    {"id": k, "holder": v["holder"], "expires_in": round(v["expires"] - now)}
                    for k, v in LEASES.items()
                ],
            }
        if action == "release":
            released = LEASES.pop(str(body.get("lease") or ""), None) is not None
            if released:
                STATS["released"] += 1
            return 200, {"ok": True, "released": released, "in_use": len(LEASES), "limit": limit}
        if action == "acquire":
            if len(LEASES) >= limit:
                STATS["denied"] += 1
                return 429, {
                    "ok": False,
                    "in_use": len(LEASES),
                    "limit": limit,
                    "retry_after_ms": 300,
                }
            lease = uuid.uuid4().hex
            LEASES[lease] = {
                "holder": str(body.get("holder") or "?"),
                "job": str(body.get("job") or ""),
                "task": str(body.get("task") or ""),
                "expires": now + CONFIG["ttl"],
            }
            STATS["granted"] += 1
            STATS["peak"] = max(STATS["peak"], len(LEASES))
            return 200, {"ok": True, "lease": lease, "in_use": len(LEASES), "limit": limit}
        return 400, {"ok": False, "error": f"unknown action {action}"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:  # noqa: BLE001
            body = {}
        if CONFIG["token"]:
            auth = self.headers.get("Authorization") or ""
            if auth != f"Bearer {CONFIG['token']}":
                return self.reply(401, {"ok": False, "error": "unauthorized"})
        try:
            status, payload = handle(body)
        except Exception as exc:  # noqa: BLE001
            status, payload = 500, {"ok": False, "error": str(exc)}
        self.reply(status, payload)

    def do_GET(self):  # noqa: N802
        self.reply(200, {"ok": True, "service": "local-gate", **handle({"action": "status"})[1]})

    def reply(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[gate] {fmt % args}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="本地假阀门（调试全局并发用）")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--ttl", type=float, default=180.0)
    parser.add_argument("--token", default="dev")
    args = parser.parse_args()
    CONFIG.update({"limit": max(1, args.limit), "ttl": max(5.0, args.ttl), "token": args.token})
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"本地阀门已启动：http://127.0.0.1:{args.port}/  额度={CONFIG['limit']}  token={args.token or '(无)'}")
    print("在 .env 里写：")
    print(f"  GATE_URL=http://127.0.0.1:{args.port}/")
    print(f"  GATE_TOKEN={args.token}")
    print("然后重启工作台，界面上「并发数」下面会显示 全局阀门 x/y。Ctrl+C 退出。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
