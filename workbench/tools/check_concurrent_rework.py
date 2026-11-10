# -*- coding: utf-8 -*-
"""9.6.7 并发改图自检：两张基准图同时框选改图，互不串任务、互不排队。

做三件事（都是真跑，不用 mock 替代 harness）：
  1. 造一个 3 张基准图的评审任务（临时目录，端口 8793，不写飞书、不发通知）
  2. 给其中两张各存一张标注图（带框的 PNG）
  3. **同时**发两轮 /api/harness/stream（mode=master，各点名一个 task_id），
     要求：两轮都拿到任务 id、id 不同、每条任务只覆盖点名的这一张、
     两个任务的 annotation_path 都存在且指向自己的标注图

用法：python tools/check_concurrent_rework.py
"""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORT = 8793
FIXTURE = ROOT / "scratch" / "concurrency-fixture"
TOKEN = "1_JiQgjmfST8"

FAILURES: list[str] = []


def check(ok: bool, label: str) -> None:
    print(f"  [{'ok' if ok else 'x'}] {label}")
    if not ok:
        FAILURES.append(label)


def free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("找不到空端口")


def annotation_png(path: Path, note_box=(0.2, 0.1, 0.3, 0.2)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (512, 512), (243, 243, 245))
    draw = ImageDraw.Draw(image)
    draw.rectangle([160, 100, 360, 460], fill=(236, 234, 230))
    x, y, w, h = note_box
    draw.rectangle(
        [int(x * 512), int(y * 512), int((x + w) * 512), int((y + h) * 512)],
        outline=(255, 60, 60), width=4,
    )
    image.save(path)
    return path


async def wait_health(client: httpx.AsyncClient, port: int) -> bool:
    for _ in range(80):
        try:
            res = await client.get(f"/api/health")
            if res.status_code == 200:
                return True
        except Exception:
            pass
        await asyncio.sleep(0.25)
    return False


async def main() -> int:
    print("① 造评审任务（3 张基准图）")
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_review_fixture.py"), str(FIXTURE)],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    job_id = ""
    for line in (FIXTURE / "data" / "jobs").iterdir():
        job_id = line.name
    check(bool(job_id), f"临时任务 {job_id}")
    config = json.loads((FIXTURE / "config.json").read_text(encoding="utf-8"))
    check(config.get("feishu", {}).get("enabled") is False, "临时任务不写飞书")

    port = free_port(PORT)
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "serve.py"), "--config",
         str(FIXTURE / "config.json"), "--port", str(port)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(base_url=base, timeout=240.0,
                                     params={"token": TOKEN}) as client:
            if not await wait_health(client, port):
                check(False, "服务起得来")
                return 1
            check(True, f"服务起来（{base}）")

            job = (await client.get(f"/api/jobs/{job_id}")).json()["job"]
            masters = [t for t in job["tasks"] if t.get("master")]
            check(len(masters) == 3, f"基准图 3 张（实际 {len(masters)}）")

            print("② 给两张基准图各存一张标注图")
            picked = masters[:2]
            annotations: list[str] = []
            for index, task in enumerate(picked):
                png = annotation_png(
                    ROOT / "scratch" / f"concurrency-anno-{index}.png",
                    (0.2 + index * 0.25, 0.1, 0.25, 0.2),
                )
                with png.open("rb") as fh:
                    res = await client.post(
                        f"/api/jobs/{job_id}/annotations",
                        data={
                            "task_id": task["id"],
                            "boxes": json.dumps([{"n": 1, "x": 0.2, "y": 0.1, "w": 0.25,
                                                  "h": 0.2, "note": f"框1 第{index + 1}张要改的地方"}]),
                        },
                        files={"file": ("annotation.png", fh, "image/png")},
                    )
                check(res.status_code == 200, f"标注图存好了（{task['id']} → {res.status_code}）")
                annotations.append(res.json()["annotation"]["id"])

            print("③ 同时发两轮改图（并发）")
            params = job.get("params") or {}
            base_payload = {
                "mode": "master",
                "job_id": job_id,
                "provider": str(params.get("provider") or ""),
                "model": str(params.get("model") or ""),
                "subject": "garment",
                "size": "auto",
                "quality": "low",
                "output_format": "png",
                "send_reference": True,
                "shape_lock": "master",
                "pattern_mode": "preserve",
                "history": [],
            }

            async def round_one(index: int):
                payload = dict(base_payload)
                payload.update({
                    "task_ids": [picked[index]["id"]],
                    "annotation_id": annotations[index],
                    "annotations": [{"n": 1, "x": 0.2 + index * 0.25, "y": 0.1,
                                     "w": 0.25, "h": 0.2, "note": f"第{index + 1}张只改框1"}],
                    "instruction": (
                        f"按我框选的位置改第 {index + 1} 张基准图的框1："
                        f"这块要走线平直。框外不要动，标注框线不要画进成品。"
                    ),
                })
                started = time.time()
                job_ids: list[str] = []
                async with client.stream("POST", "/api/harness/stream", json=payload) as res:
                    if res.status_code != 200:
                        body = await res.aread()
                        return {"index": index, "error": f"HTTP {res.status_code} {body[:200]!r}",
                                "job_ids": [], "seconds": 0.0}
                    buffer = ""
                    async for chunk in res.aiter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            frame, buffer = buffer.split("\n\n", 1)
                            event = "message"
                            data = ""
                            for line in frame.split("\n"):
                                if line.startswith("event:"):
                                    event = line[6:].strip()
                                elif line.startswith("data:"):
                                    data += line[5:].strip()
                            if not data:
                                continue
                            if event in {"job", "prompt"}:
                                try:
                                    body = json.loads(data)
                                except json.JSONDecodeError:
                                    continue
                                if body.get("job_id"):
                                    job_ids.append(str(body["job_id"]))
                            if event == "error":
                                return {"index": index, "error": data[:200],
                                        "job_ids": job_ids, "seconds": time.time() - started}
                return {"index": index, "error": "", "job_ids": job_ids,
                        "seconds": time.time() - started}

            results = await asyncio.gather(round_one(0), round_one(1), return_exceptions=True)
            ok_rounds = [r for r in results if isinstance(r, dict) and not r.get("error")]
            for item in results:
                if isinstance(item, Exception):
                    print(f"  [x] 第 ? 轮抛异常：{item}")
                    FAILURES.append("并发轮次抛异常")
                elif item.get("error"):
                    check(False, f"第 {item['index'] + 1} 轮失败：{item['error']}")
                else:
                    check(bool(item["job_ids"]),
                          f"第 {item['index'] + 1} 轮拿到改图任务 {item['job_ids']}（{item['seconds']:.1f}s）")
            check(len(ok_rounds) == 2, f"两轮都跑了（成功 {len(ok_rounds)}/2）")
            if len(ok_rounds) != 2:
                return 1

            new_ids = [r["job_ids"][-1] for r in ok_rounds]
            check(len(set(new_ids)) == 2, f"两个改图任务是分开的（{new_ids}）")
            for index, round_result in enumerate(ok_rounds):
                new_job = (await client.get(f"/api/jobs/{round_result['job_ids'][-1]}")).json()["job"]
                tasks = new_job.get("tasks") or []
                check(len(tasks) == 1, f"改图任务只含 1 张（实际 {len(tasks)}）")
                want = picked[round_result["index"]]["id"]
                src_task = tasks[0].get("id", "") if tasks else ""
                check(src_task.startswith(want), f"任务来自点名的这张（{src_task}）")
                check(bool((new_job.get("params") or {}).get("annotation_path")),
                      f"带上了标注图 {(new_job.get('params') or {}).get('annotation_path')}")
                # 接口把 prompt 从轮询响应里剔掉了（省流量），这里直接读盘上的 job.json
                disk = FIXTURE / "data" / "jobs" / round_result["job_ids"][-1] / "job.json"
                raw = json.loads(disk.read_text(encoding="utf-8"))
                prompt = ((raw.get("tasks") or [{}])[0].get("prompt") or "")
                check("OPERATOR ANNOTATION MAP" in prompt, "提示词里有标注约束")
                check("IMAGE 2" in prompt, "提示词指明标注图是 IMAGE 2")

            print("④ 两张基准图各自的改图来源不同")
            adopted = []
            for round_result in ok_rounds:
                new_job = (await client.get(f"/api/jobs/{round_result['job_ids'][-1]}")).json()["job"]
                task = (new_job.get("tasks") or [{}])[0]
                adopted.append(task.get("master_task") or task.get("adopted_from") or task.get("source"))
            check(all(adopted), f"每张都有来源（{adopted}）")
            check(len(set(map(str, adopted))) == 2, f"来源不同，没串任务（{adopted}）")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("\n全部通过：两轮框选改图并发跑通，各自只管自己那一张，标注图和约束都带上了")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
