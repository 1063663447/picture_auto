"""线上验收：新站名 / 新 logo / 款式下拉 / 去掉的元素。"""
from __future__ import annotations

import re

import httpx

BASE = "https://apparel-recolor-workbench.onrender.com"


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=180, follow_redirects=False) as client:
        print("login", client.post("/login", data={"username": "admin", "password": "Asd123456."}).status_code)
        page = client.get("/").text
        title = re.search(r"<title>(.*?)</title>", page)
        print("标题:", title.group(1) if title else "?")
        checks = {
            "站名=生图工作台": "生图工作台" in page,
            "旧站名已消失": "服装改款换色工作台" not in page,
            "款式下拉 #garment": 'id="garment"' in page,
            "旧平铺容器已去掉": 'id="types"' not in page,
            "生成方式卡片已去掉": "生成方式" not in page,
            "modelTip 已去掉": "modelTip" not in page,
            "参考图开关已去掉": "sendReference" not in page,
            "skill 路径已去掉": "skillLine" not in page,
            "新 logo": "logo-glyph" in page,
        }
        for name, ok in checks.items():
            print(f"  {'PASS' if ok else 'FAIL'}  {name}")

        version = re.search(r"app\.js\?v=([\d.]+)", page)
        print("app.js 版本:", version.group(1) if version else "?")
        js = client.get(f"/app.js?v={version.group(1)}").text if version else ""
        print("  JS 无 sendReference:", "sendReference" not in js,
              "| JS 无 modelTip:", "modelTip" not in js,
              "| JS 有 selectGarment:", "selectGarment" in js)
        login = client.get("/login").text
        print("  登录页标题=生图工作台:", "生图工作台" in login)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
