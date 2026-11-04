"""真跑一个 mock 任务，看飞书同步到底有没有写、报了什么错。"""
from __future__ import annotations

import asyncio
import io

from PIL import Image

from skillbridge.service import Workbench


async def main() -> int:
    bench = Workbench()
    buf = io.BytesIO()
    Image.new("RGB", (768, 1024), (90, 120, 90)).save(buf, format="PNG")
    job = bench.create_job(
        [("item-01_front.png", buf.getvalue())],
        garment_type="03-crewneck-sweatshirt",
        colors=["black"],
        provider="mock",
        size="1024x1536",
        account="admin",
    )
    print("job:", job.id, "tasks:", len(job.tasks))
    bench.start(job.id)
    for _ in range(120):
        await asyncio.sleep(0.5)
        if job.id not in bench._tasks or bench._tasks[job.id].done():
            break
    await asyncio.sleep(4)      # 等 _record_after 里的飞书同步（含附件上传）跑完
    live = bench.get_job(job.id)
    print("status:", live.status, live.counts())
    print("feishu_rows:", live.params.get("feishu_rows"), "| table:", live.params.get("feishu_table"))
    for entry in live.log[-10:]:
        print(f"  [{entry['level']}] {entry['msg'][:160]}")
    print("\n直接再调一次 sync_to_feishu(force=True)：")
    count = bench.sync_to_feishu(live, force=True)
    print("写得行数:", count)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
