"""修复历史任务的打包问题，并重新用 skill 的脚本打包。

背景（曾导致 `06 连帽卫衣 3 色` 整批失败）：
缩略图/压缩版缓存一度生成在 `<job>/staging/thumbs`、`<job>/staging/derived`，
而 skill 的 `package_outputs.py` 把 staging 下的**每个子目录**当成一个 item 数图，
于是报 `Item directory ...\\staging\\thumbs contains 2 images; expected 3.`。

新版把缓存写在 `<job>/thumbs`、`<job>/derived`。这个脚本用来：

1. 把历史任务里错位的缓存目录搬回任务根目录（`staging/thumbs` → `thumbs`）；
2. 重新调用 skill 的打包脚本，把 ZIP 补出来（可选 `--repack`，默认就做）。

用法：
    python tools/repair_job.py --dry-run                 # 只看看有哪些任务需要修
    python tools/repair_job.py --all                     # 修全部 + 重新打包
    python tools/repair_job.py --job 20261104-143344-4d28
    python tools/repair_job.py --all --move-only          # 只搬目录，不打包
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillbridge.job import Job  # noqa: E402
from skillbridge.packaging import package_outputs  # noqa: E402

CACHE_DIRS = ("thumbs", "derived", "exports", ".cache")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def load_config(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def move_caches(root: Path, dry: bool) -> list[str]:
    """把 staging/ 下错位的缓存目录搬到任务根目录，返回搬了哪些。"""
    staging = root / "staging"
    moved: list[str] = []
    if not staging.is_dir():
        return moved
    for name in CACHE_DIRS:
        stray = staging / name
        if not stray.is_dir():
            continue
        target = root / name
        moved.append(f"{stray.relative_to(root)} → {target.relative_to(root)}")
        if dry:
            continue
        target.mkdir(parents=True, exist_ok=True)
        for item in stray.iterdir():
            dest = target / item.name
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True) if dest.is_dir() else dest.unlink()
            item.replace(dest)
        shutil.rmtree(stray, ignore_errors=True)
    return moved


def stray_items(root: Path) -> list[str]:
    """staging 下被当成 item、但其实不是成品目录的名字（例如空目录/缓存）。"""
    staging = root / "staging"
    if not staging.is_dir():
        return []
    out = []
    for path in sorted(p for p in staging.iterdir() if p.is_dir()):
        images = [
            f for f in path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES
        ]
        if not images:
            out.append(path.name)
        elif path.name in CACHE_DIRS:
            out.append(f"{path.name}（{len(images)} 张缓存图）")
    return out


async def repack(job: Job, skill_root: Path, max_bytes: int) -> tuple[bool, str]:
    root = job.root
    assert root is not None
    staging = root / "staging"
    if not staging.is_dir():
        return False, "没有 staging 目录"
    produced = [
        p for p in staging.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    ]
    if not produced:
        return False, "staging 里没有图片"
    batch = job.batch_name or str(job.params.get("garment_name") or "batch")
    batch = batch.replace(" ", "-")
    result = await package_outputs(
        script=skill_root / "scripts" / "package_outputs.py",
        staging_dir=staging,
        output_dir=root / "outputs",
        batch_name=batch,
        max_bytes=max_bytes,
    )
    if not result.ok:
        return False, str(result.error or "打包失败")
    job.batch_name = batch
    if result.zip_path:
        job.zip_rel = str(result.zip_path.relative_to(root)).replace("\\", "/")
    job.dir_rel = str((root / "outputs" / batch).relative_to(root)).replace("\\", "/")
    counts = job.counts()
    if counts["done"] and not counts["failed"]:
        job.status = "done"
        job.stage = f"完成：{counts['done']} 张（已重新打包）"
    elif counts["done"]:
        job.status = "partial"
        job.stage = f"完成：{counts['done']} 张成功，{counts['failed']} 张失败（已重新打包）"
    else:
        job.status = "failed"
        job.stage = "没有成功的成品"
    job.error = None
    job.updated_at = __import__("time").time()
    job.add_log(f"修复脚本重新打包完成：{batch}.zip（{len(produced)} 张）")
    job.save()
    return True, str(result.zip_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="修复历史任务的打包失败")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--data", default="", help="覆盖 data 目录")
    parser.add_argument("--job", default="", help="只修这一个任务 ID")
    parser.add_argument("--all", action="store_true", help="修 data/jobs 下全部任务")
    parser.add_argument("--move-only", action="store_true", help="只搬缓存目录，不重新打包")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    here = Path(__file__).resolve().parents[1]
    config = load_config(here / args.config)
    data_dir = Path(args.data or config.get("data_dir") or "data")
    if not data_dir.is_absolute():
        data_dir = (here / data_dir).resolve()
    skill_root = Path(config.get("skill_root") or "../apparel-style-recolor")
    if not skill_root.is_absolute():
        skill_root = (here / skill_root).resolve()
    defaults = config.get("defaults") or {}
    max_bytes = int(defaults.get("max_bytes") or 2_000_000)

    jobs_dir = data_dir / "jobs"
    if not jobs_dir.is_dir():
        print(f"没有任务目录：{jobs_dir}")
        return 1

    if args.job:
        targets = [jobs_dir / args.job]
    elif args.all:
        targets = sorted(p for p in jobs_dir.iterdir() if (p / "job.json").is_file())
    else:
        # 默认：只挑「staging 里有疑似缓存目录」的任务
        targets = []
        for path in sorted(p for p in jobs_dir.iterdir() if (p / "job.json").is_file()):
            if stray_items(path):
                targets.append(path)

    if not targets:
        print("没有需要修复的任务")
        return 0

    print(f"data: {data_dir}")
    print(f"skill: {skill_root}")
    print(f"待处理 {len(targets)} 个任务" + ("（dry-run）" if args.dry_run else ""))
    failed = 0
    for path in targets:
        if not (path / "job.json").is_file():
            print(f"\n[{path.name}] 跳过：没有 job.json")
            continue
        job = Job.load(path / "job.json")
        print(f"\n[{job.id}] {job.status} · {job.stage}")
        strays = stray_items(path)
        if strays:
            print(f"  staging 里的可疑目录：{', '.join(strays)}")
        moved = move_caches(path, args.dry_run)
        for line in moved:
            print(f"  搬移 {line}")
        if not moved and not args.all:
            print("  无需搬移")
        if args.move_only or args.dry_run:
            continue
        ok, message = asyncio.run(repack(job, skill_root, max_bytes))
        print(("  重新打包 OK：" if ok else "  重新打包失败：") + message)
        if not ok:
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
