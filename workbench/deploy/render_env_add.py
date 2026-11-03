"""给 Render 服务补一个环境变量（保留现有变量），并触发一次部署。

用法：python deploy/render_env_add.py --api-key rnd_xxx --service srv-xxx --key K --value V [--deploy]
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import urllib.error
import urllib.request

API = "https://api.render.com/v1"


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render 环境变量补丁（保留已有）")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--key", action="append", default=[], help="KEY=VALUE，可重复")
    parser.add_argument("--generate", action="append", default=[], help="生成随机值的 KEY")
    parser.add_argument("--deploy", action="store_true", help="改完触发一次部署")
    parser.add_argument("--show", action="store_true", help="只打印现有变量名")
    args = parser.parse_args(argv)

    status, current = call("GET", f"/services/{args.service}/env-vars?limit=100", args.api_key)
    if status != 200:
        print(f"[x] 读环境变量失败：{status} {current}")
        return 1
    merged: dict[str, str] = {}
    for item in current or []:
        row = item.get("envVar") or item
        merged[str(row.get("key"))] = "" if row.get("generateValue") else str(row.get("value") or "")
    print("现有变量：", ", ".join(sorted(merged)))

    if args.show:
        return 0

    changed = False
    for spec in args.key:
        if "=" not in spec:
            print(f"[!] 跳过无法解析的 --key {spec}")
            continue
        name, value = spec.split("=", 1)
        merged[name.strip()] = value
        changed = True
        print(f"[ok] {name.strip()} = (已设置，{len(value)} 字符)")
    for name in args.generate:
        if name in merged and merged[name]:
            print(f"[=] {name} 已存在，保持不变")
            continue
        merged[name] = secrets.token_urlsafe(32)
        changed = True
        print(f"[ok] {name} = (随机生成)")

    if not changed:
        print("[=] 没有需要改的变量")
        return 0

    payload = [{"key": k, "value": v} for k, v in merged.items()]
    status, data = call("PUT", f"/services/{args.service}/env-vars", args.api_key, payload)
    if status not in (200, 201):
        print(f"[x] 写入失败：{status} {data}")
        return 1
    print(f"[ok] 已写入 {len(payload)} 个变量")

    if args.deploy:
        status, data = call("POST", f"/services/{args.service}/deploys", args.api_key, {"clearCache": "do_not_clear"})
        if status not in (200, 201, 202):
            print(f"[x] 触发部署失败：{status} {data}")
            return 1
        deploy_id = (data.get("deploy") or {}).get("id") or data.get("id")
        print(f"[ok] 已触发部署 {deploy_id}（改环境变量本身也会触发一次）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
