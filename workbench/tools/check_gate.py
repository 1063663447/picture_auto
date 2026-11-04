"""全局并发阀门（Cloudflare Worker 那套协议）的离线回归测试。

用本地假阀门（Python 实现的同一协议）验证三件事：
  1. 两台「电脑」（两个 JobRunner 共用一把 Key）同时跑，**合计**并发不超过额度；
  2. 跑完每张图都会还牌（不会泄漏，任务结束后 in_use 回到 0）；
  3. 阀门挂了 + fail_open=true → 降级为本机并发，照样出图。

完全不联网：假阀门跑在本机回环端口。
运行：python tools/check_gate.py
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from skillbridge.gate import GateConfig, RemoteGate  # noqa: E402
from skillbridge.runner import JobRunner  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = Path(tempfile.mkdtemp(prefix="wb-gate-"))
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILED.append(label)


# --------------------------------------------------------------------- 假阀门 #
class FakeGate:
    """和 Cloudflare Worker 同协议：acquire / release / status，租约带 TTL。"""

    def __init__(self, limit: int = 2, ttl: float = 30.0) -> None:
        self.limit = limit
        self.ttl = ttl
        self.leases: dict[str, float] = {}
        self.lock = threading.Lock()
        self.peak = 0
        self.granted = 0
        self.release_count = 0
        self.denied = 0

    def gc(self, now: float) -> None:
        for lease, expires in list(self.leases.items()):
            if expires <= now:
                self.leases.pop(lease, None)

    def handle(self, body: dict) -> tuple[int, dict]:
        import time

        now = time.time()
        with self.lock:
            self.gc(now)
            action = body.get("action")
            if action == "status":
                return 200, {"ok": True, "in_use": len(self.leases), "limit": self.limit}
            if action == "release":
                self.release_count += 1
                self.leases.pop(str(body.get("lease") or ""), None)
                return 200, {"ok": True, "in_use": len(self.leases), "limit": self.limit}
            if action == "acquire":
                if len(self.leases) >= self.limit:
                    self.denied += 1
                    return 429, {
                        "ok": False,
                        "in_use": len(self.leases),
                        "limit": self.limit,
                        "retry_after_ms": 120,
                    }
                lease = uuid.uuid4().hex
                self.granted += 1
                self.leases[lease] = now + self.ttl
                self.peak = max(self.peak, len(self.leases))
                return 200, {
                    "ok": True,
                    "lease": lease,
                    "in_use": len(self.leases),
                    "limit": self.limit,
                }
        return 400, {"ok": False, "error": "unknown action"}


def start_fake_gate(gate: FakeGate) -> tuple[ThreadingHTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:  # noqa: BLE001
                body = {}
            status, payload = gate.handle(body)
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):  # 别刷屏
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


# ------------------------------------------------------------------ 被测环境 #
def make_png(path: Path) -> bytes:
    width, height = 640, 860
    image = Image.new("RGB", (width, height), (236, 232, 225))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([width * 0.12, height * 0.16, width * 0.88, height * 0.74], 40, fill=(28, 62, 210))
    image.save(path, format="PNG")
    return path.read_bytes()


def build_bench(name: str, gate_url: str, *, limit: int, fail_open: bool = True) -> Workbench:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    config["data_dir"] = str(SANDBOX / name / "data")
    config["skill_root"] = str(ROOT.parent / "apparel-style-recolor")
    config["library"] = {"enabled": True, "dir": str(ROOT / "library"), "hide_original_types": True}
    config["providers"] = {
        "mock": {
            "type": "mock", "enabled": True, "mock_delay_sec": 0.25,
            "concurrency": 8, "max_concurrency": 20,
        }
    }
    config["feishu"] = {"enabled": False}
    config["defaults"]["gate"] = {
        "enabled": True, "url": gate_url, "token_env": "GATE_TOKEN",
        "limit": limit, "poll_ms": 150, "wait_timeout_sec": 60, "fail_open": fail_open,
    }
    path = SANDBOX / name / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
    return Workbench(path)


def create(bench: Workbench, upload: bytes, colors: list[str]):
    return bench.create_job(
        [("item-01.png", upload)],
        garment_type="06-hoodie",
        colors=colors,
        provider="mock",
        model="mock-image",
        size="auto",
        quality="low",
        output_format="png",
    )


def runner_for(bench: Workbench, job) -> JobRunner:
    garment = bench.skill.type_by_alias(job.params["garment_type"])
    colors = garment.resolve_colors(job.params["colors"])
    provider = bench.get_provider(job.params["provider"])
    return JobRunner(
        job, bench.skill, garment, colors, provider,
        data_dir=bench.data_dir,
        gate=bench.gate_for(provider),
    )


async def run_pair(setups) -> None:
    runners = [runner_for(bench, job) for bench, job in setups]
    await asyncio.gather(*(runner.run() for runner in runners))


def main() -> int:
    gate = FakeGate(limit=2, ttl=30)
    server, url = start_fake_gate(gate)
    try:
        src = SANDBOX / "src.png"
        upload = make_png(src)
        colors = ["white", "black", "heather-grey"]

        print("[1] 两台电脑共用一把 Key（额度 2）")
        bench_a = build_bench("pc-a", url, limit=2)
        bench_b = build_bench("pc-b", url, limit=2)
        job_a = create(bench_a, upload, colors)
        job_b = create(bench_b, upload, colors)
        for job in (job_a, job_b):
            check(f"{job.id} 本机并发按上限收口",
                  job.params["concurrency"] <= 8, str(job.params["concurrency"]))
        asyncio.run(run_pair([(bench_a, job_a), (bench_b, job_b)]))

        check("两个任务都跑完", all(j.status in {"done", "partial"} for j in (job_a, job_b)),
              f"{job_a.status}/{job_b.status}")
        check("合计并发峰值 ≤ 额度", gate.peak <= 2, f"peak={gate.peak} limit=2")
        check("确实发生过排队（说明阀门在起作用）", gate.denied > 0, f"denied={gate.denied}")
        check("租约全部归还", not gate.leases, f"剩余 {len(gate.leases)} 张牌")
        check("拿到的牌和还回去的一样多", gate.granted == gate.release_count,
              f"granted={gate.granted} released={gate.release_count}")
        check("日志里能看到排队提示",
              any("排队中" in entry.get("msg", "") for entry in job_a.log + job_b.log))

        print("[2] 本机并发上限：任务数 > 接口上限时自动收口")
        bench_c = build_bench("pc-c", "", limit=20)
        bench_c.config["defaults"]["gate"] = {"enabled": False}
        many = [f"c{i}" for i in range(2)]
        job_c = bench_c.create_job(
            [("item-01.png", upload)], garment_type="06-hoodie",
            colors=bench_c.skill.type_by_alias("06-hoodie").colors and
                   [c.label for c in bench_c.skill.type_by_alias("06-hoodie").colors],
            provider="mock", model="mock-image", size="auto", quality="low",
            output_format="png",
        )
        check("auto 并发 = 任务数（未超上限）",
              job_c.params["concurrency"] == min(3, len(job_c.tasks)),
              f"{job_c.params['concurrency']} / tasks={len(job_c.tasks)}")
        bench_c.providers["mock"].cfg["max_concurrency"] = 2
        job_d = create(bench_c, upload, colors)
        check("超过 max_concurrency 时收口", job_d.params["concurrency"] == 2,
              str(job_d.params["concurrency"]))
        check("并记一条 warn 日志",
              any("接口上限" in entry.get("msg", "") for entry in job_d.log),
              str([e.get("msg") for e in job_d.log][:3]))

        print("[3] 阀门挂了 + fail_open → 退回本机并发，照样出图")
        gate_down = build_bench("pc-d", "http://127.0.0.1:9/", limit=20)
        job_e = create(gate_down, upload, colors)
        runner = runner_for(gate_down, job_e)
        check("阀门地址连不上时 gate 仍存在", runner.gate is not None)
        lease = asyncio.run(runner.gate.acquire(job=job_e.id, task="t1"))
        check("fail_open 时拿不到牌但放行", lease is None)
        asyncio.run(runner.run())
        check("降级后任务仍完成", job_e.status in {"done", "partial"}, job_e.status)
        check("日志里说明了降级",
              any("阀门不可用" in e.get("msg", "") for e in job_e.log),
              str([e.get("msg") for e in job_e.log][:4]))

        print("[4] 客户端状态接口")
        gate2 = FakeGate(limit=5, ttl=30)
        server2, url2 = start_fake_gate(gate2)
        try:
            client = RemoteGate(GateConfig.from_config(
                {"enabled": True, "url": url2, "limit": 5}, {}
            ))
            status = asyncio.run(client.status())
            check("status 可读", status.ok and status.limit == 5, status.as_dict())
            lease = asyncio.run(client.acquire(job="j", task="t"))
            check("acquire 拿到租约", bool(lease and lease.id), str(lease))
            asyncio.run(client.release(lease))
            after = asyncio.run(client.status())
            check("release 之后归零", after.in_use == 0, str(after.in_use))
            asyncio.run(client.close())
        finally:
            server2.shutdown()
    finally:
        server.shutdown()
        shutil.rmtree(SANDBOX, ignore_errors=True)

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项失败：{FAILED}")
        return 1
    print("[OK] 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
