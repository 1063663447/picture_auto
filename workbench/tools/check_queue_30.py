"""30 并发 + 中途再插 5 个需求的排队实测（飞书槽位闸门，真实飞书表）。

流程：
  1. A、B 两台「电脑」同时各提交 15 个需求（共 30 张，mock 每次 6 秒）；
  2. 跑 5 秒后再从 B、A 分别插入 3 个、2 个（共 5 个）；
  3. 全程每 0.5s 采样：闸门表被占用的槽位数、已完成数；
  4. 复查：把 35 个任务的 started_at 排序，检查「后插的 5 个」是不是排在最后（FIFO）。
"""

from __future__ import annotations

import io
import json
import time
import urllib.request
import uuid
from pathlib import Path

from skillbridge import feishu as feishu_mod
from skillbridge.config import load_config, load_env

TOK = "1_JiQgjmfST8"
INST = {"A": "http://127.0.0.1:8787", "B": "http://127.0.0.1:8799"}
TABLE = "tblYydJOE5r6KAow"
IMAGE = Path("data/test/christmas-tee.jpg")
OUT = Path("data/test/queue-30-report.json")


def busy_slots(app) -> list[dict]:
    rows = app.search_table(TABLE, page_size=200, automatic=True)
    now = time.time()
    out = []
    for item in rows:
        fields = item.get("fields") or {}
        name = fields.get("占位")
        if isinstance(name, list):
            name = "".join(str(p.get("text") or "") for p in name if isinstance(p, dict))
        if str(name or "").startswith("Q"):
            continue
        lease = fields.get("租约")
        if isinstance(lease, list):
            lease = "".join(str(p.get("text") or "") for p in lease if isinstance(p, dict))
        if not str(lease or "").strip():
            continue
        started = fields.get("开始") or 0
        try:
            started = float(started) / 1000.0 if float(started) > 1e11 else float(started)
        except (TypeError, ValueError):
            started = 0.0
        if started and now - started > 900:
            continue
        note = fields.get("说明")
        if isinstance(note, list):
            note = "".join(str(p.get("text") or "") for p in note if isinstance(p, dict))
        out.append({"slot": str(name), "note": str(note), "started": started})
    return out


def tickets(app) -> list[dict]:
    rows = app.search_table(TABLE, page_size=200)
    out = []
    for item in rows:
        fields = item.get("fields") or {}
        name = fields.get("占位")
        if isinstance(name, list):
            name = "".join(str(p.get("text") or "") for p in name if isinstance(p, dict))
        if str(name or "").startswith("Q"):
            note = fields.get("说明")
            if isinstance(note, list):
                note = "".join(str(p.get("text") or "") for p in note if isinstance(p, dict))
            out.append(str(note or ""))
    return out


def submit(base: str, colors: list[str], tag: str) -> dict:
    boundary = "----wb" + uuid.uuid4().hex
    body = io.BytesIO()
    fields = {
        "garment_type": "23-sorona-tee",
        "mode": "single_pass",
        "provider": "mock",
        "model": "mock-image",
        "concurrency": "auto",
        "size": "auto",
        "quality": "low",
        "output_format": "png",
        "pattern_mode": "",
        "send_reference": "true",
        "start": "true",
        "extra_requirements": f"只换面料底色（批次 {tag}）",
    }
    for key, value in fields.items():
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
    for color in colors:
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"colors\"\r\n\r\n{color}\r\n".encode())
    body.write(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
        f"filename=\"item-01{IMAGE.suffix}\"\r\nContent-Type: image/jpeg\r\n\r\n".encode()
    )
    body.write(IMAGE.read_bytes() + b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        f"{base}/api/jobs?token={TOK}",
        data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode())


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}?token={TOK}", timeout=30) as response:
        return json.loads(response.read().decode())


TEE_COLORS = ["black", "white", "pink", "apricot", "grey", "red", "heather-grey",
              "brown", "blue", "forest-green"]


def main() -> int:
    env = load_env()
    cfg = load_config(Path("config.json"), env)
    app = feishu_mod.FeishuBitable(feishu_mod.load_config(cfg, env))
    limit = int(cfg["defaults"]["gate"]["limit"])
    print(f"闸门额度 {limit}；开始前占用 {len(busy_slots(app))} 个槽位")

    jobs: list[dict] = []

    def batch(tag: str, a_colors: list[str], b_colors: list[str]) -> None:
        if a_colors:
            job = submit(INST["A"], a_colors, tag)["job"]
            jobs.append({"side": "A", "tag": tag, "id": job["id"], "tasks": job["stats"]["total"]})
        if b_colors:
            job = submit(INST["B"], b_colors, tag)["job"]
            jobs.append({"side": "B", "tag": tag, "id": job["id"], "tasks": job["stats"]["total"]})

    # 第 1 批：A 15 张、B 15 张（10 色 × 2 单，各 15 由两单凑：先用 10 色 + 5 色）
    t0 = time.time()
    batch("1a", TEE_COLORS, [])
    batch("1b", ["black", "white", "pink", "apricot", "grey"], TEE_COLORS)
    batch("1c", [], ["black", "white", "pink", "apricot", "grey"])
    print(f"第 1 批已提交（{sum(j['tasks'] for j in jobs)} 张）")

    late_done = False
    peak = 0
    timeline = []
    while time.time() - t0 < 600:
        busy = busy_slots(app)
        peak = max(peak, len(busy))
        elapsed = round(time.time() - t0, 1)
        statuses = {}
        done = 0
        for side, base in INST.items():
            mine = [j for j in jobs if j["side"] == side]
            for job in mine:
                try:
                    detail = get(base, f"/api/jobs/{job['id']}")["job"]
                except Exception:  # noqa: BLE001
                    continue
                statuses[job["id"]] = detail["status"]
                done += detail["stats"]["done"] + detail["stats"]["skipped"]
        timeline.append({"t": elapsed, "busy": len(busy), "done": done,
                         "tickets": len(tickets(app))})
        print(f"  t={elapsed:6.1f}s  占用={len(busy):2d}  排队号={len(tickets(app)):2d}  完成={done}")

        if not late_done and elapsed >= 6:
            batch("2a", ["blue", "brown", "red"], [])
            batch("2b", [], ["blue", "brown"])
            late_done = True
            print(f"  → 中途插入第 2 批 5 张（t={elapsed}s）")

        if late_done and all(s in {"done", "partial", "failed", "canceled"}
                             for s in statuses.values()) and len(statuses) == len(jobs):
            break
        time.sleep(0.5)

    # 汇总：按 started_at 排序看谁先跑
    records = []
    for job in jobs:
        base = INST[job["side"]]
        try:
            detail = get(base, f"/api/jobs/{job['id']}")["job"]
        except Exception:  # noqa: BLE001
            continue
        for task in detail["tasks"]:
            records.append({
                "side": job["side"], "tag": job["tag"], "task": task["id"],
                "status": task["status"],
                "started_at": task.get("started_at") or 0,
                "duration": task.get("duration"),
            })
    records.sort(key=lambda r: r["started_at"])
    order = [f"{r['tag']}/{r['task']}" for r in records]

    print("\n按开始时间排序（前 8 / 后 8）：")
    for line in order[:8]:
        print("   ", line)
    print("    ...")
    for line in order[-8:]:
        print("   ", line)

    first_batch = [r for r in records if r["tag"].startswith("1")]
    late = [r for r in records if r["tag"].startswith("2")]
    max_first = max((r["started_at"] for r in first_batch), default=0)
    min_late = min((r["started_at"] for r in late), default=0)
    fifo_ok = len(late) == 5 and min_late > max_first
    stats = {}
    for side, base in INST.items():
        try:
            gate = get(base, "/api/gate")
            stats[side] = gate.get("stats_by_provider") or {}
        except Exception:  # noqa: BLE001
            stats[side] = {}

    report = {
        "limit": limit,
        "elapsed": round(time.time() - t0, 1),
        "peak_busy": peak,
        "fifo_ok": fifo_ok,
        "late_count": len(late),
        "first_batch_last_start": max_first,
        "late_first_start": min_late,
        "order": order,
        "timeline": timeline,
        "jobs": jobs,
        "gate_stats": stats,
        "final_busy": len(busy_slots(app)),
        "final_tickets": len(tickets(app)),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n总耗时 {report['elapsed']}s；同时占用槽位峰值 {peak}（额度 {limit}）")
    print(f"FIFO：后插的 5 个都排在最后 → {fifo_ok}")
    print(f"结束后占用 {report['final_busy']} · 残留排队号 {report['final_tickets']}")
    print("埋点：", json.dumps(stats, ensure_ascii=False)[:600])
    print("报告：", OUT)
    return 0 if fifo_ok and peak <= limit else 1


if __name__ == "__main__":
    raise SystemExit(main())


# 用法：
#   python tools/check_queue_30.py                 # 用 mock 接口（免费，推荐）
#   想用真接口：把 submit() 里的 provider 改成 terra（30 张 ≈ ¥1.14）
