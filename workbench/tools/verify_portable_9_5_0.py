# -*- coding: utf-8 -*-
"""9.5.0 便携包自检：zip 里该有的都在，且款式库是 27 款 / 121 色。

用法：python tools/verify_portable_9_5_0.py [--zip=<路径>]
"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ZIP = ROOT / "releases" / "PictureWorkbench-9.5.0-win-x64-internal.zip"
VERSION = "9.5.0"
TOP = f"PictureWorkbench-{VERSION}/"

REQUIRED = [
    "一键启动.bat",
    "一键检查修复.bat",
    "使用说明.txt",
    "start.bat",
    "bootstrap.ps1",
    "package-manifest.json",
    "package-info.json",
    "runtime/python.exe",
    "runtime/Lib/site-packages/fastapi/__init__.py",
    "runtime/Lib/site-packages/httpx/__init__.py",
    "runtime/Lib/site-packages/PIL/__init__.py",
    "runtime/Lib/site-packages/multipart/__init__.py",
    "workbench/serve.py",
    "workbench/config.json",
    "workbench/pricing.json",
    "workbench/.env",
    "workbench/skillbridge/service.py",
    "workbench/skillbridge/prompts.py",
    "workbench/skillbridge/style_prompts.py",
    "workbench/skillbridge/swatch.py",
    "workbench/skillbridge/skill.py",
    "workbench/library/styles.json",
    "workbench/library/effects/manifest.json",
    "workbench/CHANGELOG_STYLES_9.5.0.md",
    "workbench/HANDOVER.md",
    "workbench/web/index.html",
    "workbench/web/app.js",
    "apparel-style-recolor/SKILL.md",
]

FAILURES: list[str] = []


def check(ok: bool, label: str) -> None:
    if ok:
        print(f"  [ok] {label}")
    else:
        FAILURES.append(label)
        print(f"  [x] {label}")


def main() -> int:
    target = DEFAULT_ZIP
    for arg in sys.argv[1:]:
        if arg.startswith("--zip="):
            target = Path(arg.split("=", 1)[1])
    if not target.is_file():
        print(f"包不存在：{target}")
        return 2
    data = target.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    print(f"包：{target.name}  {len(data)/1024/1024:.1f} MB  sha256 {digest}")

    z = zipfile.ZipFile(target)
    names = z.namelist()
    print(f"条目 {len(names)}")
    missing = [item for item in REQUIRED if TOP + item not in names]
    check(not missing, f"必需文件齐全（缺 {missing[:4]}）")

    styles_raw = z.read(TOP + "workbench/library/styles.json").decode("utf-8")
    styles = json.loads(styles_raw)
    colours = sum(len(s.get("colors") or []) for s in styles)
    check(len(styles) == 27, f"款式 27 个（实际 {len(styles)}）")
    check(colours == 121, f"色号 121 个（实际 {colours}）")
    kinds = {s.get("kind") for s in styles}
    check(kinds == {"top", "vest", "bottom"}, f"品类齐全 {sorted(kinds)}")

    effects = [n for n in names if "/library/effects/" in n and n.lower().endswith(
        (".jpg", ".jpeg", ".png", ".webp"))]
    refs = [n for n in names if "/library/refs/" in n and n.lower().endswith(
        (".jpg", ".jpeg", ".png", ".webp"))]
    check(len(effects) == 121, f"效果图 121 张（实际 {len(effects)}）")
    check(len(refs) == 121, f"参考缩略图 121 张（实际 {len(refs)}）")

    prompt_files = [n for n in names
                    if "/library/prompts/" in n and n.endswith(".json") and not n.endswith("index.json")]
    check(len(prompt_files) == 30, f"款式专属提示词 30 份（实际 {len(prompt_files)}）")

    readme = z.read(TOP + "使用说明.txt").decode("utf-8-sig")
    check(VERSION in readme and "27 个款式" in readme, "使用说明写明 9.5.0 与 27 个款式")
    check("9.4.9" not in readme.split("9.5.0")[0][:200], "使用说明不是上一版文案")

    index = z.read(TOP + "workbench/web/index.html").decode("utf-8")
    check(f"?v={VERSION}" in index, f"前端资源版本 {VERSION}")

    info = json.loads(z.read(TOP + "package-info.json").decode("utf-8"))
    check(info.get("version") == VERSION, f"package-info 版本 {info.get('version')}")

    z.close()
    summary = {
        "version": VERSION,
        "zip": target.name,
        "bytes": len(data),
        "sha256": digest,
        "styles": len(styles),
        "colours": colours,
        "effect_images": len(effects),
        "ref_images": len(refs),
        "style_prompt_files": len(prompt_files),
        "application_files": info.get("application_files"),
    }
    out = target.with_name(f"PictureWorkbench-{VERSION}-verification.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target.with_name(target.name + ".sha256")).write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    print(f"\n校验摘要 → {out.name}")
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("全部通过：包内 27 款 / 121 色 / 121 效果图 / 30 份提示词齐全")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
