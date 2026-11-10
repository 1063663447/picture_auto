# -*- coding: utf-8 -*-
"""清掉界面验收（fixture）写进飞书「记录」表的测试行。

验收时 fixture 任务会照真实配置写飞书，跑完把这几行删掉，
免得运营在记录表里看到 job id 带 `-revw` 的假数据。

用法：python tools/purge_fixture_rows.py [--dry-run]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillbridge import feishu as feishu_mod  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

MARKERS = ("-revw", "revw")


def main() -> int:
    dry = "--dry-run" in sys.argv
    bench = Workbench()
    cfg = feishu_mod.load_config(bench.config, bench.env)
    ok, reason = cfg.ready()
    if not ok:
        print(f"飞书未就绪：{reason}")
        return 2
    client = feishu_mod.FeishuBitable(cfg)
    table = cfg.table_id
    page_token = ""
    hit: list[dict] = []
    total = 0
    while True:
        path = f"/bitable/v1/apps/{cfg.app_token}/tables/{table}/records?page_size=200"
        if page_token:
            path += f"&page_token={page_token}"
        data = client._api("GET", path)          # noqa: SLF001 - 只读分页
        items = data.get("items") or []
        total += len(items)
        for row in items:
            text = str(row)
            if any(marker in text for marker in MARKERS):
                hit.append(row)
        page_token = str(data.get("page_token") or "")
        if not data.get("has_more") or not page_token:
            break
    print(f"记录表 {table} 共 {total} 行，命中测试数据 {len(hit)} 行")
    for row in hit:
        record_id = str(row.get("record_id") or "")
        fields = row.get("fields") or {}
        label = " / ".join(
            f"{key}={str(fields.get(key))[:40]}"
            for key in ("任务号", "款式", "颜色", "批次")
            if key in fields
        )
        print(f"  - {record_id} {label}")
        if dry or not record_id:
            continue
        try:
            client.delete_record(table, record_id)
            print("    已删除")
        except Exception as exc:  # noqa: BLE001
            print(f"    删除失败：{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
