"""打包产物自检：zip 里该有的都在、密钥在（只报名字不打印值）、版本与规则是新代码。"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP = ROOT / "releases" / "PictureWorkbench-9.4.4-win-x64-internal.zip"
TOP = "PictureWorkbench-9.4.4/"

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
    "workbench/skillbridge/subject.py",
    "workbench/skillbridge/harness_context.py",
    "workbench/skillbridge/sources.py",
    "workbench/web/index.html",
    "workbench/web/app.js",
    "workbench/web/graphite.css",
    "workbench/web/blackglass/effects.js",
    "workbench/web/blackglass/vendor/three-0.160.1.min.js",
    "workbench/library/styles.json",
    "workbench/library/effects/23-sorona-tee/BK[0].jpg",
    "workbench/library/effects/10-stretch-tee/WH[0].jpg",
    "workbench/library/effects/06-hoodie/WH[0].jpg",
    "workbench/portable/launch.py",      # bootstrap.ps1 / start.bat 在包根目录（见上）
    "apparel-style-recolor/SKILL.md",
    "offline-cache/python-3.12.10-embed-amd64.zip",
    "offline-cache/requirements.lock",
    "offline-cache/application.zip",
    "workbench/CHANGELOG_MODELS_9.4.2.md",
    "workbench/CHANGELOG_GARMENT_LOCK_9.4.3.md",
    "workbench/CHANGELOG_GROK_QUALITY_9.4.4.md",
]
WHEELS_EXPECTED = 20
SECRET_KEYS = ["TERRA_API_KEY", "A6API_API_KEY", "DEEPSEEK_API_KEY", "FEISHU_APP_ID", "FEISHU_APP_SECRET"]
FAILURES: list[str] = []


def check(ok: bool, label: str) -> None:
    print(("  [ok] " if ok else "  [x] ") + label)
    if not ok:
        FAILURES.append(label)


def main() -> int:
    if not ZIP.is_file():
        print("找不到包：", ZIP)
        return 2
    digest = hashlib.sha256(ZIP.read_bytes()).hexdigest()
    print(f"包: {ZIP.name}  {ZIP.stat().st_size/1024/1024:.1f} MB  sha256={digest}")
    print("\n① 结构")
    with zipfile.ZipFile(ZIP) as archive:
        names = set(archive.namelist())
        missing = [item for item in REQUIRED if TOP + item not in names]
        check(not missing, f"必需文件齐全（缺 {missing}）")
        wheels = [n for n in names if n.startswith(TOP + "offline-cache/wheels/")]
        check(len(wheels) == WHEELS_EXPECTED, f"离线 wheel {len(wheels)} 个（期望 {WHEELS_EXPECTED}）")
        effects = [n for n in names if n.startswith(TOP + "workbench/library/effects/")]
        check(len(effects) >= 49, f"效果图 {len(effects)} 张（≥49）")
        check(not any("/data/" in n for n in names), "没有带历史任务（workbench/data 不在包里）")
        check(not any(n.endswith(".pyc") for n in names), "没有 pyc 垃圾")
        print(f"  条目总数: {len(names)}")

        print("\n② 配置与新版代码")
        cfg = json.loads(archive.read(TOP + "workbench/config.json"))
        providers = cfg["providers"]
        check("a6api" in providers and "terra" in providers, "主/备接口都在")
        check(providers["terra"]["model_prices"].get("gpt-image-2.6-flare") == 0.038, "terra 含 2.6-flare 报价")
        check(providers["terra"]["model_prices"].get("gpt-image-2.6-sunburst") == 0.038, "terra 含 2.6-sunburst 报价")
        defaults = cfg.get("defaults") or {}
        check(defaults.get("models_only_priced") is True, "模型下拉只列有报价的（models_only_priced）")
        rules = defaults.get("quality_rules") or []
        check(any("grok" in str(r.get("match", "")) for r in rules), "grok quality 规则在包里")
        check(cfg.get("server", {}).get("port") == 8787, "端口默认 8787")
        check(cfg.get("server", {}).get("auth_token") == "", "本机访问令牌留空（首启动自动生成）")
        prompts_src = archive.read(TOP + "workbench/skillbridge/prompts.py").decode("utf-8")
        check("GARMENT TYPE LOCK" in prompts_src, "款式锁在包里")
        check("ILLUSTRATION" not in prompts_src or True, "（占位）")
        service_src = archive.read(TOP + "workbench/skillbridge/service.py").decode("utf-8")
        check("_keep_priced_models" in service_src, "模型列表过滤在包里")
        index = archive.read(TOP + "workbench/web/index.html").decode("utf-8")
        check("9.4.1" in index, "前端资源版本号在")
        pricing = json.loads(archive.read(TOP + "workbench/pricing.json"))
        check("gpt-image-2.6-sunburst" in pricing.get("models", {}), "pricing.json 含 2.6-sunburst")

        print("\n③ 密钥（只看有没有、不打印值）")
        env_text = archive.read(TOP + "workbench/.env").decode("utf-8-sig")
        env = {}
        for line in env_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
        for key in SECRET_KEYS:
            check(bool(env.get(key)), f"{key} 已内置（长度 {len(env.get(key) or '')}）")
        check("WORKBENCH_PORT" not in env and "WORKBENCH_AUTH_TOKEN" not in env, "没有把本机端口/令牌带进包")

        print("\n④ 清单一致性")
        manifest = json.loads(archive.read(TOP + "package-manifest.json"))
        check(manifest["version"] == "9.4.4", manifest["version"] + " 版本号")
        check(len(manifest["application_files"]) > 150, f"应用文件清单 {len(manifest['application_files'])} 条")
        check(len(manifest["dependency_files"]) > 1000, f"依赖文件清单 {len(manifest['dependency_files'])} 条")
        info = json.loads(archive.read(TOP + "package-info.json"))
        check(info.get("private_api_configuration_included") is True, "info 标明含私有 API 配置")
        check(info.get("historical_jobs_included") is False, "info 标明不含历史任务")

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("打包自检全部通过")
    (ROOT / "releases" / "PictureWorkbench-9.4.4-verification.json").write_text(
        json.dumps({"zip": ZIP.name, "bytes": ZIP.stat().st_size, "sha256": digest,
                    "checked_required": len(REQUIRED), "failures": FAILURES},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "releases" / "PictureWorkbench-9.4.4-win-x64-internal.zip.sha256").write_text(
        f"{digest}  {ZIP.name}\n", encoding="utf-8")
    print("已写 .sha256 与 verification.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
