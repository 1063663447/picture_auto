"""账号表演示/自检工具：新增成员账号、查看哈希状态、删除账号。

用法：
  python tools/feishu_account_admin.py add tester "Test@2026" --role member --display 测试员
  python tools/feishu_account_admin.py list
  python tools/feishu_account_admin.py del tester
  python tools/feishu_account_admin.py passwd admin "新密码"
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import auth, feishu  # noqa: E402
from skillbridge.config import load_config, load_env  # noqa: E402


def _client():
    env = load_env()
    cfg = load_config(None, env)
    fcfg = feishu.load_config(cfg, env)
    return fcfg, feishu.FeishuBitable(fcfg)


def _text(value) -> str:
    if isinstance(value, list):
        return "".join(str(x.get("text") or "") if isinstance(x, dict) else str(x) for x in value)
    return "" if value is None else str(value)


def _records(client, fcfg) -> list[dict]:
    return client.search_table(fcfg.account_table_id, page_size=200)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="飞书「账号」表维护")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add")
    p_add.add_argument("username")
    p_add.add_argument("password")
    p_add.add_argument("--display", default="")
    p_add.add_argument("--role", default="member")
    p_add.add_argument("--quota", type=float, default=0.0)
    p_add.add_argument("--note", default="")

    sub.add_parser("list")

    p_del = sub.add_parser("del")
    p_del.add_argument("username")

    p_pw = sub.add_parser("passwd")
    p_pw.add_argument("username")
    p_pw.add_argument("password")

    args = parser.parse_args(argv)
    fcfg, client = _client()
    records = _records(client, fcfg)

    def find(name: str):
        for rec in records:
            if _text(rec["fields"].get("账号")).strip().lower() == name.strip().lower():
                return rec
        return None

    if args.cmd == "add":
        if find(args.username):
            print(f"[!] {args.username} 已存在，改用 passwd 改密码")
            return 1
        fields = {
            "账号": args.username,
            "显示名": args.display or args.username,
            "密码哈希": auth.hash_password(args.password),
            "明文密码": "",
            "角色": args.role,
            "状态": "启用",
            "额度元": args.quota,
            "备注": args.note or "由 feishu_account_admin.py 创建",
            "创建时间": int(time.time() * 1000),
        }
        client.create_records([fields], table_id=fcfg.account_table_id)
        print(f"[ok] 已新增账号 {args.username}（角色 {args.role}）")
        return 0

    if args.cmd == "list":
        for rec in records:
            f = rec["fields"]
            print(
                f"{_text(f.get('账号')):<12} 角色={_text(f.get('角色')):<8} "
                f"状态={_text(f.get('状态')):<6} 哈希={'有' if _text(f.get('密码哈希')) else '无'} "
                f"明文={'有' if _text(f.get('明文密码')) else '无'} 显示名={_text(f.get('显示名'))}"
            )
        return 0

    rec = find(args.username)
    if rec is None:
        print(f"[x] 没找到账号 {args.username}")
        return 1

    if args.cmd == "del":
        client._api(
            "DELETE",
            f"/bitable/v1/apps/{fcfg.app_token}/tables/{fcfg.account_table_id}/records/{rec['record_id']}",
        )
        print(f"[ok] 已删除账号 {args.username}")
        return 0

    if args.cmd == "passwd":
        client.update_record(
            fcfg.account_table_id,
            rec["record_id"],
            {"密码哈希": auth.hash_password(args.password), "明文密码": ""},
        )
        print(f"[ok] 已重置 {args.username} 的密码（PBKDF2 哈希）")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
