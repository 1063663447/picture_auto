"""把两个连帽款式的专属提示词 JSON 里的帽绳文案换成 9.6.26 的规格。

只动帽绳那两段（正文里「HOOD CORD COLOUR — …」/「HOOD DRAWSTRING — …」和负面里的同色系句子），
其它字节不动 —— 避免用 build_style_prompts.py --all 覆盖运营手改过的别的款式。

可重复执行：
  * 正文还没有新段落 → 用正则把旧段落整段换掉；旧段落不在且新段落也不在 → 报错
  * 负面缺这句 → 追加；已有 → 替换

用法：python tools/patch_hood_cords_9_6_26.py [--dry]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillbridge import prompts  # noqa: E402

SLUGS: tuple[str, ...] = ("06-hoodie", "hoodie")
OLD_POS = re.compile(r"HOOD CORD COLOUR — .*?not another colourway\.", re.S)
OLD_NEG = re.compile(
    r"Do not give the hood cords a colour from outside the garment's colour family.*?"
    r"do not leave the cords uncoloured while the garment is recoloured\.",
    re.S,
)
NEW_NEG = re.compile(r"Do not colour the cord body anything other than.*?eyelets or the hood\.", re.S)


def main() -> int:
    dry = "--dry" in sys.argv
    for slug in SLUGS:
        path = ROOT / "library" / "prompts" / f"{slug}.json"
        if not path.is_file():
            print(f"  跳过 {slug}：文件不存在")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        pos = str(data.get("positive") or "")
        neg = str(data.get("negative") or "")

        if "HOOD DRAWSTRING" in pos:
            pos_note = "正文已是新规格"
        else:
            pos, hits = OLD_POS.subn(prompts.DRAWSTRING_CLAUSE, pos)
            if not hits:
                print(f"  ✗ {slug}：正文里既没有旧帽绳段落也没有新段落，不敢动")
                return 1
            data["positive"] = pos
            pos_note = f"正文替换 {hits} 处"

        if NEW_NEG.search(neg):
            neg, hits = NEW_NEG.subn(prompts.DRAWSTRING_NEGATIVE, neg)
            neg_note = f"负面替换 {hits} 处"
        elif OLD_NEG.search(neg):
            neg, hits = OLD_NEG.subn(prompts.DRAWSTRING_NEGATIVE, neg)
            neg_note = f"负面替换旧句 {hits} 处"
        else:
            neg = (neg.rstrip() + " " + prompts.DRAWSTRING_NEGATIVE).strip()
            neg_note = "负面补上帽绳句"
        data["negative"] = neg

        print(f"  {'(dry) ' if dry else ''}{slug}: {pos_note}，{neg_note}")
        if not dry:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
