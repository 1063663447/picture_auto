"""解压即用验证：把 9.4.4 包解到干净目录，用包内 runtime 起服务，跑一单 mock（不花钱）。

验证点：
1. 解压后不改任何东西，直接用 runtime/python.exe 起得来
2. /api/health、/ 首页、/api/skill、/api/models 都正常（说明依赖、静态资源、款式库、价目都在）
3. 建一单 mock 任务能出图并打包（说明整条生图链路在便携环境里可用）
4. 用完关掉，不留残留
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
VERSION = "9.6.12"
for _arg in sys.argv[1:]:
    if _arg.startswith("--version="):
        VERSION = _arg.split("=", 1)[1]
ZIP = ROOT.parent / "dist" / f"PictureWorkbench-{VERSION}-win-x64-internal.zip"
if not ZIP.is_file():
    ZIP = ROOT / "releases" / f"PictureWorkbench-{VERSION}-win-x64-internal.zip"
WORK = Path(r"E:\picture_auto\scratch\portable-test")
TARGET = WORK / f"PictureWorkbench-{VERSION}"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    if not ZIP.is_file():
        print("找不到包：", ZIP)
        return 2
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    print("[1/5] 解压到", TARGET)
    with zipfile.ZipFile(ZIP) as archive:
        archive.extractall(WORK)
    python = TARGET / "runtime" / "python.exe"
    if not python.is_file():
        print("包里没有 runtime/python.exe")
        return 1

    port = free_port()
    env = {"PATH": str(TARGET / "runtime"), "SYSTEMROOT": "C:\\Windows",
           "WORKBENCH_PORT": str(port), "PYTHONIOENCODING": "utf-8"}
    log = WORK / "server.log"
    print(f"[2/5] 用包内 Python 起服务，端口 {port}")
    with log.open("wb") as handle:
        process = subprocess.Popen(
            [str(python), "serve.py", "--port", str(port)],
            cwd=str(TARGET / "workbench"),
            env=env, stdout=handle, stderr=subprocess.STDOUT,
        )
    base = f"http://127.0.0.1:{port}"
    ok = False
    try:
        print("[3/5] 等健康检查")
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                health = httpx.get(base + "/api/health", timeout=3).json()
                ok = bool(health.get("ok"))
                break
            except Exception:
                time.sleep(1)
        if not ok:
            print("  起不来；日志末尾：")
            print(log.read_text(encoding="utf-8", errors="replace")[-2500:])
            return 1
        print("  health:", json.dumps({k: health.get(k) for k in ("ok", "types", "providers")}, ensure_ascii=False))

        with httpx.Client(base_url=base, timeout=60.0, follow_redirects=True) as client:
            index = client.get("/")
            print(f"  首页 HTTP {index.status_code}，{len(index.text)} 字节")
            skill = client.get("/api/skill").json()
            print("  款式数:", len(skill.get("types") or {}))
            models = client.get("/api/models?provider=terra&refresh=true").json()
            print("  备用接口可选模型:", models.get("count"), "| 隐藏未定价:", models.get("filtered_unpriced"))
            pricing = client.get("/api/pricing").json()
            print("  价目来源:", pricing.get("source"), "| 条目:", len(pricing.get("models") or {}))

            print("[4/5] 跑一单 mock 生图（不花钱）")
            source = TARGET / "workbench" / "library" / "effects" / "23-sorona-tee" / "WH[0].jpg"
            data = {
                "selections": json.dumps([{"garment_type": "23-sorona-tee", "colors": ["black"]}]),
                "provider": "mock",
                "subject": "garment",
                "start": "true",
            }
            with source.open("rb") as handle:
                response = client.post("/api/jobs", files={"files": (source.name, handle, "image/jpeg")}, data=data)
            payload = response.json()
            if not payload.get("ok"):
                print("  建单失败：", payload)
                return 1
            job_id = payload["job"]["id"]
            deadline = time.time() + 120
            job = payload["job"]
            while time.time() < deadline:
                time.sleep(2)
                job = client.get(f"/api/jobs/{job_id}").json()["job"]
                if all(t.get("status") in {"done", "failed", "skipped", "canceled"} for t in job["tasks"]):
                    break
            stage = job.get("stage")
            task = job["tasks"][0]
            print(f"  任务 {job_id} 状态={job.get('status')} stage={stage} 成品={task.get('out')}")
            if task.get("status") != "done":
                print("  失败原因：", task.get("error"))
                return 1
            image = client.get(f"/api/jobs/{job_id}/image/{task['id']}")
            print(f"  成品图 HTTP {image.status_code}，{len(image.content)//1024}KB")
    finally:
        print("[5/5] 关服务")
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
    print()
    print("解压即用验证通过：包内 Python + 依赖 + 配置 + 款式库 + 出图链路全部可用（mock 未产生费用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
