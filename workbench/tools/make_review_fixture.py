# -*- coding: utf-8 -*-
"""造一个「基准图待确认」的假任务，用来在没有真实生图花费的情况下验收界面。

用法：
    python tools/make_review_fixture.py [目标目录]
输出：<目录>/config.json（额外挂 mock 接口 + data_dir 指过来）+ data/jobs/<id>/job.json
      打印 job id 与可用的访问地址，用于浏览器验收。

9.6.7 起造 **3 张基准图**（3 个款式各一张），用来验收：
  · 多张基准图时的审核条布局（不能互相挤压）
  · 只框选其中一张时，服务端只针对那一张返工
  · 两张基准图同时框选改图（并发），互不串任务
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


# 款式 · 颜色 · 基准图（跟真实任务同款同色的命名习惯）
MASTERS = [
    ("26-cropped-sorona-tee", "26 露脐索罗娜", "white", "白色", (236, 234, 230),
     [("black", "黑色"), ("red", "红色")]),
    ("19-u-neck-tee", "19 U领T", "white", "白色", (232, 229, 224), [("black", "黑色")]),
    ("06-hoodie", "06 连帽卫衣", "black", "黑色", (58, 56, 62), [("white", "白色")]),
]


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

    tasks: list[dict] = []
    selections: list[dict] = []
    master_ids: list[str] = []
    derived_total = 0
    for slug, display, color, color_name, tint, derived in MASTERS:
        master_id = f"item-01__{slug}__{color}"
        master_ids.append(master_id)
        out_name = f"master-{slug}-{color}.png"
        master = make_png(outputs / out_name, f"BASE {slug}", tint)
        tasks.append({
            "id": master_id, "item": "item-01", "view": "view-01",
            "color": color, "color_hex": "#ECEAE6", "color_name": color_name,
            "source": "inputs/item-01/view-01.png",
            "garment": slug, "garment_name": display,
            "prompt": f"CONVERT THE GARMENT TYPE … {slug} … fixture …",
            "subject": "garment", "person_alert": False,
            "master": True, "master_task": "", "reference": "", "approved": False,
            "review_note": "", "adopted_from": "", "status": "done", "attempts": 1,
            "error": None, "error_kind": "", "out": f"outputs/{out_name}",
            "started_at": time.time() - 60, "finished_at": time.time() - 30,
            "bytes": master.stat().st_size,
        })
        selections.append({"garment_type": slug, "garment_name": display,
                           "colors": [color] + [c for c, _ in derived]})
        for other, other_name in derived:
            derived_total += 1
            tasks.append({
                "id": f"item-01__{slug}__{other}", "item": "item-01", "view": "view-01",
                "color": other, "color_hex": "#ECEAE6", "color_name": other_name,
                "source": "inputs/item-01/view-01.png",
                "garment": slug, "garment_name": display,
                "prompt": f"CONVERT THE GARMENT TYPE … {slug} … fixture …",
                "subject": "garment", "person_alert": False,
                "master": False, "master_task": master_id, "reference": "",
                "approved": False, "review_note": "", "adopted_from": "",
                "status": "pending", "attempts": 0, "error": None, "error_kind": "",
                "out": None, "started_at": None, "finished_at": None, "bytes": 0,
            })

    total = len(tasks)
    colour_total = sum(len(s["colors"]) for s in selections)
    payload = {
        "id": job_id, "status": "review",
        "stage": "基准图已出，等你确认（确认后按这张图改其余颜色）",
        "batch_name": "multi-style-recolor-batch",
        "zip": None, "has_zip": False, "error": None,
        "created_at": time.time() - 120, "updated_at": time.time() - 20, "finished_at": None,
        "duration": None,
        "stats": {"total": total, "pending": total - len(MASTERS), "queued": 0, "running": 0,
                  "done": len(MASTERS), "failed": 0, "skipped": 0, "canceled": 0,
                  "finished": len(MASTERS), "rate_limited": 0},
        "progress": round(100.0 * len(MASTERS) / max(1, total), 1),
        "params": {
            "garment_type": MASTERS[0][0], "garment_name": MASTERS[0][1],
            "colors": [MASTERS[0][2]] + [c for c, _ in MASTERS[0][5]],
            "selections": selections,
            "style_count": len(MASTERS), "total_colors": colour_total, "mode": "single_pass",
            "kind": "", "provider": "mock", "provider_label": "离线模拟",
            "model": "mock-image", "concurrency": 4, "rpm": 0, "size": "auto",
            "quality": "low", "seed": None, "max_retries": 1, "timeout_sec": 60,
            "output_format": "png", "send_reference": True, "input_fidelity": "",
            "max_bytes": 2000000, "extra_requirements": "", "pattern_mode": "preserve",
            "pattern_brief": "", "subject": "garment", "subject_choice": "garment",
            "retry_on_person": False, "shape_lock": "master", "review_gate": True,
            "account": "fixture",
            "estimate": {"model": "mock-image", "count": total, "unit": 0.0, "total": 0.0,
                         "currency": "CNY", "priced": True},
        },
        "tasks": tasks,
        "log": [{"t": time.time() - 100, "level": "info",
                 "msg": "（界面验收用的假任务，不花钱；3 个款式各一张基准图）"}],
        "review": {
            "enabled": True, "awaiting": True, "phase": "",
            "master_ids": master_ids,
            "pending_ids": list(master_ids),
            "approved_ids": [], "derived_total": derived_total, "derived_done": 0,
            "can_approve": True,
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
    print(f"款式   : {len(MASTERS)} 个 · 基准图 {len(master_ids)} 张 · 总任务 {total} 个")
    print("启动：python serve.py --config \"%s\" --port 8789" % (target / "config.json"))
    print(f"打开：http://127.0.0.1:8789/?token={token}&job={job_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
