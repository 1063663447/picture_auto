"""Headless batch runner (no browser needed).

    python cli.py --input E:\\shoot\\today --garment-type 圆领卫衣 --provider gateway --concurrency 6
    python cli.py --input a.jpg b.jpg --garment-type hoodie --colors black white
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillbridge.runner import JobRunner  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def collect(paths: list[str]) -> list[tuple[str, bytes]]:
    uploads: list[tuple[str, bytes]] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.is_file() and child.suffix.lower() in IMAGE_SUFFIXES:
                    uploads.append((child.name, child.read_bytes()))
        elif path.is_file():
            uploads.append((path.name, path.read_bytes()))
        else:
            print(f"[!] 跳过不存在的路径：{path}", file=sys.stderr)
    return uploads


async def run(bench: Workbench, job_id: str) -> int:
    job = bench.get_job(job_id)
    runner = JobRunner(
        job,
        bench.skill,
        bench.skill.type_by_alias(job.params["garment_type"]),
        bench.skill.type_by_alias(job.params["garment_type"]).resolve_colors(job.params["colors"]),
        bench.get_provider(job.params["provider"]),
        data_dir=bench.data_dir,
    )
    started = time.monotonic()
    finished = asyncio.create_task(runner.run())

    last_line = ""
    while not finished.done():
        await asyncio.sleep(1.0)
        stats = job.counts()
        line = (
            f"\r[{stats['done'] + stats['skipped']}/{stats['total']}] "
            f"成功 {stats['done']} 失败 {stats['failed']} 运行 {stats['running']} "
            f"· {job.stage}"
        )
        if line != last_line:
            print(line.ljust(80), end="", flush=True)
            last_line = line
    await finished
    print()
    stats = job.counts()
    print(f"状态: {job.status} / {job.stage}")
    print(f"耗时: {time.monotonic() - started:.1f}s  成功 {stats['done']}  失败 {stats['failed']}")
    for task in job.tasks:
        if task.status == "failed":
            print(f"  [x] {task.id}: {task.error}")
    if job.zip_rel:
        print(f"ZIP: {job.root / job.zip_rel}")
    return 0 if job.status == "done" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="服装改款换色：命令行批量跑")
    parser.add_argument("--input", nargs="+", default=None, help="图片文件或目录")
    parser.add_argument("--garment-type", default=None, help="圆领卫衣 / 连帽卫衣 / 短袖")
    parser.add_argument("--colors", nargs="*", default=None, help="颜色标签，留空=全部颜色")
    parser.add_argument("--provider", default=None, help="config.json 里的 provider 名")
    parser.add_argument("--model", default=None, help="覆盖该接口的模型名（可用 --list-models 查看）")
    parser.add_argument("--list-models", action="store_true", help="只打印该接口的模型列表")
    parser.add_argument("--mode", default="single_pass",
                        choices=["single_pass", "two_phase", "recolor_only"])
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--rpm", type=int, default=None)
    parser.add_argument("--size", default="auto")
    parser.add_argument("--quality", default="high")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--retries", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--format", default="auto", choices=["auto", "png", "jpg", "webp"])
    parser.add_argument("--fidelity", default=None,
                        help="原图保真度（gpt-image 系列）：high / low；high 更能锁住图案")
    parser.add_argument("--no-reference", action="store_true", help="不把颜色参考图一起发送")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    uploads = collect(args.input) if args.input else []
    bench = Workbench(args.config)

    if args.list_models:
        data = asyncio.run(bench.list_models(args.provider, refresh=True))
        print(f"接口 {data['provider']}：{data['count']} 个模型（当前 {data['current'] or '—'}）")
        for model in data["models"]:
            mark = "●" if model["current"] else " "
            tag = "图片" if model["image"] else ("视频" if model["video"] else "文本/其它")
            print(f" {mark} {model['id']}   [{tag}]")
        return 0

    if not uploads:
        print("没有找到可处理的图片", file=sys.stderr)
        return 2

    job = bench.create_job(
        uploads,
        garment_type=args.garment_type,
        colors=args.colors,
        mode=args.mode,
        provider=args.provider,
        model=args.model,
        concurrency=args.concurrency,
        rpm=args.rpm,
        size=args.size,
        quality=args.quality,
        seed=args.seed,
        max_retries=args.retries,
        timeout_sec=args.timeout,
        output_format=args.format,
        send_reference=not args.no_reference,
        input_fidelity=args.fidelity,
    )
    print(f"任务 {job.id}：{len(job.params['colors'])} 色 × {len(uploads)} 图")
    return asyncio.run(run(bench, job.id))


if __name__ == "__main__":
    raise SystemExit(main())
