"""在多维表格里创建「模型定价」「账号」两张表，并灌入初始数据（幂等）。

    python deploy/setup_feishu_tables.py --app-id cli_xxx --app-secret xxx \
        --app-token Cg19bjnLJaAU7CsYPB5cO3qynrb \
        --admin 你的登录名 --admin-password 你的密码

做的事：
  1. 建「模型定价」表（按次 / 1K / 2K / 4K / 其他档 单价 + 启用开关），从 pricing.json 导入现有价格
  2. 建「账号」表（账号 / 显示名 / 密码哈希 / 明文密码 / 角色 / 状态 / 额度 / 备注 / 创建时间）
  3. 建一个管理员账号（密码用 PBKDF2-SHA256 存哈希，表里同时留「明文密码」列方便人工加号）
  4. 给现有记录表补一个「账号」字段
  5. 打印两张表的链接

飞书里人工改动即可生效：改单价 → 工作台下次拉取就用新价；加一行账号 → 就能登录。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://open.feishu.cn/open-apis"
ROOT = Path(__file__).resolve().parent.parent

PRICE_FIELDS = [
    ("模型", 1),
    ("单价元", 2),
    ("1K档", 2),
    ("2K档", 2),
    ("4K档", 2),
    ("其他档", 2),
    ("启用", 1),
    ("备注", 1),
]
ACCOUNT_FIELDS = [
    ("账号", 1),
    ("显示名", 1),
    ("密码哈希", 1),
    ("明文密码", 1),
    ("角色", 1),
    ("状态", 1),
    ("额度元", 2),
    ("备注", 1),
    ("创建时间", 5),
]


def call(method: str, url: str, token: str = "", payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"code": exc.code, "msg": body[:300]}


def pbkdf2(password: str, salt: str | None = None, iterations: int = 260000) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def find_table(token: str, app_token: str, name: str) -> str | None:
    data = call("GET", f"{BASE}/bitable/v1/apps/{app_token}/tables?page_size=100", token)
    for item in (data.get("data") or {}).get("items") or []:
        if item.get("name") == name:
            return str(item.get("table_id"))
    return None


def create_table(token: str, app_token: str, name: str, schema: list[tuple[str, int]]) -> str:
    # 建表必须带上字段（至少主字段），否则报 field validation failed
    data = call(
        "POST",
        f"{BASE}/bitable/v1/apps/{app_token}/tables",
        token,
        {
            "table": {
                "name": name,
                "default_view_name": "表格",
                "fields": [{"field_name": n, "type": t} for n, t in schema],
            }
        },
    )
    if data.get("code") != 0:
        raise SystemExit(f"[x] 建表失败 {name}: {data.get('msg')}")
    return str((data.get("data") or {}).get("table_id") or "")


def ensure_fields(token: str, app_token: str, table_id: str, schema: list[tuple[str, int]]) -> None:
    current = call(
        "GET",
        f"{BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields?page_size=100",
        token,
    )
    have = {str(x.get("field_name")): x for x in (current.get("data") or {}).get("items") or []}
    # 新表自带一个主字段（文本），把它重命名成 schema 的第一项
    first_name, first_type = schema[0]
    if first_name not in have:
        primary = next((x for x in have.values() if x.get("is_primary")), None)
        if primary and primary.get("type") == first_type:
            call(
                "PUT",
                f"{BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{primary['field_id']}",
                token,
                {"field_name": first_name, "type": first_type},
            )
            have[first_name] = {**primary, "field_name": first_name}
    for name, ftype in schema:
        if name in have:
            continue
        result = call(
            "POST",
            f"{BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token,
            {"field_name": name, "type": ftype},
        )
        print(f"    {'ok ' if result.get('code') == 0 else 'x  '}字段 {name} (type {ftype})")


def create_records(token: str, app_token: str, table_id: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    data = call(
        "POST",
        f"{BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
        token,
        {"records": [{"fields": row} for row in rows]},
    )
    if data.get("code") != 0:
        print(f"    x 写入失败：{data.get('msg')}")
        return 0
    return len((data.get("data") or {}).get("records") or [])


def main() -> int:
    parser = argparse.ArgumentParser(description="创建定价表/账号表并灌初始数据")
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--app-secret", required=True)
    parser.add_argument("--app-token", required=True)
    parser.add_argument("--record-table-id", default="", help="已有的记录表，用来补「账号」字段")
    parser.add_argument("--admin", default="admin")
    parser.add_argument("--admin-password", default="")
    parser.add_argument("--price-from", default=str(ROOT / "pricing.json"))
    args = parser.parse_args()

    auth = call(
        "POST",
        f"{BASE}/auth/v3/tenant_access_token/internal",
        payload={"app_id": args.app_id, "app_secret": args.app_secret},
    )
    if auth.get("code") != 0:
        print(f"[x] 鉴权失败：{auth.get('msg')}")
        return 1
    token = auth["tenant_access_token"]

    # ---------------- 模型定价 ----------------
    price_table = find_table(token, args.app_token, "模型定价") or create_table(
        token, args.app_token, "模型定价", PRICE_FIELDS
    )
    print(f"[*] 模型定价表：{price_table}")
    ensure_fields(token, args.app_token, price_table, PRICE_FIELDS)

    existing = call(
        "POST",
        f"{BASE}/bitable/v1/apps/{args.app_token}/tables/{price_table}/records/search?page_size=200",
        token,
        {"automatic_fields": False},
    )
    have_models = set()
    for item in (existing.get("data") or {}).get("items") or []:
        name = item.get("fields", {}).get("模型")
        if isinstance(name, list) and name:
            first = name[0]
            name = first.get("text") if isinstance(first, dict) else first
        if name:
            have_models.add(str(name))

    pricing_file = Path(args.price_from)
    rows: list[dict] = []
    if pricing_file.is_file():
        payload = json.loads(pricing_file.read_text(encoding="utf-8"))
        for model, entry in (payload.get("models") or {}).items():
            if model in have_models:
                continue
            row: dict = {"模型": model, "启用": "是", "备注": "导入自 pricing.json"}
            if isinstance(entry, dict):
                tiers = entry.get("tiers") or {}
                row["1K档"] = tiers.get("1K")
                row["2K档"] = tiers.get("2K")
                row["4K档"] = tiers.get("4K")
                row["其他档"] = entry.get("other")
                row["单价元"] = tiers.get("1K") or entry.get("other")
            elif entry is not None:
                row["单价元"] = float(entry)
            else:
                row["备注"] = "未定价（pricing.json 里是 null）"
            rows.append(row)
    created = create_records(token, args.app_token, price_table, rows)
    print(f"[*] 定价表写入 {created} 行（已存在 {len(have_models)} 个模型）")

    # ---------------- 账号 ----------------
    account_table = find_table(token, args.app_token, "账号") or create_table(
        token, args.app_token, "账号", ACCOUNT_FIELDS
    )
    print(f"[*] 账号表：{account_table}")
    ensure_fields(token, args.app_token, account_table, ACCOUNT_FIELDS)

    existing_accounts = call(
        "POST",
        f"{BASE}/bitable/v1/apps/{args.app_token}/tables/{account_table}/records/search?page_size=200",
        token,
        {"automatic_fields": False},
    )
    names = set()
    for item in (existing_accounts.get("data") or {}).get("items") or []:
        value = item.get("fields", {}).get("账号")
        if isinstance(value, list) and value:
            first = value[0]
            value = first.get("text") if isinstance(first, dict) else first
        if value:
            names.add(str(value))

    if args.admin not in names:
        password = args.admin_password or secrets.token_urlsafe(9)
        row = {
            "账号": args.admin,
            "显示名": args.admin,
            "密码哈希": pbkdf2(password),
            "明文密码": "",
            "角色": "admin",
            "状态": "enabled",
            "额度元": 0,
            "备注": "初始化管理员",
            "创建时间": int(time.time() * 1000),
        }
        if create_records(token, args.app_token, account_table, [row]):
            print(f"[*] 已创建管理员 `{args.admin}`，初始密码：{password}")
            print("    （登录后建议在飞书表格里把「明文密码」清空，只保留哈希）")
    else:
        print(f"[*] 管理员 `{args.admin}` 已存在，跳过")

    # ---------------- 记录表补「账号」字段 ----------------
    if args.record_table_id:
        ensure_fields(token, args.app_token, args.record_table_id, [("账号", 1)])
        print("[*] 记录表已补齐「账号」字段")

    print()
    print("=" * 74)
    print(f"  模型定价：https://feishu.cn/base/{args.app_token}?table={price_table}")
    print(f"  账号    ：https://feishu.cn/base/{args.app_token}?table={account_table}")
    if args.record_table_id:
        print(f"  记录    ：https://feishu.cn/base/{args.app_token}?table={args.record_table_id}")
    print("=" * 74)
    print("下一步：把这两张表的 table_id 填进 config.json → feishu.price_table_id / account_table_id")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
