"""用 Render API 一键创建并部署工作台（需要 Render API Key）。

Key 获取：Render Dashboard → 右上头像 → Account Settings → API Keys → Create API Key（`rnd_...`）。

前提（一次性，Dashboard 里点一下）：
  Render → Account Settings → GitHub → Connect GitHub，并授权 `1063663447/picture_auto`
  （Render 通过 GitHub OAuth 拉代码；没连过的话 API 会报 "repo not connected"）

用法：
    python deploy/render_deploy.py --api-key rnd_xxx \
        --repo https://github.com/1063663447/picture_auto \
        --env TERRA_API_KEY=sk-xxx --env DEEPSEEK_API_KEY=sk-xxx \
        --env FEISHU_APP_ID=cli_xxx --env FEISHU_APP_SECRET=xxx --wait

幂等：同名服务已存在则复用（不重复创建），只更新环境变量并触发一次部署。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

API = "https://api.render.com/v1"
DOCKERFILE = "./workbench/deploy/Dockerfile"
CONTEXT = "."


def call(method: str, path: str, key: str, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(f"{API}{path}", data=data, method=method)
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"message": body[:400]}
    except urllib.error.URLError as exc:
        return 0, {"message": f"网络错误：{exc.reason}"}


def pick_owner(key: str) -> str:
    status, owners = call("GET", "/owners?limit=20", key)
    if status != 200 or not owners:
        raise SystemExit(f"[x] 读账号失败：{status} {owners}")
    first = owners[0]
    owner = first.get("owner") or first
    return owner.get("id") or first.get("id")


def find_service(key: str, name: str) -> dict | None:
    status, data = call("GET", "/services?limit=100", key)
    if status != 200 or not data:
        return None
    for item in data:
        service = item.get("service") or item
        if service.get("name") == name:
            return service
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="创建/更新并部署 Render 服务")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--repo", default="https://github.com/1063663447/picture_auto")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--name", default="apparel-recolor-workbench")
    parser.add_argument("--plan", default="free")
    parser.add_argument("--region", default="singapore", help="免费实例常用 singapore / oregon")
    parser.add_argument("--env", action="append", default=[], help="KEY=VALUE，可重复")
    parser.add_argument("--wait", action="store_true", help="等部署完成并打印地址")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    env_vars = []
    for item in args.env:
        if "=" not in item:
            continue
        key, _, value = item.partition("=")
        env_vars.append({"key": key.strip(), "value": value.strip()})
    # 平台相关固定变量
    for key, value in (
        ("WORKBENCH_HOST", "0.0.0.0"),
        ("WORKBENCH_DEPLOYED", "1"),
        ("PYTHONUNBUFFERED", "1"),
    ):
        if not any(item["key"] == key for item in env_vars):
            env_vars.append({"key": key, "value": value})

    owner_id = pick_owner(args.api_key)
    print(f"[*] 账号 ownerId = {owner_id}")

    existing = find_service(args.api_key, args.name)
    if existing:
        service_id = existing["id"]
        print(f"[*] 服务已存在，复用：{args.name} ({service_id})")
        if env_vars:
            status, _ = call("PUT", f"/services/{service_id}/env-vars", args.api_key, env_vars)
            print(f"    更新环境变量：HTTP {status}")
    else:
        payload = {
            "type": "web_service",
            "name": args.name,
            "ownerId": owner_id,
            "repo": args.repo,
            "branch": args.branch,
            "autoDeploy": "yes",
            "envVars": env_vars,
            "serviceDetails": {
                "env": "docker",
                "plan": args.plan,
                "region": args.region,
                "healthCheckPath": "/api/health",
                "envSpecificDetails": {
                    "dockerfilePath": DOCKERFILE,
                    "dockerContext": CONTEXT,
                },
            },
        }
        status, result = call("POST", "/services", args.api_key, payload)
        if status not in (200, 201):
            hint = ""
            message = json.dumps(result, ensure_ascii=False)[:400]
            if "repo" in message.lower() or "not connected" in message.lower():
                hint = "\n    → 先在 Render Dashboard → Account Settings → GitHub 里连上这个仓库"
            if args.region and "region" in message.lower():
                hint += "\n    → 换个区域试试：--region oregon"
            print(f"[x] 创建服务失败：HTTP {status} {message}{hint}")
            return 1
        service = (result or {}).get("service") or result or {}
        service_id = service.get("id")
        print(f"[*] 已创建服务：{args.name} ({service_id})")

    status, deploy = call("POST", f"/services/{service_id}/deploys", args.api_key, {})
    deploy = (deploy or {}).get("deploy") or deploy or {}
    deploy_id = deploy.get("id")
    print(f"[*] 触发部署：{deploy_id or '(无 id)'}")

    if not args.wait:
        print(f"[*] 进度看：https://dashboard.render.com/web/{service_id}")
        return 0

    started = time.time()
    url = ""
    while time.time() - started < args.timeout:
        time.sleep(15)
        status, service = call("GET", f"/services/{service_id}", args.api_key)
        service = (service or {}).get("service") or service or {}
        details = service.get("serviceDetails") or {}
        url = details.get("url") or service.get("url") or ""
        dstatus = ""
        if deploy_id:
            _, d = call("GET", f"/services/{service_id}/deploys/{deploy_id}", args.api_key)
            d = (d or {}).get("deploy") or d or {}
            dstatus = d.get("status") or ""
        elapsed = int(time.time() - started)
        print(f"    [{elapsed:>3}s] deploy={dstatus or '?'} url={url or '-'}")
        if dstatus in {"live"}:
            break
        if dstatus in {"build_failed", "update_failed", "canceled", "deactivated"}:
            print(f"[x] 部署失败：{dstatus}（看日志：https://dashboard.render.com/web/{service_id}/deploys/{deploy_id}）")
            return 1

    if url:
        print()
        print("=" * 70)
        print(f"  服务地址： {url}")
        print(f"  带口令打开： {url}/?token=<config.json 里的 auth_token>")
        print(f"  健康检查： {url}/api/health （免口令，应返回 JSON）")
        print(f"  控制台：   https://dashboard.render.com/web/{service_id}")
        print("=" * 70)
        print("提醒：免费实例闲置 15 分钟休眠，冷启动 30~60s；磁盘临时，记录已写飞书多维表格。")
        return 0
    print("[!] 超时未拿到地址，去控制台看：", f"https://dashboard.render.com/web/{service_id}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
