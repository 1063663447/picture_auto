"""静态一致性检查：admin.html / admin.js / app.js 的 id 与图标是否对得上。"""
from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"

html = (WEB / "admin.html").read_text(encoding="utf-8")
js = (WEB / "admin.js").read_text(encoding="utf-8")
index = (WEB / "index.html").read_text(encoding="utf-8")
app_js = (WEB / "app.js").read_text(encoding="utf-8")

ids = set(re.findall(r'id="([^"]+)"', html))
used = set(re.findall(r'\$\("([^"]+)"\)', js))
missing = sorted(used - ids)
print("admin.html ids:", len(ids))
print("admin.js 用到但页面缺的 id:", missing or "无")

symbols = set(re.findall(r'symbol id="([^"]+)"', html)) | set(
    re.findall(r'symbol id="([^"]+)"', index)
)
used_symbols = set(re.findall(r'use href="#([^"]+)"', js)) | set(
    re.findall(r'use href="#([^"]+)"', index)
) | set(re.findall(r'icon\("([a-z-]+)"', app_js))
print("缺的图标 symbol:", sorted(s for s in used_symbols if s not in symbols) or "无")

print("admin.html 引用的静态资源:", re.findall(r'(?:href|src)="(/[^"]+)"', html))
print("index.html 脚本版本:", re.findall(r'/app\.js\?v=([\d.]+)', index))

# app.js 里用到的 id 必须都在 index.html 中
app_used = set(re.findall(r'\$\("([^"]+)"\)', app_js))
app_missing = sorted(app_used - set(re.findall(r'id="([^"]+)"', index)))
print("app.js 用到但 index.html 缺的 id:", app_missing or "无")
