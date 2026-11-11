# -*- coding: utf-8 -*-
"""9.6.0 便携包自检：该有的都在，且带上了「基准图闸门 + 款式记忆 + 对话工具」。

用法：python tools/verify_portable_9_6_0.py [--zip=<路径>]
"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ZIP = ROOT / "releases" / "PictureWorkbench-9.6.10-win-x64-internal.zip"
VERSION = "9.6.10"
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
    "workbench/skillbridge/job.py",
    "workbench/skillbridge/runner.py",
    "workbench/skillbridge/agent.py",
    # 9.6.0 新增/改动的关键文件
    "workbench/skillbridge/style_memory.py",
    "workbench/skillbridge/align_judge.py",
    "workbench/library/prompts/30-tie-dye-tee.json",
    "workbench/library/styles.json",
    "workbench/library/effects/manifest.json",
    "workbench/CHANGELOG_MASTER_GATE_9.6.0.md",
    "workbench/CHANGELOG_STYLES_9.5.0.md",
    "workbench/CHANGELOG_WHITE_MASTER_9.6.5.md",
    "workbench/CHANGELOG_ANNOTATE_9.6.6.md",
    "workbench/CHANGELOG_REVIEW_965_9.6.7.md",
    "workbench/CHANGELOG_CHAIN_9.6.9.md",
    "workbench/CHANGELOG_REVIEW_TIGHT_9.6.10.md",
    "workbench/HANDOVER.md",
    "workbench/web/index.html",
    "workbench/web/app.js",
    "workbench/web/style.css",
    "workbench/web/review-cards.css",
    "workbench/web/annotate.css",
    "workbench/web/ai-workflow.js",
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

    styles = json.loads(z.read(TOP + "workbench/library/styles.json").decode("utf-8"))
    colours = sum(len(s.get("colors") or []) for s in styles)
    check(len(styles) == 25, f"款式 25 个（实际 {len(styles)}）")
    check(colours == 115, f"色号 115 个（实际 {colours}）")

    service = z.read(TOP + "workbench/skillbridge/service.py").decode("utf-8")
    check("approve_master" in service and "reject_master" in service,
          "service.py 带基准图审核（approve/reject）")
    check("learn_from_review" in service and "review_payload" in service,
          "service.py 带款式记忆沉淀入口")
    runner = z.read(TOP + "workbench/skillbridge/runner.py").decode("utf-8")
    check("review_gate" in runner and 'status = "review"' in runner,
          "runner.py 带基准图闸门（review 状态）")
    agent = z.read(TOP + "workbench/skillbridge/agent.py").decode("utf-8")
    for tool in ("master_status", "approve_master", "reject_master", "get_style_skill", "save_style_rule"):
        check(f'"{tool}"' in agent, f"agent 工具 {tool}")
    memory = z.read(TOP + "workbench/skillbridge/style_memory.py").decode("utf-8")
    check("def remember(" in memory and "def clauses(" in memory, "style_memory 提供 remember/clauses")

    api = z.read(TOP + "workbench/skillbridge/api.py").decode("utf-8")
    for route in ("api/jobs/{job_id}/approve", "api/jobs/{job_id}/reject", "api/styles/{slug}/lessons"):
        check(route in api, f"接口 {route}")
    check('mode not in {"intake", "rework", "master"}' in api, "harness 支持 master 模式")

    app = z.read(TOP + "workbench/web/app.js").decode("utf-8")
    check("renderReview" in app and "approveMaster" in app, "前端有基准图审核条与确认动作")
    check("QUICK_MASTER" in app, "前端有审核期话术")
    # 9.6.6 框选改图：点基准图 → 画框 + 写要求 → 标注图随改图发出
    check("openAnnotator" in app and "composeAnnotatedImage" in app,
          "前端有框选标注（大图 + 画框 + 合成标注图）")
    check("annotation_id" in app, "标注 id 随对话发给 harness")
    # 9.6.7 框选直接发送 + 每张并发改图 + 卡片状态
    check("data-revert" in app and "/revert" in app, "卡片有复原（回第 1 轮）")
    check("runAnnotationRound" in app and "streamQuiet" in app,
          "「交给 AI 改」直接发送（不等第二次点击）")
    check("watchReworkFor" in app and "reworkTimers" in app,
          "改图进度按 jobId 分开盯（多张可并发）")
    check("data-state=" in app and "working-veil" in app,
          "卡片有改图中/第 2 轮状态")
    review_css = z.read(TOP + "workbench/web/review-cards.css").decode("utf-8")
    check("grid-template-columns:repeat(auto-fill" in review_css,
          "审核卡片自适应网格（不被挤压）")
    annotate_css = z.read(TOP + "workbench/web/annotate.css").decode("utf-8")
    check(".annotate-frame{position:relative" in annotate_css,
          "框层贴着图片（框跟手，不偏移）")
    index = z.read(TOP + "workbench/web/index.html").decode("utf-8")
    check('id="reviewBar"' in index, "index.html 有审核条容器")
    check('id="annotateDialog"' in index, "index.html 有框选弹窗容器")
    check("?v=9.6" in index, "前端资源版本 9.6.x")

    readme = z.read(TOP + "使用说明.txt").decode("utf-8-sig")
    check(VERSION in readme, f"使用说明写明 {VERSION}")
    check("基准图" in readme, "使用说明讲了先出基准图")
    info = json.loads(z.read(TOP + "package-info.json").decode("utf-8"))
    check(info.get("version") == VERSION, f"package-info 版本 {info.get('version')}")

    prompt_files = [
        n for n in names
        if "/library/prompts/" in n and n.endswith(".json") and not n.endswith("index.json")
    ]
    check(len(prompt_files) == 28, f"款式专属提示词 28 份（实际 {len(prompt_files)}）")
    effects = [n for n in names if "/library/effects/" in n and n.lower().endswith(
        (".jpg", ".jpeg", ".png", ".webp"))]
    check(len(effects) == 115, f"效果图 115 张（实际 {len(effects)}）")

    z.close()
    summary = {
        "version": VERSION,
        "zip": target.name,
        "bytes": len(data),
        "sha256": digest,
        "styles": len(styles),
        "colours": colours,
        "effect_images": len(effects),
        "style_prompt_files": len(prompt_files),
        "master_gate": True,
        "style_memory": True,
        "annotation": True,
        "assistant_tools": 5,
        "application_files": info.get("application_files"),
    }
    out = target.with_name(f"PictureWorkbench-{VERSION}-verification.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target.with_name(target.name + ".sha256")).write_text(
        f"{digest}  {target.name}\n", encoding="utf-8"
    )
    print(f"\n校验摘要 → {out.name}")
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("全部通过：基准图闸门 + 款式记忆 + 对话工具 + 框选改图都在包里，25 款 / 115 色齐全")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
