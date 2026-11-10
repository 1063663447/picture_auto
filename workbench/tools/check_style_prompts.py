"""款式级提示词回归：每个款式一套专属默认提示词，互不串味。

背景（2026-11-10）：客户要求「每个款式对应一套专属默认提示词」，后面还要加十几个款式。
现在的结构：
  * 共享骨架（代码里）：图案锁、材质锁、效果图/色卡分工、主体/禁人物规则
  * 款式专属核心（library/prompts/<slug>.json）：positive（怎么拆源图、怎么建目标款）+ negative
没有 json 的款式回落到「按属性拼装」，所以新款式可以先跑起来再补文件。

用法：python tools/check_style_prompts.py [--show]
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import prompts  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402
from skillbridge.style_prompts import (  # noqa: E402
    load_all,
    load_style_prompt,
    prompt_path,
    save_style_prompt,
    StylePrompt,
)

FAILURES: list[str] = []


def check(ok: bool, label: str, show: bool) -> None:
    if not ok:
        FAILURES.append(label)
        print(f"  [x] {label}")
    elif show:
        print(f"  [ok] {label}")


def main() -> int:
    show = "--show" in sys.argv
    wb = Workbench()
    library = wb.library_dir

    print("① 覆盖：每个可选款式都有专属提示词")
    prompts_on_disk = load_all(library)
    missing = [slug for slug in wb.skill.types if slug not in prompts_on_disk]
    check(not missing, f"没有缺文件的款式（缺 {missing}）", show)
    check(len(prompts_on_disk) >= 10, f"专属提示词份数 {len(prompts_on_disk)}（≥10）", show)

    print("② 生效：文件内容真的进了提示词")
    for slug in sorted(wb.skill.types)[:3]:
        garment = wb.skill.types[slug]
        check(bool(garment.style_positive), f"{slug}：加载器把专属提示词挂到了款式上", show)
        built = prompts.build_single_pass(wb.skill, garment, garment.colors[0], subject="person")
        marker = garment.style_positive.split(".")[0][:40]
        check(marker in built, f"{slug}：专属提示词出现在最终提示词里", show)
        negative = prompts.full_negative(wb.skill, "preserve", garment, "person")
        neg_marker = (garment.style_negative or "").split(".")[0][:30]
        if neg_marker:
            check(neg_marker in negative, f"{slug}：专属否定词出现在负面词里", show)

    print("③ 隔离：改一个款式不影响别的款式")
    slug_a, slug_b = "06-hoodie", "23-sorona-tee"
    garment_a = wb.skill.type_by_alias(slug_a)
    garment_b = wb.skill.type_by_alias(slug_b)
    before_b = prompts.build_single_pass(wb.skill, garment_b, garment_b.colors[0], subject="person")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        (tmp_root / "prompts").mkdir(parents=True)
        for path in (library / "prompts").glob("*.json"):
            shutil.copy2(path, tmp_root / "prompts" / path.name)
        marker = "UNIQUE-STYLE-MARKER-FOR-TEST"
        patched = load_style_prompt(tmp_root, slug_a)
        assert patched is not None
        patched.positive = patched.positive + " " + marker
        save_style_prompt(tmp_root, patched)
        reloaded = load_style_prompt(tmp_root, slug_a)
        check(reloaded is not None and marker in reloaded.positive, "临时目录里能写入并读回专属提示词", show)
        other = load_style_prompt(tmp_root, slug_b)
        check(other is not None and marker not in other.positive, "同一次写入没有污染别的款式", show)
    after_b = prompts.build_single_pass(wb.skill, garment_b, garment_b.colors[0], subject="person")
    check(before_b == after_b, "别的款式提示词完全没变", show)

    print("④ 新增款式：可以先把文件放进去，再让加载器读到")
    sample = StylePrompt(slug="fixture-style", positive="GARMENT TYPE LOCK — fixture only.",
                         negative="Do not change the fixture.", notes="测试用")
    with tempfile.TemporaryDirectory() as tmp:
        saved = save_style_prompt(tmp, sample)
        check(saved.is_file(), "能落盘一个新的款式提示词文件", show)
        loaded = load_style_prompt(tmp, "fixture-style")
        check(loaded is not None and loaded.positive == sample.positive, "能按 slug 读回来", show)
        raw = json.loads(saved.read_text(encoding="utf-8"))
        check(set(raw) == {"slug", "notes", "positive", "negative"}, f"文件字段固定：{sorted(raw)}", show)

    print("⑤ 回退：没有文件的款式仍然能出提示词（按属性拼装）")
    bare = type(wb.skill.type_by_alias("24-long-sleeve"))(
        **{**wb.skill.type_by_alias("24-long-sleeve").__dict__,
           "style_positive": "", "style_negative": ""}
    )
    built = prompts.build_single_pass(wb.skill, bare, bare.colors[0], subject="person")
    check("CONVERT THE GARMENT TYPE" in built, "无专属文件时回落到按属性拼装", show)
    negative = prompts.full_negative(wb.skill, "preserve", bare, "person")
    check("rib-knit" in negative.lower(), "无专属文件时否定词也能按属性拼", show)

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for item in FAILURES:
            print("  -", item)
        return 1
    print(f"全部通过：{len(prompts_on_disk)} 个款式各有专属提示词，互不影响，缺文件可回落")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
