"""看飞书并发闸门表当前占位（等同 python -m skillbridge.feishu_gate --check）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillbridge.feishu_gate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["--check"]))
