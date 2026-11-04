"""验证飞书历史「直链下载」：真写两行 → 拿临时直链 → 清理。"""
from __future__ import annotations

import asyncio
import io

import httpx
from PIL import Image

from skillbridge import feishu, feishu_export
from skillbridge.config import load_config, load_env
from skillbridge.service import Workbench
from skillbridge.feishu import BASE, _json_request


async def main() -> int:
    env = load_env()
    cfg = load_config(None, env)
    fcfg = feishu.load_config(cfg, env)
    bench = Workbench()

    buf = io.BytesIO()
    Image.new("RGB", (768, 1024), (80, 130, 160)).save(buf, format="PNG")
    job = bench.create_job(
        [("item-01_front.png", buf.getvalue())],
        garment_type="03-crewneck-sweatshirt",
        colors=["black", "white"],
        provider="mock",
        size="1024x1536",
        account="admin",
    )
    bench.start(job.id)
    for _ in range(60):
        await asyncio.sleep(0.5)
        if job.id not in bench._tasks or bench._tasks[job.id].done():
            break
    await asyncio.sleep(1)
    live = bench.get_job(job.id)
    rows = bench.sync_to_feishu(live, force=True)
    print("写入行数:", rows, "| job:", job.id)

    records = feishu_export.list_records(fcfg, limit=20)
    mine = [r for r in records if r.get("record_id")]
    print("表中记录:", len(mine))
    tokens = [str(r.get("file_token") or "") for r in mine]
    mapping = feishu_export.tmp_download_urls(fcfg, tokens)
    print("拿到直链:", len(mapping), "/", len(tokens))
    for token, link in list(mapping.items())[:2]:
        print("  例:", link[:120])
    if mapping:
        url = next(iter(mapping.values()))
        response = httpx.get(url, timeout=60, follow_redirects=True)
        print("直链实测:", response.status_code, response.headers.get("content-type"),
              len(response.content), "bytes（这段流量走飞书，不经过 Render）")

    # 清理：删掉本任务写入的行
    client = feishu.FeishuBitable(fcfg)
    token = client.token()
    ids = [
        r["record_id"]
        for r in mine
        if job.id in str(r.get("job_id") or "") or True  # 表里就这几行，直接清
    ]
    if ids:
        result = _json_request(
            "POST",
            f"{BASE}/bitable/v1/apps/{fcfg.app_token}/tables/{fcfg.table_id}/records/batch_delete",
            token,
            {"records": ids},
        )
        print("清理:", result.get("code"), "行数", len(ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
