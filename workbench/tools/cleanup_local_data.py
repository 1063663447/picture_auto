"""清本地产生的数据（成品图、缩略图、ZIP、任务日志）。

现在整套跑在本机，`data/jobs` 会越来越占盘。这个脚本按时间清理：

    python tools/cleanup_local_data.py --days 30 --dry-run     # 看会删什么
    python tools/cleanup_local_data.py --days 30               # 删掉 30 天前的任务
    python tools/cleanup_local_data.py --derived-only           # 只清缩略图/压缩版缓存
    python tools/cleanup_local_data.py --drafts                 # 清草稿

注意：删掉任务目录 = 本地看不到这些成品了；飞书里（记录 + 附件）仍然保留。
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for file in path.rglob("*"):
        if file.is_file():
            try:
                total += file.stat().st_size
            except OSError:
                pass
    return total


def human(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{num} B"
        num /= 1024.0
    return f"{num:.1f} GB"


def main() -> int:
    parser = argparse.ArgumentParser(description="清理本地 data/（成品/缓存/草稿）")
    parser.add_argument("--days", type=float, default=30, help="保留最近 N 天的任务")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--derived-only", action="store_true", help="只清缩略图/压缩版")
    parser.add_argument("--drafts", action="store_true", help="清掉上传草稿")
    args = parser.parse_args()

    jobs_dir = DATA / "jobs"
    total_before = size_of(DATA)
    print(f"data/ 当前占用：{human(total_before)}")

    removed = 0
    freed = 0

    if args.derived_only:
        for cache in list(jobs_dir.glob("*/thumbs")) + list(jobs_dir.glob("*/derived")):
            size = size_of(cache)
            print(f"  {'[dry] ' if args.dry_run else ''}清缓存 {cache.relative_to(DATA)} {human(size)}")
            if not args.dry_run:
                shutil.rmtree(cache, ignore_errors=True)
            removed += 1
            freed += size
    else:
        cutoff = time.time() - args.days * 86400
        for job_dir in sorted(jobs_dir.glob("*")):
            if not job_dir.is_dir():
                continue
            try:
                stamp = job_dir.stat().st_mtime
            except OSError:
                continue
            if stamp >= cutoff:
                continue
            size = size_of(job_dir)
            print(
                f"  {'[dry] ' if args.dry_run else ''}删任务 {job_dir.name} "
                f"（{time.strftime('%Y-%m-%d', time.localtime(stamp))}，{human(size)}）"
            )
            if not args.dry_run:
                shutil.rmtree(job_dir, ignore_errors=True)
            removed += 1
            freed += size

    if args.drafts:
        drafts = DATA / "drafts"
        if drafts.is_dir():
            size = size_of(drafts)
            print(f"  {'[dry] ' if args.dry_run else ''}清草稿 {human(size)}")
            if not args.dry_run:
                shutil.rmtree(drafts, ignore_errors=True)
            removed += 1
            freed += size

    total_after = size_of(DATA) if not args.dry_run else total_before - freed
    print(f"\n{'[dry-run] ' if args.dry_run else ''}处理 {removed} 项，释放 {human(freed)}")
    print(f"data/ 之后占用：{human(max(0, total_after))}")
    if args.dry_run:
        print("（加 --dry-run 只是预览，去掉就真删）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
