"""一键把多维表格整理成工作台要用的结构（幂等，可重复执行）。

做的事：
  1. 重命名默认字段（文本→成品、附件→成品图、日期→时间）
  2. 删掉默认的「单选」字段（改成文本字段更省事，不用维护选项）
  3. 补齐缺的字段（含 3 个附件字段：成品图 / 参考图 / 原图）
  4. 打印表格链接与当前字段清单

用法：
    python deploy/setup_feishu_table.py \
      --app-id cli_xxx --app-secret xxx \
      --app-token Cg19bjnLJaAU7CsYPB5cO3qynrb --table-id tblvrb2DVxO7s28c
    # 只体检不改动：加 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://open.feishu.cn/open-apis"

#: 目标结构：字段名 → 飞书类型（1 文本 / 2 数字 / 5 日期 / 17 附件）
TARGET_FIELDS: list[tuple[str, int]] = [
    ("成品", 1),        # 主字段：item-01 / 黑色
    ("任务ID", 1),
    ("时间", 5),
    ("款式", 1),
    ("颜色", 1),
    ("模型", 1),
    ("尺寸", 1),
    ("张数", 2),
    ("单价元", 2),
    ("金额元", 2),
    ("接口", 1),
    ("状态", 1),
    ("备注", 1),
    ("成品图", 17),
    ("参考图", 17),
    ("原图", 17),
]

#: 默认字段的重命名计划：原字段名 → 新字段名
RENAMES = {"文本": "成品", "附件": "成品图", "日期": "时间"}
#: 默认字段里要删掉的（单选选项维护成本高，统一用文本）
DROP = {"单选"}


def call(method: str, url: str, token: str = "", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"code": exc.code, "msg": body[:300]}


def main() -> int:
    parser = argparse.ArgumentParser(description="整理飞书多维表格结构")
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--app-secret", required=True)
    parser.add_argument("--app-token", required=True)
    parser.add_argument("--table-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = call(
        "POST",
        f"{BASE}/auth/v3/tenant_access_token/internal",
        payload={"app_id": args.app_id, "app_secret": args.app_secret},
    )
    if token.get("code") != 0:
        print(f"[x] 鉴权失败：{token.get('msg')}")
        return 1
    token = token["tenant_access_token"]
    fields_url = f"{BASE}/bitable/v1/apps/{args.app_token}/tables/{args.table_id}/fields"

    current = call("GET", f"{fields_url}?page_size=100", token)
    if current.get("code") != 0:
        print(f"[x] 读字段失败：{current.get('code')} {current.get('msg')}")
        return 1
    have = {item["field_name"]: item for item in (current.get("data") or {}).get("items") or []}
    print(f"[*] 现有 {len(have)} 个字段：{', '.join(have)}")

    # 1) 重命名
    for old, new in RENAMES.items():
        if old in have and new not in have:
            item = have[old]
            if args.dry_run:
                print(f"    (dry) 重命名 {old} → {new}")
                continue
            result = call(
                "PUT", f"{fields_url}/{item['field_id']}", token, {"field_name": new, "type": item["type"]}
            )
            print(f"    {'ok ' if result.get('code') == 0 else 'x  '}重命名 {old} → {new} {result.get('msg')}")
            if result.get("code") == 0:
                have[new] = {**item, "field_name": new}
                have.pop(old, None)

    # 2) 删除默认不要的字段
    for name in DROP:
        if name in have:
            item = have[name]
            if args.dry_run:
                print(f"    (dry) 删除 {name}")
                continue
            result = call("DELETE", f"{fields_url}/{item['field_id']}", token)
            print(f"    {'ok ' if result.get('code') == 0 else 'x  '}删除 {name} {result.get('msg')}")
            if result.get("code") == 0:
                have.pop(name, None)

    # 3) 补字段
    for name, ftype in TARGET_FIELDS:
        if name in have:
            continue
        if args.dry_run:
            print(f"    (dry) 新建 {name} (type {ftype})")
            continue
        result = call("POST", fields_url, token, {"field_name": name, "type": ftype})
        ok = result.get("code") == 0
        print(f"    {'ok ' if ok else 'x  '}新建 {name} (type {ftype}) {result.get('msg')}")
        if ok:
            have[name] = result.get("data", {}).get("field", {})

    print()
    print("=" * 66)
    print(f"  表格：https://feishu.cn/base/{args.app_token}?table={args.table_id}")
    print(f"  字段（{len(TARGET_FIELDS)} 个）：")
    for name, ftype in TARGET_FIELDS:
        mark = "✓" if name in have else "✗"
        print(f"    {mark} {name}  (type {ftype})")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
