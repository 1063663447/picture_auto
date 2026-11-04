"""验证「每个颜色不同图案」真的生效：预览提示词 + 建任务后逐色提示词各不相同。"""
from __future__ import annotations

import io
import sys

import httpx
from PIL import Image

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8792"
REQ = "让生成出来每个颜色对应不同的图案"


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=180, follow_redirects=False) as client:
        client.post("/login", data={"username": "admin", "password": "Asd123456."})

        # 1) 预览接口：需求 + 图案策略
        preview = client.post(
            "/api/preview",
            json={"garment_type": "short-sleeve", "color": "黑色",
                  "requirement": REQ, "pattern_mode": ""},
        ).json()
        prompt = preview["single_pass"]
        print("预览 pattern_mode:", preview["pattern_mode"])
        for mark in ("OPERATOR REQUEST", "PATTERN VARIETY", "LIFTED", "PRINT LOCK", "KEEP UNCHANGED"):
            print(f"  {mark:<16}", mark in prompt)

        # 2) 建任务（mock，3 色），检查每色的提示词不同
        buf = io.BytesIO()
        Image.new("RGB", (768, 1024), (120, 110, 100)).save(buf, format="PNG")
        response = client.post(
            "/api/jobs",
            data={
                "garment_type": "short-sleeve",
                "colors": ["黑色", "白色", "杏色"],
                "provider": "mock",
                "model": "gpt-image-2",
                "pattern_mode": "per_color_variant",
                "extra_requirements": REQ,
                "start": "false",
                "size": "1024x1536",
            },
            files=[("files", ("item-01_front.png", buf.getvalue(), "image/png"))],
        )
        if response.status_code != 200:
            print("[x]", response.status_code, response.text[:300])
            return 1
        job = response.json()["job"]
        print("job:", job["id"], "| pattern_mode:", job["params"].get("pattern_mode"))
        prompts = {t["color_name"]: t["prompt"] for t in job["tasks"]}
        for color, text in prompts.items():
            has_variant = "PATTERN VARIETY" in text
            mentions = color in text
            has_lock = "PRINT LOCK" in text
            print(f"  {color:<6} 专属图案={has_variant} 提到该色={mentions} 有图案锁={has_lock} len={len(text)}")
        unique = len({text for text in prompts.values()})
        print(f"不同颜色提示词去重后 {unique} 条（应等于颜色数 {len(prompts)}）")
        shared = set.intersection(*[set(text.split()) for text in prompts.values()])
        print("共有词数（越少说明差异化越多）:", len(shared))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
