"""端到端：用 mock 接口跑一个任务，检查「账号」是否写进任务与飞书记录。

用法：python tools/check_account_e2e.py                      # 不写飞书（默认）
      python tools/check_account_e2e.py --sync               # 同时验证飞书写入
"""
from __future__ import annotations

import asyncio
import io
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from skillbridge.service import Workbench  # noqa: E402


def png_bytes(size=(512, 512), color=(228, 70, 70)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def main(argv=None) -> int:
    return asyncio.run(_main(argv))


async def _main(argv=None) -> int:
    sync = "--sync" in (argv if argv is not None else sys.argv[1:])
    bench = Workbench()
    slug = next(iter(bench.skill.types))
    job = bench.create_job(
        [("item-01_front.png", png_bytes())],
        garment_type=slug,
        colors=None,
        provider="mock",
        model="mock-model",
        concurrency=1,
        size="1024x1024",
        account="admin",
    )
    print("job:", job.id, "tasks:", len(job.tasks), "account:", job.params.get("account"))
    bench.start(job.id)
    for _ in range(120):
        await asyncio.sleep(0.5)
        live = bench.get_job(job.id)
        if live.status in {"done", "partial", "failed", "canceled"}:
            break
    live = bench.get_job(job.id)
    print("status:", live.status, live.counts())
    entry = bench.record_usage(live)
    print("ledger:", (entry or {}).get("account"), (entry or {}).get("images"),
          (entry or {}).get("cost"))

    if sync:
        cfg = bench.feishu_config
        client_mod = __import__("skillbridge.feishu", fromlist=["FeishuBitable"])
        client = client_mod.FeishuBitable(cfg)
        # 先清掉本次测试可能重复的行：只看本任务
        rows = client.search_table(cfg.table_id, page_size=200, max_pages=3)
        mine = []
        for item in rows:
            fields = item.get("fields") or {}
            tid = fields.get("任务ID")
            tid = tid[0]["text"] if isinstance(tid, list) and tid else tid
            if str(tid or "") == live.id:
                mine.append(item)
        print("existing rows for this job:", len(mine))
        if not mine:
            n = bench.sync_to_feishu(live)
            print("feishu rows written:", n)
            rows = client.search_table(cfg.table_id, page_size=200, max_pages=3)
            for item in rows:
                fields = item.get("fields") or {}
                tid = fields.get("任务ID")
                tid = tid[0]["text"] if isinstance(tid, list) and tid else tid
                if str(tid or "") == live.id:
                    acc = fields.get("账号")
                    acc = acc[0]["text"] if isinstance(acc, list) and acc else acc
                    print("  row 账号 =", acc, "| 成品 =",
                          (fields.get("成品") or [{}])[0].get("text"))
        usage = bench.accounts_payload()
        print("usage_source:", usage["usage_source"])
        for row in usage["accounts"]:
            print("  ", row["username"], row["images"], row["cost"], row["jobs"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
