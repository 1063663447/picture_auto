"""备用接口并发实测：一次打 N 个（默认 50）真实请求，看会不会被限流。

测的是「接口本身能扛多少并发」——所以**不走飞书闸门、不走任务队列**，
直接对 provider 开火，这样 429 / 排队 / 超时都能看得一清二楚。

用法：
    python tools/check_backup_50.py --provider a6api --n 50
    python tools/check_backup_50.py --provider a6api --n 50 --keep 2   # 顺便存 2 张成品看效果

产物：
    data/test/a6api-<n>concurrency.json    每个请求的状态/耗时/错误
    data/test/a6api/ok-<i>.png              --keep 时保存的成品
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillbridge.config import load_config, load_env  # noqa: E402
from skillbridge.providers import build_providers  # noqa: E402
from skillbridge.providers.base import EditRequest  # noqa: E402
from skillbridge.runner import is_rate_limited  # noqa: E402

SRC = ROOT / "data" / "test" / "christmas-tee.jpg"
OUT_DIR = ROOT / "data" / "test" / "a6api"

PROMPT = (
    "Transform only the visible garment into a long-sleeve cotton-jersey T-shirt with flat "
    "plain cuffs and a straight flat hem, ribbed neckband only, and recolor the fabric base "
    "to white (#F0EFED). Keep the printed artwork identical: same letterforms, size, position, "
    "relief and photographic grain."
)


async def burst(provider, n: int, keep: int) -> list[dict]:
    """同时发 n 个请求，记录每个的耗时/结果。"""
    data = SRC.read_bytes()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    await provider.open()
    results: list[dict] = []
    lock = asyncio.Lock()
    inflight = 0
    peak = 0
    saved = 0

    async def one(index: int) -> None:
        nonlocal inflight, peak, saved
        request = EditRequest(
            images=[SRC],
            prompt=PROMPT,
            negative="",
            size="auto",
            quality="high",
            seed=None,
        )
        async with lock:
            inflight += 1
            peak = max(peak, inflight)
        started = time.time()
        row = {"index": index, "status": "?", "sec": 0.0, "bytes": 0, "error": ""}
        try:
            payload = await provider.edit(request, 420)
            row.update(status="ok", bytes=len(payload))
            if saved < keep:
                saved += 1
                (OUT_DIR / f"ok-{saved:02d}.png").write_bytes(payload)
        except Exception as exc:  # noqa: BLE001 - 任何失败都要记下来
            message = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
            row["error"] = str(message)[:220]
            row["status"] = "rate_limit" if is_rate_limited(str(message)) else "error"
        finally:
            row["sec"] = round(time.time() - started, 1)
            async with lock:
                inflight -= 1
                results.append(row)

    started = time.time()
    await asyncio.gather(*(one(i) for i in range(n)))
    wall = time.time() - started
    try:
        await provider.close()
    except Exception:  # noqa: BLE001
        pass
    ok = [row for row in results if row["status"] == "ok"]
    secs = sorted(row["sec"] for row in ok)
    summary = {
        "provider": provider.name,
        "model": provider.info().get("model") or provider.setting("model", ""),
        "requests": n,
        "ok": len(ok),
        "failed": n - len(ok),
        "rate_limited": sum(1 for row in results if row["status"] == "rate_limit"),
        "errors": sum(1 for row in results if row["status"] == "error"),
        "peak_inflight": peak,
        "wall_sec": round(wall, 1),
        "ok_per_min": round(len(ok) / wall * 60, 1) if wall > 0 else 0,
        "latency_p50": round(statistics.median(secs), 1) if secs else None,
        "latency_p95": round(secs[max(0, int(len(secs) * 0.95) - 1)], 1) if secs else None,
        "latency_max": round(secs[-1], 1) if secs else None,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rows": sorted(results, key=lambda row: row["index"]),
    }
    return [summary]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="备用接口并发实测")
    parser.add_argument("--provider", default="a6api")
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--keep", type=int, default=0, help="保存几张成品看效果")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    args = parser.parse_args(argv)

    if not SRC.is_file():
        print(f"[x] 缺少测试原图：{SRC}")
        return 2
    cfg = load_config(Path(args.config))
    env = load_env()
    providers = build_providers(cfg, env)
    provider = providers.get(args.provider)
    if provider is None:
        print(f"[x] 没有这个接口：{args.provider}（可用：{sorted(providers)}）")
        return 2
    ready, reason = provider.ready()
    if not ready:
        print(f"[x] {args.provider} 不可用：{reason}")
        return 2

    print(f"[*] {args.provider} · 模型 {provider.setting('model', '')} · 同时打 {args.n} 张")
    summary = asyncio.run(burst(provider, args.n, args.keep))[0]
    rows = summary.get("rows") or []

    report = OUT_DIR / f"{args.provider}-{args.n}concurrency.json"
    payload = dict(summary)
    payload["provider"] = args.provider
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n========== 结果 ==========")
    for key in ("ok", "failed", "rate_limited", "errors", "peak_inflight",
                "wall_sec", "ok_per_min", "latency_p50", "latency_p95", "latency_max"):
        print(f"  {key:<14} {summary.get(key)}")
    print(f"  报告            {report}")
    if summary["failed"]:
        print("\n  失败样例：")
        for row in [r for r in rows if r["status"] != "ok"][:6]:
            print("   ", row["status"], f"{row['sec']}s", row["error"][:130])
    print("\n结论：", "没有被限流，50 并发可以直接全推"
          if summary["rate_limited"] == 0 and summary["failed"] == 0
          else f"有 {summary['rate_limited']} 个被限流 / {summary['failed']} 个失败，需要降并发或加重试")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
