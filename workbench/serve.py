"""Start the workbench web UI（本地版）.

    python serve.py                 # 用 config.json，默认 127.0.0.1:8787
    python serve.py --open          # 启动后自动打开浏览器（run.bat 默认这样）
    python serve.py --port 8899
    python serve.py --config other.json
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from skillbridge.api import create_app  # noqa: E402
from skillbridge.config import load_config, load_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="生图工作台（本地版）")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 config.json）")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument(
        "--share",
        action="store_true",
        help="局域网共享：绑 0.0.0.0，别人用浏览器访问这台机器提交任务（排队/出图都在这一台）",
    )
    parser.add_argument("--reload", action="store_true", help="开发模式：文件改动自动重载")
    args = parser.parse_args()

    # 输出被重定向到管道/文件时用 UTF-8，避免中文日志变乱码（真实控制台不受影响）
    if not sys.stdout.isatty():
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    config = load_config(Path(args.config) if args.config else None)
    server = config.get("server") or {}
    env = load_env()
    # 只有"部署上下文"才认云平台注入的 PORT（本地常常已经有别的 PORT 变量）
    deployed = bool(env.get("WORKBENCH_DEPLOYED")) or bool(env.get("WORKBENCH_HOST"))
    port = int(
        args.port
        or env.get("WORKBENCH_PORT")
        or (env.get("PORT") if deployed else None)
        or server.get("port")
        or 8787
    )
    host = (
        "0.0.0.0"
        if args.share
        else (args.host or env.get("WORKBENCH_HOST") or server.get("host") or "127.0.0.1")
    )

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, port))
    except OSError:
        print(
            f"[x] 端口 {host}:{port} 已被占用，请换一个端口："
            f"python serve.py --port {port + 1}（或设环境变量 PORT）"
        )
        return 2

    # 让 service 知道真正监听的地址（通知里的「取图」链接、共享模式的局域网地址都靠它）
    os.environ["WORKBENCH_HOST"] = str(host)
    os.environ["WORKBENCH_PORT"] = str(port)
    app = create_app(str(Path(args.config).resolve()) if args.config else None)
    url = f"http://{'127.0.0.1' if host in {'0.0.0.0', '::'} else host}:{port}"
    sharing = host in {"0.0.0.0", "::"}
    token = str(server.get("auth_token") or "")
    feishu = config.get("feishu") or {}
    print("=" * 64)
    print("  生图工作台（本地）")
    print(f"  地址    : {url}")
    if token:
        print(f"  应急口令: {url}/?token={token}")
    print("  登录    : 飞书「账号」表的账号（默认 admin / Asd123456.）")
    print(f"  skill   : {config['skill_root']}")
    print(f"  款式库  : {(config.get('library') or {}).get('dir') or '（未启用）'}")
    print(f"  数据    : {config['data_dir']}（成品、日志、本地缓存都在这）")
    print(
        "  飞书    : "
        + (
            f"已接入（app_token {feishu.get('app_token')}）"
            if feishu.get("enabled") and feishu.get("app_token")
            else "未配置（记录只存本地）"
        )
    )
    if sharing:
        print("  " + "-" * 60)
        print("  共享模式：同一局域网的同事用下面的地址访问这台机器")
        for ip in _lan_ips():
            print(f"    http://{ip}:{port}")
        print("  · 任务、并发、出图全在这一台机器上排队执行，别人只是用浏览器提交")
        print(f"  · 第一次可能要在 Windows 防火墙放行：netsh advfirewall firewall add rule "
              f"name=\"workbench {port}\" dir=in action=allow protocol=TCP localport={port}")
        print("  · 只建议在局域网/公司网内这样用；需要外网就套 Tailscale（见 README §5.9）")
        print("  " + "-" * 60)
    else:
        print("  说明    : 关掉这个窗口即停止；下次重新双击 run.bat 即可")
        print("  共享    : 想让同事也能提交？用 python serve.py --share（同一局域网）")
    print("=" * 64, flush=True)

    if args.open:
        def _open() -> None:
            time.sleep(1.6)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="info" if args.reload else "warning")
    return 0


def _lan_ips() -> list[str]:
    """尽量列出本机的局域网地址（供 --share 打印）。"""
    ips: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            ips.append(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips or ["127.0.0.1"]


if __name__ == "__main__":
    raise SystemExit(main())
