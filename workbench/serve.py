"""Start the workbench web UI.

    python serve.py                 # uses config.json
    python serve.py --port 8899
    python serve.py --config other.json
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from skillbridge.api import create_app  # noqa: E402
from skillbridge.config import load_config, load_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="服装改款换色工作台")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 config.json）")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
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
    # 云平台（Render/Koyeb/Cloud Run/HF Spaces 等）会注入 PORT，容器里需要监听 0.0.0.0
    env = load_env()
    # 注意：本机可能已经有别的 PORT 环境变量（例如别的服务），所以裸 PORT 只在"部署上下文"里采用：
    # 设了 WORKBENCH_DEPLOYED=1 或 WORKBENCH_HOST 时才算（Dockerfile 已设置）。
    deployed = bool(env.get("WORKBENCH_DEPLOYED")) or bool(env.get("WORKBENCH_HOST"))
    port = int(
        args.port
        or env.get("WORKBENCH_PORT")
        or (env.get("PORT") if deployed else None)
        or server.get("port")
        or 8787
    )
    host = args.host or env.get("WORKBENCH_HOST") or server.get("host") or "127.0.0.1"

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, port))
    except OSError:
        print(
            f"[x] 端口 {host}:{port} 已被占用，请换一个端口："
            f"python serve.py --port {port + 1}（或设环境变量 PORT）"
        )
        return 2

    app = create_app(str(Path(args.config).resolve()) if args.config else None)
    print("=" * 62)
    print("  服装改款换色工作台")
    print(f"  skill : {config['skill_root']}")
    print(f"  数据  : {config['data_dir']}")
    print(f"  地址  : http://{host}:{port}")
    print("=" * 62)
    uvicorn.run(app, host=host, port=port, log_level="info" if args.reload else "warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
