"""线上验收：下载格式下拉 / JPG 压缩参数 / ZIP fmt。"""
from __future__ import annotations

import re

import httpx

BASE = "https://apparel-recolor-workbench.onrender.com"


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=180, follow_redirects=False) as client:
        client.post("/login", data={"username": "admin", "password": "Asd123456."})
        page = client.get("/").text
        version = re.search(r"app\.js\?v=([\d.]+)", page)
        js = client.get(f"/app.js?v={version.group(1)}").text if version else ""
        print("版本:", version.group(1) if version else "?")
        checks = {
            "下载格式下拉": 'id="downloadFmt"' in page,
            "下载说明位": 'id="downloadHint"' in page,
            "JS downloadUrl()": "function downloadUrl" in js,
            "JS 用 currentDownloadFmt": "currentDownloadFmt()" in js,
            "下载链接带 fmt": "&fmt=jpg&q=88" in js,
            "ZIP 导出带 fmt": 'fmt=${encodeURIComponent(fmt)}' in js,
            "JS renderDownloadHint": "function renderDownloadHint" in js,
        }
        for name, ok in checks.items():
            print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        missing = [k for k, v in checks.items() if not v]
        # 路由存在性：不存在的任务应当是 400（业务错），而不是 404 路由缺失
        print("  export?fmt=jpg 路由:", client.get("/api/jobs/nonexistent/export?fmt=jpg").status_code)
        return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
