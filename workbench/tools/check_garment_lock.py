"""款式锁 + 模型列表过滤 的回归检查（离线，不花钱）。

两件事：
1. **款式锁**：2026-11-09 飞书改款记录 1382~1390 出现「基准色对、派生色变成卫衣」，
   原因是派生色（跟随基准只改颜色）的提示词里既没有版型描述、也没有 no hood。
   现在所有非卫衣款式的基准/派生提示词都必须带 `GARMENT TYPE LOCK` 且列明 no hood / no drawstrings /
   no kangaroo pocket，否定词也要有；卫衣款式反过来不许出现「no hood」。
2. **模型列表只列有报价的**：备用接口（65535）原来把网关 36 个模型全铺进下拉，
   客户要求按飞书「模型定价」表来读；现在 `list_models` 默认过滤掉没报价的，
   可用 `include_unpriced=true` 看全量。

用法：
    python tools/check_garment_lock.py            # 离线断言
    python tools/check_garment_lock.py --show     # 打印每条判定
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import prompts  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

FAILURES: list[str] = []
HOODIE = {"06-hoodie"}
PLAIN_STYLES = {"23-sorona-tee", "07-washed-tee", "10-stretch-tee", "21-kids-sorona-tee",
                "24-long-sleeve", "25-kids-long-sleeve", "29-kids-washed-tee"}
SHORT_SLEEVE = {"23-sorona-tee", "07-washed-tee", "10-stretch-tee", "21-kids-sorona-tee",
                "29-kids-washed-tee"}


def check(ok: bool, label: str, show: bool) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


def main() -> int:
    show = "--show" in sys.argv
    wb = Workbench()

    print("① 非卫衣款式：基准 + 派生提示词都要有款式锁和 no hood")
    for slug in sorted(PLAIN_STYLES | {"03-crewneck-sweatshirt", "28-kids-sweatshirt"}):
        garment = wb.skill.type_by_alias(slug)
        color = garment.colors[min(1, len(garment.colors) - 1)]
        master = prompts.build_single_pass(wb.skill, garment, color, subject="auto")
        derived = prompts.build_master_recolor(color, garment=garment, subject="auto", effect=True)
        negative = prompts.full_negative(wb.skill, "preserve", garment, "auto")
        check("GARMENT TYPE LOCK" in master, f"{slug}：基准提示词含款式锁", show)
        check("GARMENT TYPE LOCK" in derived, f"{slug}：派生提示词含款式锁", show)
        check("no hood" in master.lower(), f"{slug}：基准提示词写明 no hood", show)
        check("no hood" in derived.lower(), f"{slug}：派生提示词写明 no hood", show)
        check("no drawstrings" in derived.lower(), f"{slug}：派生提示词写明 no drawstrings", show)
        check("do not add hood" in negative.lower(), f"{slug}：否定词含 Do not add hood", show)
        if slug in PLAIN_STYLES:
            check("no rib-knit cuffs" in derived.lower(), f"{slug}：平口款写明 no rib-knit cuffs", show)
        if slug in SHORT_SLEEVE:
            check("no long sleeves" in derived.lower(), f"{slug}：短袖款写明 no long sleeves", show)
        check(
            prompts.garment_lock(garment).count(garment.slug) == 0,
            f"{slug}：款式锁里写的是版型描述而不是 slug", show,
        )

    print("② 卫衣款式：不许出现「no hood」")
    for slug in sorted(HOODIE):
        garment = wb.skill.type_by_alias(slug)
        color = garment.colors[0]
        derived = prompts.build_master_recolor(color, garment=garment, subject="auto", effect=True)
        lock = prompts.garment_lock(garment)
        check("no hood" not in lock.lower(), f"{slug}：卫衣的款式锁不提 no hood", show)
        check("hoodie" in lock.lower(), f"{slug}：款式锁写明是 hoodie", show)
        check("GARMENT TYPE LOCK" in derived, f"{slug}：派生提示词仍有款式锁", show)

    print("③ 长袖/短袖互斥")
    long_tee = wb.skill.type_by_alias("24-long-sleeve")
    short_tee = wb.skill.type_by_alias("23-sorona-tee")
    check("no short sleeves" in prompts.garment_forbidden(long_tee).lower(), "长袖款禁短袖", show)
    check("no long sleeves" in prompts.garment_forbidden(short_tee).lower(), "短袖款禁长袖", show)

    print("④ 模型列表默认只列有报价的")
    fake_models = [
        {"id": "priced-a", "image": True, "price": 0.038, "price_label": "¥0.038/张", "current": False},
        {"id": "free-b", "image": True, "price": None, "price_label": "未定价", "current": False},
        {"id": "current-c", "image": True, "price": None, "price_label": "未定价", "current": True},
    ]
    payload = {
        "provider": "fixture",
        "models": fake_models,
        "count": len(fake_models),
        "image_models": [m["id"] for m in fake_models],
        "current": "current-c",
    }
    provider = SimpleNamespace(setting=lambda key, default=None: default)
    filtered = wb._keep_priced_models(payload, provider)
    ids = [m["id"] for m in filtered["models"]]
    check("priced-a" in ids, "有报价的模型保留", show)
    check("free-b" not in ids, "没报价的模型被过滤", show)
    check("current-c" in ids, "当前在用的模型一定保留（否则下拉框空）", show)
    check(filtered.get("filtered_unpriced") == 1, "记录了隐藏数量", show)
    check(filtered["count"] == 2, "count 跟着过滤结果走", show)
    disabled = SimpleNamespace(setting=lambda key, default=None: False)
    keep_all = wb._keep_priced_models(payload, disabled)
    check(len(keep_all["models"]) == 3, "provider 关掉过滤时不过滤", show)

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("全部通过：款式锁生效 + 模型列表只列有报价的")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
