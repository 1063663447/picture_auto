# -*- coding: utf-8 -*-
"""造一个「基准图待确认」的假任务，用来在没有真实生图花费的情况下验收界面。

用法：
    python tools/make_review_fixture.py [目标目录]
输出：<目录>/config.json（额外挂 mock 接口 + data_dir 指过来）+ data/jobs/<id>/job.json
      打印 job id 与可用的访问地址，用于浏览器验收。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402


def make_png(path: Path, text: str, color) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (768, 768), (243, 243, 245))
    draw = ImageDraw.Draw(image)
    draw.rectangle([210, 120, 560, 640], fill=color)
    draw.text((300, 360), text, fill=(255, 255, 255))
    image.save(path)
    return path


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "scratch" / "review-fixture"
    if target.exists():
        shutil.rmtree(target)
    data = target / "data"
    job_id = f"{time.strftime('%Y%m%d-%H%M%S')}-revw"
    job_root = data / "jobs" / job_id
    inputs = job_root / "inputs" / "item-01"
    outputs = job_root / "outputs"
    for path in (inputs, outputs, job_root / "work", job_root / "staging"):
        path.mkdir(parents=True, exist_ok=True)

    make_png(inputs / "view-01.png", "SOURCE", (188, 60, 66))
    master = make_png(outputs / "master-white.png", "BASE", (236, 234, 230))

    def task(task_id: str, color: str, name: str, *, master_flag: bool, out: str = "",
             derived: bool = False) -> dict:
        return {
            "id": task_id, "item": "item-01", "view": "view-01",
            "color": color, "color_hex": "#ECEAE6", "color_name": name,
            "source": "inputs/item-01/view-01.png",
            "garment": "23-sorona-tee", "garment_name": "23 索罗娜短袖",
            "prompt": "CONVERT THE GARMENT TYPE … fixture …",
            "subject": "garment", "person_alert": False,
            "master": master_flag, "master_task": "" if master_flag else "item-01-view-01__white",
            "reference": "", "approved": False, "review_note": "", "adopted_from": "",
            "status": "done" if out else "pending", "attempts": 1 if out else 0,
            "error": None, "error_kind": "", "out": out or None,
            "started_at": time.time() - 60 if out else None,
            "finished_at": time.time() - 30 if out else None,
            "bytes": master.stat().st_size if out else 0,
        }

    tasks = [
        task("item-01-view-01__white", "white", "白色", master_flag=True,
             out="outputs/master-white.png"),
        task("item-01-view-01__black", "black", "黑色", master_flag=False, derived=True),
        task("item-01-view-01__red", "red", "红色", master_flag=False, derived=True),
    ]
    payload = {
        "id": job_id, "status": "review", "stage": "基准图已出，等你确认（确认后按这张图改其余颜色）",
        "batch_name": "23-sorona-tee-style-recolor-batch",
        "zip": None, "has_zip": False, "error": None,
        "created_at": time.time() - 120, "updated_at": time.time() - 20, "finished_at": None,
        "duration": None,
        "stats": {"total": 3, "pending": 2, "queued": 0, "running": 0, "done": 1,
                  "failed": 0, "skipped": 0, "canceled": 0, "finished": 1, "rate_limited": 0},
        "progress": 33.3,
        "params": {
            "garment_type": "23-sorona-tee", "garment_name": "23 索罗娜短袖",
            "colors": ["white", "black", "red"],
            "selections": [{"garment_type": "23-sorona-tee", "garment_name": "23 索罗娜短袖",
                            "colors": ["white", "black", "red"]}],
            "style_count": 1, "total_colors": 3, "mode": "single_pass",
            "kind": "", "provider": "mock", "provider_label": "离线模拟",
            "model": "mock-image", "concurrency": 1, "rpm": 0, "size": "auto",
            "quality": "low", "seed": None, "max_retries": 1, "timeout_sec": 60,
            "output_format": "png", "send_reference": True, "input_fidelity": "",
            "max_bytes": 2000000, "extra_requirements": "", "pattern_mode": "preserve",
            "pattern_brief": "", "subject": "garment", "subject_choice": "garment",
            "retry_on_person": False, "shape_lock": "master", "review_gate": True,
            "account": "fixture",
            "estimate": {"model": "mock-image", "count": 3, "unit": 0.0, "total": 0.0,
                         "currency": "CNY", "priced": True},
        },
        "tasks": tasks,
        "log": [{"t": time.time() - 100, "level": "info", "msg": "（界面验收用的假任务，不花钱）"}],
        "review": {
            "enabled": True, "awaiting": True, "phase": "",
            "master_ids": ["item-01-view-01__white"],
            "pending_ids": ["item-01-view-01__white"],
            "approved_ids": [], "derived_total": 2, "derived_done": 0, "can_approve": True,
        },
    }
    (job_root / "job.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    config["data_dir"] = str(data)
    config.setdefault("providers", {})["mock"] = {
        "type": "mock", "label": "离线模拟", "enabled": True, "ready": True,
        "unlimited": True, "model": "mock-image",
    }
    # 验收用的假任务绝不能写真实飞书 / 发通知
    config.setdefault("feishu", {})["enabled"] = False
    config["feishu"]["table_id"] = ""
    config.setdefault("notify", {})["enabled"] = False
    config["defaults"]["review_master"] = True
    config["defaults"]["models_only_priced"] = False
    server = config.setdefault("server", {})
    server["port"] = 8789
    (target / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    token = str(server.get("auth_token") or "1_JiQgjmfST8")
    print(f"fixture: {target}")
    print(f"job_id : {job_id}")
    print("启动：python serve.py --config \"%s\" --port 8789" % (target / "config.json"))
    print(f"打开：http://127.0.0.1:8789/?token={token}&job={job_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
