"""把「模型定价」表按 pricing.json 重刷一遍，并把数字列格式固定成三位小数。

- 数字列（单价元 / 1K档 / 2K档 / 4K档 / 其他档）统一设置 `formatter: 0.000`，飞书里就显示 0.150 / 0.038 / 15.000
- 逐模型 upsert（按「模型」列匹配），多余的手工行不动
- 备注写明控制台原始报价口径；按 token 计价的模型（gpt-image-2-native）保持未定价并说明原因

用法：
  python tools/feishu_pricing_sync.py --dry-run
  python tools/feishu_pricing_sync.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import feishu  # noqa: E402
from skillbridge.config import WORKBENCH_DIR, load_config, load_env  # noqa: E402
from skillbridge.feishu import BASE, _json_request  # noqa: E402

NUMBER_FIELDS = ("单价元", "1K档", "2K档", "4K档", "其他档")
FORMATTER = "0.000"

EXTRA_NOTES = {
    "gpt-image-2-native": "按 token 计价：输入 15.000/1M、输出 60.000/1M（默认输出 3240×3240），无法按张预估 → 未定价",
    "seedvr2-7b": "SeedVR2 图片超分模型：1M 分辨率 0.0060 每次调用，其他分辨率 0.0080",
}


def text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(str(x.get("text") or "") if isinstance(x, dict) else str(x) for x in value)
    return str(value)


def build_row(model: str, entry) -> dict:
    row: dict = {"模型": model, "启用": "是"}
    for name in NUMBER_FIELDS:
        row[name] = None
    if isinstance(entry, dict) and entry.get("tiers"):
        tiers = {str(k): float(v) for k, v in entry["tiers"].items() if v is not None}
        for tier in ("1K", "2K", "4K"):
            if tier in tiers:
                row[f"{tier}档"] = round(tiers[tier], 3)
        other = entry.get("other")
        if other is not None:
            row["其他档"] = round(float(other), 3)
        parts = [f"{k} {tiers[k]:.3f}" for k in ("1K", "2K", "4K") if k in tiers]
        if other is not None:
            parts.append(f"其他分辨率 {float(other):.3f}")
        row["备注"] = "按分辨率分档（控制台每次调用）：" + " / ".join(parts)
    elif isinstance(entry, (int, float)):
        row["单价元"] = round(float(entry), 3)
        row["备注"] = f"每次调用 {float(entry):.3f}（控制台）"
    else:
        row["备注"] = EXTRA_NOTES.get(model, "控制台未展示此变体 → 未定价")
    if model in EXTRA_NOTES and not (isinstance(entry, dict) and entry.get("tiers")):
        row["备注"] = EXTRA_NOTES[model]
    return row


def fix_number_formats(client, fcfg) -> None:
    fields = _json_request(
        "GET",
        f"{BASE}/bitable/v1/apps/{fcfg.app_token}/tables/{fcfg.price_table_id}/fields?page_size=100",
        client.token(),
    )
    items = (fields.get("data") or {}).get("items") or []
    for item in items:
        name = str(item.get("field_name"))
        if name not in NUMBER_FIELDS:
            continue
        property_ = dict(item.get("property") or {})
        if property_.get("formatter") == FORMATTER and int(item.get("type") or 0) == 2:
            print(f"    [=] {name} 已是 {FORMATTER}")
            continue
        property_["formatter"] = FORMATTER
        result = _json_request(
            "PUT",
            f"{BASE}/bitable/v1/apps/{fcfg.app_token}/tables/{fcfg.price_table_id}"
            f"/fields/{item['field_id']}",
            client.token(),
            {"field_name": name, "type": 2, "property": property_},
        )
        print(
            f"    [{'ok' if result.get('code') == 0 else 'x'}] {name} formatter={FORMATTER}"
            f"{'' if result.get('code') == 0 else ' ' + str(result.get('msg'))}"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="按 pricing.json 重刷飞书「模型定价」表")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pricing", default=str(WORKBENCH_DIR / "pricing.json"))
    args = parser.parse_args(argv)

    env = load_env()
    cfg = load_config(None, env)
    fcfg = feishu.load_config(cfg, env)
    ok, reason = fcfg.ready()
    if not ok or not fcfg.price_table_id:
        print(f"[x] 飞书未就绪：{reason}（或 feishu.price_table_id 为空）")
        return 2

    payload = json.loads(Path(args.pricing).read_text(encoding="utf-8"))
    models = payload.get("models") or {}
    client = feishu.FeishuBitable(fcfg)

    print("[*] 固定数字列格式为三位小数")
    if not args.dry_run:
        fix_number_formats(client, fcfg)

    rows = {name: build_row(name, entry) for name, entry in models.items()}
    print(f"[*] 准备写入 {len(rows)} 个模型的报价")

    existing = client.search_table(fcfg.price_table_id, page_size=200)
    by_model: dict[str, dict] = {}
    for item in existing:
        name = text((item.get("fields") or {}).get("模型")).strip()
        if name:
            by_model[name] = item

    created = updated = 0
    for name, row in rows.items():
        # 数字列要显式写 None（飞书里传 null = 清空），否则旧的分档价会残留
        clean = dict(row)
        item = by_model.get(name)
        if item:
            if args.dry_run:
                print(f"    [dry] 更新 {name}: {clean}")
                updated += 1
                continue
            client.update_record(fcfg.price_table_id, item["record_id"], clean)
            print(f"    [ok] 更新 {name}: 单价={clean.get('单价元', '—')} 备注={str(clean.get('备注') or '')[:40]}")
            updated += 1
        else:
            if args.dry_run:
                print(f"    [dry] 新建 {name}: {clean}")
                created += 1
                continue
            client.create_records(
                [{k: v for k, v in clean.items() if v is not None}], table_id=fcfg.price_table_id
            )
            print(f"    [ok] 新建 {name}")
            created += 1

    print(f"[*] 完成：更新 {updated}，新建 {created}")
    if not args.dry_run:
        pricing = __import__("skillbridge.pricing", fromlist=["Pricer"]).Pricer()
        pricing.refresh_from_feishu(fcfg, force=True)
        print("[*] 复核（来自飞书）：")
        for name in ("gpt-image-2", "gpt-image-2-auto", "grok-imagine-image-2.0",
                     "grok-imagine-image-quality", "seedvr2-7b", "gpt-image-2-native"):
            print(f"    {name:<30} {pricing.price(name, '1024x1536').label():<14}"
                  f" @2048x2048 → {pricing.price(name, '2048x2048').label()}")
        print(f"    表格：https://feishu.cn/base/{fcfg.app_token}?table={fcfg.price_table_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
