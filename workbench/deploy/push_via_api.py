"""通过 GitHub REST API 推送仓库（不需要 git 可执行文件）。

适用场景：机器上 git 连不上 github.com（企业代理/防火墙只放行 HTTP），但 HTTPS API 可用。
它会读取本地 git 索引（git ls-files 的结果）或直接遍历目录，把文件逐个 PUT 到目标仓库。

    python deploy/push_via_api.py --token ghp_xxx --repo 1063663447/picture_auto
    python deploy/push_via_api.py --token ghp_xxx --repo owner/name --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # E:\picture_auto
API = "https://api.github.com"

SKIP_DIRS = {".git", "data", ".bin", "docs", ".skills", "__pycache__", ".tmp-uiux"}
SKIP_FILES = {".env"}


def run_git_files() -> list[str]:
    """优先用 git ls-files（尊重 .gitignore）；失败就自己遍历。"""
    git = None
    for candidate in (
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        "git",
    ):
        try:
            out = subprocess.run(
                [candidate, "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=60
            )
            if out.returncode == 0 and out.stdout.strip():
                git = out.stdout.splitlines()
                break
        except (OSError, subprocess.SubprocessError):
            continue
    if git:
        return [line.strip() for line in git if line.strip()]

    files: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name in SKIP_FILES or path.suffix in {".pyc", ".log"}:
            continue
        files.append(str(rel).replace("\\", "/"))
    return sorted(files)


def request(method: str, url: str, token: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "workbench-deploy")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"message": body[:300]}
        return exc.code, parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="用 GitHub API 推送文件（无需 git）")
    parser.add_argument("--token", required=True)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--message", default="deploy: apparel recolor workbench")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = run_git_files()
    print(f"[*] 待推送文件：{len(files)}")
    if args.dry_run:
        for name in files[:20]:
            print("   ", name)
        print("    ...")
        return 0

    status, info = request("GET", f"{API}/repos/{args.repo}", args.token)
    if status != 200:
        print(f"[x] 仓库不可访问：{status} {info.get('message')}")
        return 1
    print(f"[*] 目标仓库：{info.get('full_name')}（默认分支 {info.get('default_branch')}）")

    ok = 0
    failed: list[tuple[str, str]] = []
    for index, name in enumerate(files, start=1):
        path = ROOT / name
        try:
            content = path.read_bytes()
        except OSError as exc:
            failed.append((name, str(exc)))
            continue
        payload = {
            "message": f"{args.message} ({name})",
            "content": base64.b64encode(content).decode("ascii"),
            "branch": args.branch,
        }
        # 已存在则需要 sha 才能更新
        status, existing = request(
            "GET", f"{API}/repos/{args.repo}/contents/{urllib.parse.quote(name)}?ref={args.branch}",
            args.token,
        )
        if status == 200 and isinstance(existing, dict) and existing.get("sha"):
            payload["sha"] = existing["sha"]
        status, result = request(
            "PUT",
            f"{API}/repos/{args.repo}/contents/{urllib.parse.quote(name)}",
            args.token,
            payload,
        )
        if status in (200, 201):
            ok += 1
        else:
            failed.append((name, f"{status} {result.get('message')}"))
        if index % 10 == 0 or index == len(files):
            print(f"    {index}/{len(files)} 已上传")
        time.sleep(0.05)

    print(f"[*] 完成：成功 {ok}，失败 {len(failed)}")
    for name, reason in failed[:10]:
        print(f"    [x] {name}: {reason}")
    print(f"[*] 仓库地址：https://github.com/{args.repo}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
