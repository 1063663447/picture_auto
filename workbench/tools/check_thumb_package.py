"""端到端验证：跑单过程中生成缩略图，不会再让打包失败。

复现的线上事故（06 连帽卫衣 · 3 色 · 1 张原图）：
    打包失败：ERROR: Item directory ...\\staging\\thumbs contains 2 images; expected 3.

这个脚本用 `mock` 接口在本机起一个临时服务（独立 data 目录、飞书关闭），
在**任务还没跑完时**就去取缩略图（复现真实场景），然后断言：
    * 打包成功、ZIP 里 3 张成品
    * 缩略图落在 `<job>/thumbs/`，`<job>/staging/` 里没有 thumbs/derived

用法：python tools/check_thumb_package.py
"""

from __future__ import annotations

import io
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SKILL = (ROOT.parent / "apparel-style-recolor").resolve()
TOKEN = "test-token-4d28"
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILED.append(label)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_json(url: str, payload=None, method="GET", timeout=30):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode() or "{}")


def http_bytes(url: str, timeout=30) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def post_multipart(url: str, fields: dict[str, list[str]], files: list[tuple[str, str, bytes]]):
    boundary = "----wb" + uuid.uuid4().hex
    body = io.BytesIO()
    for name, values in fields.items():
        for value in values:
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.write(str(value).encode() + b"\r\n")
    for name, filename, content in files:
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        )
        body.write(b"Content-Type: image/png\r\n\r\n")
        body.write(content + b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        url,
        data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def make_png() -> bytes:
    image = Image.new("RGB", (640, 800), (60, 90, 140))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def wait_health(base: str, timeout=40) -> bool:
    limit = time.time() + timeout
    while time.time() < limit:
        try:
            http_json(f"{base}/api/health?token={TOKEN}", timeout=3)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    return False


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    tmp = Path(tempfile.mkdtemp(prefix="wb-e2e-"))
    config_path = tmp / "config.json"
    data_dir = tmp / "data"
    config = {
        "skill_root": str(SKILL),
        "data_dir": str(data_dir),
        "library": {"enabled": True, "dir": str(ROOT / "library"), "hide_original_types": True},
        "server": {
            "host": "127.0.0.1", "port": port, "auth_token": TOKEN,
            "session_secret": "test-secret", "session_ttl": 3600, "login_required": False,
        },
        "defaults": {
            "mode": "single_pass", "concurrency": "auto", "max_retries": 2,
            "timeout_sec": 120, "size": "auto", "quality": "low",
            "output_format": "png", "send_reference": True, "input_fidelity": "high",
            "pricing_file": "pricing.json", "retry_on_timeout": False,
            "harness": {"enabled": False},
        },
        "providers": {
            "mock": {
                "type": "mock", "label": "mock", "enabled": True,
                "concurrency": 3, "mock_delay_sec": 1.2, "show_in_ui": True,
            }
        },
        "feishu": {"enabled": False},
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")

    env_note = " 独立端口 / 临时 data 目录 / 飞书关闭"
    print(f"[设置] port={port}{env_note}")
    server = subprocess.Popen(
        [sys.executable, "serve.py", "--config", str(config_path), "--port", str(port)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        if not wait_health(base):
            print("  服务没起来：" + (server.stdout.read(800).decode("utf-8", "replace")
                                     if server.stdout else ""))
            return 1
        check("服务就绪", True)

        colors = ["black", "white", "heather-grey"]
        fields = {
            "garment_type": ["06-hoodie"],
            "mode": ["single_pass"],
            "provider": ["mock"],
            "model": ["mock-image"],
            "concurrency": ["auto"],
            "size": ["auto"],
            "quality": ["low"],
            "output_format": ["png"],
            "pattern_mode": [""],
            "send_reference": ["true"],
            "start": ["true"],
            "colors": colors,
        }
        created = post_multipart(
            f"{base}/api/jobs?token={TOKEN}", fields, [("files", "item-01.png", make_png())]
        )
        job_id = created["job"]["id"]
        job_root = data_dir / "jobs" / job_id
        check("任务已创建", bool(job_id), job_id)

        # 关键：趁任务还在跑就去取缩略图 —— 正是线上出事的时序
        thumb_hits = 0
        deadline = time.time() + 90
        while time.time() < deadline:
            job = http_json(f"{base}/api/jobs/{job_id}?token={TOKEN}")["job"]
            if job["status"] in {"done", "partial", "failed", "canceled"}:
                break
            for task in job["tasks"]:
                if not task.get("thumb"):
                    continue
                try:
                    http_bytes(f"{base}{task['thumb']}?token={TOKEN}")
                    thumb_hits += 1
                except urllib.error.HTTPError:
                    pass
                try:  # 第二次应该命中缓存文件
                    http_bytes(f"{base}/api/jobs/{job_id}/thumb/{task['id']}?w=520&token={TOKEN}")
                except urllib.error.HTTPError:
                    pass
                break
            time.sleep(0.4)

        job = http_json(f"{base}/api/jobs/{job_id}?token={TOKEN}")["job"]
        check("任务跑到终态", job["status"] in {"done", "partial"}, job["status"])
        check("3 张全部完成", job["stats"]["done"] == 3, json.dumps(job["stats"], ensure_ascii=False))
        check("跑的过程中取过图", thumb_hits > 0, f"{thumb_hits} 次")

        thumbs_dir = job_root / "thumbs"
        cached = list(thumbs_dir.glob("*.webp")) if thumbs_dir.is_dir() else []
        check("缩略图缓存在 <job>/thumbs/", bool(cached), f"{len(cached)} 个")

        staging = job_root / "staging"
        junk = [p.name for p in staging.iterdir() if p.is_dir() and p.name in {"thumbs", "derived"}]
        check("staging 里没有缓存目录", not junk, ",".join(junk))
        check("staging 只有 item 目录",
              [p.name for p in staging.iterdir() if p.is_dir()] == ["item-01"],
              ",".join(p.name for p in staging.iterdir() if p.is_dir()))

        zip_path = job.get("zip")
        check("任务带 ZIP 链接", bool(zip_path), str(zip_path))
        zips = list((job_root / "outputs").glob("*.zip"))
        check("ZIP 已生成", bool(zips), str([p.name for p in zips]))
        if zips:
            with zipfile.ZipFile(zips[0]) as archive:
                names = [n for n in archive.namelist() if not n.endswith("/")]
            check("ZIP 里 3 张成品", len(names) == 3, str(len(names)))
            check(
                "成品名是「款式_颜色」",
                all("/" in n and n.rsplit("/", 1)[-1].count("_") >= 1 for n in names)
                and not any(n.rsplit("/", 1)[-1].startswith("item-01") for n in names),
                str(names),
            )
            check("ZIP 里没有缓存图",
                  not any("thumbs" in n or "derived" in n for n in names), str(names))
        check("任务没有报打包错误",
              "打包" not in (job.get("error") or ""), (job.get("error") or "")[:70])
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项失败：{FAILED}")
        return 1
    print("[OK] 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
