"""把工作台推到 Hugging Face Spaces（Docker SDK）——不需要 Git。

前提：
    python -m pip install huggingface_hub
    HF token：https://huggingface.co/settings/tokens （需要 write 权限）

用法（在本目录 workbench/ 下运行）：
    python deploy/push_hf_space.py --token hf_xxx --repo 你的用户名/apparel-workbench
    # 可选：顺便把 API Key 写成 Space secrets（推荐，别写进仓库）
    python deploy/push_hf_space.py --token hf_xxx --repo 你的用户名/apparel-workbench \
        --secret TERRA_API_KEY=sk-... --secret DEEPSEEK_API_KEY=sk-...

它会：
  1) 创建（或复用）一个 Docker Space
  2) 把 workbench/ 与 ../apparel-style-recolor/ 一起上传（工作台启动要解析 skill）
  3) 用 deploy/hf-space/README.md 作为 Space 的说明与 sdk 配置
  4) 打印访问地址
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

WORKBENCH = Path(__file__).resolve().parent.parent
SKILL = WORKBENCH.parent / "apparel-style-recolor"


def main() -> int:
    parser = argparse.ArgumentParser(description="推送到 Hugging Face Spaces")
    parser.add_argument("--token", required=True, help="HF write token")
    parser.add_argument("--repo", required=True, help="用户名/space名")
    parser.add_argument("--secret", action="append", default=[], help="KEY=VALUE，可重复")
    parser.add_argument("--private", action="store_true", help="创建私有 Space（需付费账号）")
    args = parser.parse_args()

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("需要先安装：python -m pip install huggingface_hub")
        return 2

    if not SKILL.is_dir():
        print(f"找不到 skill 目录：{SKILL}")
        return 2

    api = HfApi(token=args.token)

    # 1) 组一个干净的发布目录：workbench/* + apparel-style-recolor + Space README
    staging = Path(tempfile.mkdtemp(prefix="hf-space-"))
    target = staging / "payload"
    (target / "workbench").mkdir(parents=True)
    (target / "apparel-style-recolor").mkdir(parents=True)

    ignore = shutil.ignore_patterns(
        "data", ".bin", "__pycache__", "*.pyc", ".env", "docs", ".skills", "*.log"
    )
    for src in WORKBENCH.iterdir():
        if src.name in {"data", ".bin", "__pycache__", "docs", ".skills", ".env"}:
            continue
        if src.is_dir():
            (target / "workbench" / src.name).mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, target / "workbench" / src.name, ignore=ignore, dirs_exist_ok=True)
        else:
            shutil.copy2(src, target / "workbench" / src.name)
    for src in SKILL.iterdir():
        if src.name == "__pycache__":
            continue
        if src.is_dir():
            (target / "apparel-style-recolor" / src.name).mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, target / "apparel-style-recolor" / src.name, ignore=ignore, dirs_exist_ok=True)
        else:
            shutil.copy2(src, target / "apparel-style-recolor" / src.name)

    shutil.copy2(WORKBENCH / "deploy" / "Dockerfile", target / "Dockerfile")
    shutil.copy2(WORKBENCH / "deploy" / "hf-space" / "README.md", target / "README.md")

    print(f"[*] 创建/复用 Space：{args.repo}")
    api.create_repo(
        repo_id=args.repo,
        repo_type="space",
        space_sdk="docker",
        exist_ok=True,
        private=args.private,
    )

    print("[*] 上传文件（首次约 40~80MB，取决于依赖与资源）")
    api.upload_folder(
        folder_path=str(target),
        repo_id=args.repo,
        repo_type="space",
        commit_message="deploy: apparel recolor workbench",
    )

    for item in args.secret:
        if "=" not in item:
            continue
        key, _, value = item.partition("=")
        api.add_space_secret(repo_id=args.repo, key=key.strip(), value=value.strip())
        print(f"[*] 已写入 secret：{key.strip()}")

    print()
    print("=" * 70)
    print(f"  Space 地址： https://huggingface.co/spaces/{args.repo}")
    print(f"  应用地址：   https://{args.repo.replace('/', '-')}.hf.space/?token=<auth_token>")
    print("  首次构建 3~8 分钟，日志在 Space 的 Logs 里看")
    print("=" * 70)
    shutil.rmtree(staging, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
