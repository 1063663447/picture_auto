"""用生图接口直接出「产品展示图」：给参考图 + 提示词，拿回一张成品。

和工作台里跑批的区别：这里不碰款式库、不改色，只是把 tools/make_showcase_materials.py
排好的真机素材图（整屏界面 / 款式面板 / 真实成品网格 / 提示词正文）当成参考图，
让模型做摄影化处理（打光、材质、景深、氛围），版面里的一切像素保持不动。

用法：
    python tools/make_showcase.py --spec showcase.json                # 全跑
    python tools/make_showcase.py --spec showcase.json --only 01,03   # 只跑指定几张
    python tools/make_showcase.py --spec showcase.json --dry-run       # 只打印不调用

spec.json：
{
  "provider": "terra",
  "model": "gpt-image-2.6-sunburst",
  "out": "E:\\\\picture_auto\\\\workbench\\\\showcase",
  "shots": [
    {"id": "01-hero", "ref": ["mat/board-01-screen.png"], "size": "1536x1024", "prompt": "..."}
  ]
}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillbridge.config import load_config, load_env  # noqa: E402
from skillbridge.providers import build_providers  # noqa: E402
from skillbridge.providers.base import EditRequest, ProviderError, sniff_extension  # noqa: E402


async def run_shot(provider, shot: dict, *, timeout: float, model: str | None, quality: str,
                   fidelity: str | None, default_size: str) -> dict:
    refs = [Path(item) for item in shot.get("ref") or []]
    missing = [str(path) for path in refs if not path.is_file()]
    if missing:
        return {"id": shot.get("id"), "ok": False, "error": f"参考图不存在: {missing}"}
    if not refs:
        return {"id": shot.get("id"), "ok": False, "error": "没有参考图"}
    limit = int(getattr(provider, "max_images", 1) or 1)
    if len(refs) > limit:
        return {"id": shot.get("id"), "ok": False, "error": f"参考图 {len(refs)} 张超过接口上限 {limit}"}

    request = EditRequest(
        images=refs,
        prompt=str(shot.get("prompt") or ""),
        negative=shot.get("negative"),
        size=str(shot.get("size") or default_size),
        quality=str(shot.get("quality") or quality),
        model=shot.get("model") or model,
        input_fidelity=shot.get("input_fidelity") or fidelity,
    )
    started = time.time()
    try:
        data = await provider.edit(request, timeout)
    except ProviderError as exc:
        return {"id": shot.get("id"), "ok": False, "error": exc.message, "status": exc.status,
                "sec": round(time.time() - started, 1)}
    except Exception as exc:  # pragma: no cover - defensive
        return {"id": shot.get("id"), "ok": False, "error": f"{type(exc).__name__}: {exc}",
                "sec": round(time.time() - started, 1)}
    if len(data) < 512:
        return {"id": shot.get("id"), "ok": False, "error": f"返回内容过小（{len(data)} 字节）",
                "sec": round(time.time() - started, 1)}
    return {
        "id": shot.get("id"),
        "ok": True,
        "bytes": data,
        "sec": round(time.time() - started, 1),
        "size": request.size,
        "model": provider.model_name(request),
    }


async def main_async(args) -> int:
    spec_path = Path(args.spec).resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    base = spec_path.parent
    out_dir = Path(args.out or spec.get("out") or (base / "out"))
    out_dir.mkdir(parents=True, exist_ok=True)

    env = load_env()
    config = load_config(env=env)
    name = args.provider or spec.get("provider") or config.get("default_provider")
    providers = build_providers(config, env)
    if name not in providers:
        print(f"没有这个接口：{name}（可用：{sorted(providers)}）")
        return 2
    provider = providers[name]
    ok, reason = provider.ready()
    if not ok:
        print(f"接口 {name} 未就绪：{reason}")
        return 2

    model = args.model or spec.get("model")
    quality = str(spec.get("quality") or "high")
    fidelity = spec.get("input_fidelity")
    default_size = str(spec.get("size") or "1536x1024")
    timeout = float(spec.get("timeout_sec") or args.timeout)

    wanted = {part.strip() for part in (args.only or "").split(",") if part.strip()}
    shots = [shot for shot in (spec.get("shots") or []) if shot.get("id")]
    if wanted:
        shots = [shot for shot in shots if any(shot["id"].startswith(key) for key in wanted)]
    if not shots:
        print("没有要跑的 shot（检查 --only）")
        return 2

    print(f"接口 {name} / 模型 {model or provider.model_name()} / 质量 {quality} / 出图目录 {out_dir}")
    print(f"共 {len(shots)} 张，预计花费 ≈ ${len(shots) * 0.038:.3f}")
    if args.dry_run:
        for shot in shots:
            refs = [Path(item) for item in shot.get("ref") or []]
            print(f"  [dry] {shot['id']}  {shot.get('size') or default_size}  "
                  f"refs={[p.name for p in refs]}  prompt={len(str(shot.get('prompt') or ''))} 字")
        return 0

    await provider.open()
    manifest: list[dict] = []
    try:
        for shot in shots:
            for index, item in enumerate(shot.get("ref") or []):
                if not Path(item).is_absolute():
                    shot["ref"][index] = str((base / item).resolve())
            result = await run_shot(
                provider, shot, timeout=timeout, model=model, quality=quality,
                fidelity=fidelity, default_size=default_size,
            )
            record = {k: v for k, v in result.items() if k != "bytes"}
            if result.get("ok"):
                suffix = sniff_extension(result["bytes"])
                target = out_dir / f"{result['id']}{suffix}"
                target.write_bytes(result["bytes"])
                record["file"] = str(target)
                record["kb"] = round(len(result["bytes"]) / 1024, 1)
                print(f"  ✓ {result['id']}  {record['kb']} KB  {result['sec']}s  -> {target.name}")
            else:
                print(f"  ✗ {result['id']}  {record.get('error')}")
            manifest.append(record)
    finally:
        await provider.close()

    manifest_path = out_dir / "showcase-manifest.json"
    # 分批跑的时候不能把上一批的记录冲掉：按 id 合并，同 id 以本次为准
    previous: dict[str, dict] = {}
    if manifest_path.is_file():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in old.get("shots") or []:
                if entry.get("id"):
                    previous[str(entry["id"])] = entry
        except Exception:
            previous = {}
    for entry in manifest:
        if entry.get("id"):
            previous[str(entry["id"])] = entry
    merged = list(previous.values())
    manifest_path.write_text(
        json.dumps(
            {
                "provider": name,
                "model": model or provider.model_name(),
                "quality": quality,
                "spec": str(spec_path),
                "unit_price_usd": 0.038,
                "spent_usd": round(sum(1 for m in merged if m.get("ok")) * 0.038, 3),
                "shots": merged,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    done = sum(1 for item in manifest if item.get("ok"))
    print(f"完成 {done}/{len(manifest)}，清单：{manifest_path}")
    return 0 if done == len(manifest) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--only", default="", help="按 id 前缀挑几张，逗号分隔，例如 01,03")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
