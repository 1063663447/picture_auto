"""探测 65535 网关：quality 参数到底影响什么（分辨率？计价？还是纯细节）。

对同一 size 分别用 quality=low / medium / high 各出一张，比较：
  * 返回图片的实际像素尺寸
  * 响应里有没有 usage / tokens 字段（能反推是否按 token 计价）

用法：python tools/probe_quality.py [--model gpt-image-2] [--size 1024x1024]
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path

import httpx
from PIL import Image

BASE = "https://sub-proxy-us.65535.space/v1"


def api_key() -> str:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("TERRA_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("缺 TERRA_API_KEY")


def probe(key: str, model: str, size: str, quality: str) -> dict:
    payload = {
        "model": model,
        "prompt": "a plain gray t-shirt, front view, studio photo",
        "size": size,
        "n": 1,
        "quality": quality,
    }
    try:
        response = httpx.post(
            f"{BASE}/images/generations",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=300,
        )
    except Exception as exc:  # noqa: BLE001
        return {"quality": quality, "error": str(exc)}
    if response.status_code != 200:
        return {"quality": quality, "status": response.status_code, "body": response.text[:300]}
    data = response.json()
    item = (data.get("data") or [{}])[0]
    b64 = item.get("b64_json") or ""
    dims = None
    if b64:
        try:
            dims = Image.open(io.BytesIO(base64.b64decode(b64))).size
        except Exception:  # noqa: BLE001
            dims = None
    usage = data.get("usage")
    return {
        "quality": quality,
        "status": response.status_code,
        "size": dims,
        "usage": usage,
        "extra_keys": [k for k in data if k not in {"created", "data"}],
        "item_keys": sorted(item.keys()),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--qualities", default="low,medium,high")
    args = parser.parse_args(argv)

    key = api_key()
    for quality in [q.strip() for q in args.qualities.split(",") if q.strip()]:
        result = probe(key, args.model, args.size, quality)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
