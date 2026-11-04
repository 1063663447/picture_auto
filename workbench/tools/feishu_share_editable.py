"""把飞书多维表格的链接分享权限从「组织内可阅读」改成「组织内可编辑」。

接口：PATCH /open-apis/drive/v1/permissions/{token}/public?type=bitable
  body: {"link_share_entity": "tenant_editable"}
可选值：closed / tenant_readable / tenant_editable / partner_tenant_readable /
        partner_tenant_editable / anyone_readable / anyone_editable

用法：
  python tools/feishu_share_editable.py --check           # 只看当前设置
  python tools/feishu_share_editable.py                   # 改成组织内可编辑
  python tools/feishu_share_editable.py --mode tenant_readable   # 改回只读
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import feishu  # noqa: E402
from skillbridge.config import load_config, load_env  # noqa: E402
from skillbridge.feishu import BASE, _json_request  # noqa: E402


def get_public(token: str, app_token: str) -> dict:
    return _json_request(
        "GET", f"{BASE}/drive/v1/permissions/{app_token}/public?type=bitable", token
    )


def patch_public(token: str, app_token: str, payload: dict) -> dict:
    return _json_request(
        "PATCH",
        f"{BASE}/drive/v1/permissions/{app_token}/public?type=bitable",
        token,
        payload,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="设置多维表格链接分享权限")
    parser.add_argument("--mode", default="tenant_editable", help="目标 link_share_entity")
    parser.add_argument("--check", action="store_true", help="只读当前设置")
    parser.add_argument("--app-token", default="", help="默认取 config.json 里的")
    args = parser.parse_args(argv)

    env = load_env()
    cfg = load_config(None, env)
    fcfg = feishu.load_config(cfg, env)
    app_token = args.app_token or fcfg.app_token
    client = feishu.FeishuBitable(fcfg)
    token = client.token()

    before = get_public(token, app_token)
    print("[当前]", json.dumps(before, ensure_ascii=False)[:600])
    current = ((before.get("data") or {}).get("permission_public") or {}).get("link_share_entity")
    if args.check:
        print(f"[i] 当前 link_share_entity = {current}")
        want = "tenant_editable"
        print("[ok] 已是组织内可编辑" if current == want else f"[!] 需要改成 {want}")
        return 0

    result = patch_public(token, app_token, {"link_share_entity": args.mode})
    print("[PATCH]", json.dumps(result, ensure_ascii=False)[:400])
    after = get_public(token, app_token)
    new = ((after.get("data") or {}).get("permission_public") or {}).get("link_share_entity")
    ok = new == args.mode
    print(f"[{'ok' if ok else 'x'}] link_share_entity: {current} -> {new}")
    if not ok:
        print("    若报权限错：应用需要该多维表格的「可管理」权限（协作者里设成可管理）")
        return 1
    print(f"[i] 表格链接：https://feishu.cn/base/{app_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
