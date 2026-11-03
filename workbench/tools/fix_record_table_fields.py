"""修复：记录表主字段曾被误改名（成品 → 账号）。

`setup_feishu_tables.py` 里 ensure_fields 会把「主字段」改名成 schema 第一项，
而给记录表补字段时 schema 只有 [("账号", 1)]，于是主字段「成品」被改成了「账号」。
这里把主字段改回「成品」，并新建一个空的「账号」字段。
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="把记录表主字段改回「成品」并新建「账号」")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    env = load_env()
    cfg = load_config(None, env)
    fcfg = feishu.load_config(cfg, env)
    client = feishu.FeishuBitable(fcfg)
    token = client.token()
    app_token, table_id = fcfg.app_token, fcfg.table_id

    fields = _json_request(
        "GET", f"{BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields?page_size=100", token
    )
    items = (fields.get("data") or {}).get("items") or []
    primary = next((x for x in items if x.get("is_primary")), None)
    print("主字段：", primary.get("field_name"), primary.get("field_id"), "type", primary.get("type"))
    print("全部字段：", [x.get("field_name") for x in items])

    changed = False
    renamed = False
    if primary and primary.get("field_name") == "账号":
        if args.dry_run:
            print("[dry] 会把主字段 账号 → 成品")
            renamed = True
        else:
            result = _json_request(
                "PUT",
                f"{BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{primary['field_id']}",
                token,
                {"field_name": "成品", "type": 1},
            )
            print("[ok] 主字段改回「成品」" if result.get("code") == 0
                  else f"[x] 改名失败 {result.get('code')} {result.get('msg')}")
            renamed = changed = result.get("code") == 0

    has_account = any(
        x.get("field_name") == "账号" and not (renamed and x.get("field_id") == primary.get("field_id"))
        for x in items
    )
    if not has_account:
        if args.dry_run:
            print("[dry] 会新建「账号」字段")
        else:
            result = _json_request(
                "POST",
                f"{BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                token,
                {"field_name": "账号", "type": 1},
            )
            print("[ok] 新建「账号」字段" if result.get("code") == 0
                  else f"[x] 建字段失败 {result.get('code')} {result.get('msg')}")
    else:
        print("[=] 已有独立「账号」字段，不用新建")

    if changed or not args.dry_run:
        rows = client.search_table(table_id, page_size=3)
        for row in rows[:2]:
            fields_row = row.get("fields") or {}
            print("   行样本 成品=", json.dumps(fields_row.get("成品"), ensure_ascii=False),
                  " 账号=", json.dumps(fields_row.get("账号"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
