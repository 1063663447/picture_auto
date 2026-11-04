"""完成通知（飞书群机器人 webhook）回归测试。

不联网：在本机起一个假的 webhook 接收端，检查
  1. 任务跑完能推出一条包含「张数 / 花费 / 任务号 / 取图地址」的文字；
  2. 没配 webhook（或 notify.enabled=false）时什么都不发，也不报错；
  3. webhook 500 时只写 warn 日志，不影响任务状态。

运行：python tools/check_notify.py
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from skillbridge.runner import JobRunner  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = Path(tempfile.mkdtemp(prefix="wb-notify-"))
FAILED: list[str] = []
RECEIVED: list[dict] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILED.append(label)


def start_webhook(status: int = 200) -> tuple[ThreadingHTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:  # noqa: BLE001
                body = {}
            RECEIVED.append(body)
            raw = json.dumps({"code": 0 if status == 200 else 1}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/hook"


def make_png(path: Path) -> bytes:
    width, height = 640, 860
    image = Image.new("RGB", (width, height), (236, 232, 225))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([width * 0.12, height * 0.16, width * 0.88, height * 0.74], 40, fill=(28, 62, 210))
    image.save(path, format="PNG")
    return path.read_bytes()


def build_bench(name: str, webhook: str, *, enabled: bool = True) -> Workbench:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    config["data_dir"] = str(SANDBOX / name / "data")
    config["skill_root"] = str(ROOT.parent / "apparel-style-recolor")
    config["library"] = {"enabled": True, "dir": str(ROOT / "library"), "hide_original_types": True}
    config["providers"] = {
        "mock": {"type": "mock", "enabled": True, "mock_delay_sec": 0.01, "concurrency": 4,
                 "max_concurrency": 20, "global_concurrency": 20}
    }
    config["feishu"] = {"enabled": False}
    config["defaults"]["gate"] = {"enabled": False}
    config["defaults"]["notify"] = {"enabled": enabled, "feishu_webhook": webhook}
    path = SANDBOX / name / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
    return Workbench(path)


def create(bench: Workbench, upload: bytes):
    return bench.create_job(
        [("item-01.png", upload)],
        garment_type="06-hoodie",
        colors=["white", "black"],
        provider="mock",
        model="mock-image",
        size="auto",
        quality="low",
        output_format="png",
    )


async def run_job(bench: Workbench, job) -> None:
    garment = bench.skill.type_by_alias(job.params["garment_type"])
    runner = JobRunner(
        job, bench.skill, garment, garment.resolve_colors(job.params["colors"]),
        bench.get_provider(job.params["provider"]), data_dir=bench.data_dir,
    )
    await runner.run()


def main() -> int:
    src = SANDBOX / "src.png"
    upload = make_png(src)

    print("[1] 配了 webhook：跑完推一条")
    server, hook = start_webhook(200)
    try:
        bench = build_bench("ok", hook)
        job = create(bench, upload)
        asyncio.run(run_job(bench, job))
        sent = bench.notify_done(job)
        check("推送成功", bool(sent) and bool(RECEIVED), f"收到 {len(RECEIVED)} 条")
        payload = RECEIVED[-1] if RECEIVED else {}
        text = (payload.get("content") or {}).get("text", "")
        check("是 text 消息", payload.get("msg_type") == "text", str(payload.get("msg_type")))
        for keyword in ("出图完成", "2 张", job.id, "取图"):
            check(f"内容含「{keyword}」", keyword in text, text.replace("\n", " | ")[:90])
        check("日志里记了推送成功",
              any("飞书群通知" in entry.get("msg", "") for entry in job.log),
              str([e.get("msg") for e in job.log][-3:]))
    finally:
        server.shutdown()

    print("[2] 没启用通知：不发，也不报错")
    RECEIVED.clear()
    server2, hook2 = start_webhook(200)
    try:
        quiet = build_bench("quiet", hook2, enabled=False)
        job2 = create(quiet, upload)
        asyncio.run(run_job(quiet, job2))
        check("返回 False", quiet.notify_done(job2) is False)
        check("没有发出请求", not RECEIVED, f"收到 {len(RECEIVED)} 条")
        check("任务状态正常", job2.status in {"done", "partial"}, job2.status)
    finally:
        server2.shutdown()

    print("[3] webhook 500：只写 warn，不炸")
    RECEIVED.clear()
    server3, hook3 = start_webhook(500)
    try:
        broken = build_bench("broken", hook3)
        job3 = create(broken, upload)
        asyncio.run(run_job(broken, job3))
        check("推送失败返回 False", broken.notify_done(job3) is False)
        check("日志里有失败提示",
              any("通知失败" in entry.get("msg", "") for entry in job3.log),
              str([e.get("msg") for e in job3.log][-3:]))
        check("任务仍是完成态", job3.status in {"done", "partial"}, job3.status)
    finally:
        server3.shutdown()
        shutil.rmtree(SANDBOX, ignore_errors=True)

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项失败：{FAILED}")
        return 1
    print("[OK] 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
