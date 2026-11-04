"""打印飞书「模型定价」表当前内容（核对三位小数与分档）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import feishu  # noqa: E402
from skillbridge.config import load_config, load_env  # noqa: E402


def t(value) -> str:
    if isinstance(value, list):
        return "".join(str(x.get("text") or "") for x in value if isinstance(x, dict))
    return "" if value is None else str(value)


def main() -> int:
    env = load_env()
    cfg = load_config(None, env)
    fcfg = feishu.load_config(cfg, env)
    rows = feishu.FeishuBitable(fcfg).search_table(fcfg.price_table_id, page_size=200)
    print(f"定价表共 {len(rows)} 行")
    header = f"{'模型':<30}{'单价':>8}{'1K':>8}{'2K':>8}{'4K':>8}{'其他':>8}  备注"
    print(header)
    print("-" * len(header))
    for item in sorted(rows, key=lambda r: t((r.get("fields") or {}).get("模型"))):
        f = item.get("fields") or {}
        print(
            f"{t(f.get('模型')):<30}"
            f"{(t(f.get('单价元')) or '—'):>8}"
            f"{(t(f.get('1K档')) or '—'):>8}"
            f"{(t(f.get('2K档')) or '—'):>8}"
            f"{(t(f.get('4K档')) or '—'):>8}"
            f"{(t(f.get('其他档')) or '—'):>8}  {t(f.get('备注'))[:46]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
