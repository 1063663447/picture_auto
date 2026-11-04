r"""回归测试：staging 里出现缩略图缓存目录时，打包不能失败。

复现过的线上事故：
    06 连帽卫衣 · 3 色 · 1 张原图
    打包失败：ERROR: Item directory ...\staging\thumbs contains 2 images; expected 3.

原因：缩略图/压缩版缓存被写到了 `<job>/staging/thumbs`，而 skill 的
`package_outputs.py` 会把 staging 下**每个子目录**当成一个 item 数图。
修法：
    1. 缓存改写到 `<job>/thumbs`、`<job>/derived`（service.py）
    2. 打包前 `clean_staging()` 兜底清掉 staging 里的缓存目录（runner.py）
    3. 打包脚本自家 fallback 也跳过这些目录（packaging.py）

用法：python tools/check_package_thumbs.py
"""

from __future__ import annotations

import asyncio
import io
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from skillbridge.packaging import package_outputs  # noqa: E402
from skillbridge.runner import STAGING_JUNK_DIRS, clean_staging  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SKILL = (ROOT.parent / "apparel-style-recolor").resolve()
SCRIPT = SKILL / "scripts" / "package_outputs.py"
MAX_BYTES = 2_000_000

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILED.append(label)


def make_png(path: Path, color: tuple[int, int, int], size=(600, 800)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    path.write_bytes(buffer.getvalue())


def build_job(root: Path, *, with_cache: bool, colors: int = 3) -> Path:
    """造一个 staging：item-01 里有 colors 张成品，可选塞进 thumbs/derived 缓存目录。"""
    staging = root / "staging"
    for index in range(colors):
        make_png(staging / "item-01" / f"item-01_color-{index}.png", (30 + index * 60, 90, 120))
    if with_cache:
        for index in range(colors - 1):
            make_png(staging / "thumbs" / f"item-01_color-{index}@520.webp", (200, 200, 200))
        make_png(staging / "derived" / "item-01_color-0@full.jpg", (180, 180, 180))
    return staging


def zip_entries(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return sorted(archive.namelist())


def main() -> int:
    print("[1] 未清理时：staging 里的缓存目录会让打包失败（复现原 bug）")
    tmp = Path(tempfile.mkdtemp(prefix="wb-pkg-"))
    try:
        staging = build_job(tmp / "job-a", with_cache=True)
        result = asyncio.run(package_outputs(
            script=SCRIPT,
            staging_dir=staging,
            output_dir=tmp / "job-a" / "outputs",
            batch_name="demo-batch",
            max_bytes=MAX_BYTES,
            expected_per_item=3,
        ))
        check("打包如预期失败（说明 bug 机理是对的）", not result.ok, (result.error or "")[:80])
        check("报错点名了缓存目录",
              any(name in (result.error or "") for name in ("thumbs", "derived")),
              (result.error or "")[:60])

        print("[2] clean_staging() 之后：打包成功、ZIP 里只有成品")
        removed = clean_staging(staging)
        check("清掉了缓存目录", set(removed) == {"thumbs", "derived"}, ",".join(removed))
        check("缓存目录已消失", not (staging / "thumbs").exists() and not (staging / "derived").exists())
        check("成品目录没被动", len(list((staging / "item-01").glob("*.png"))) == 3)

        result = asyncio.run(package_outputs(
            script=SCRIPT,
            staging_dir=staging,
            output_dir=tmp / "job-a" / "outputs",
            batch_name="demo-batch",
            max_bytes=MAX_BYTES,
            expected_per_item=3,
        ))
        check("打包成功", result.ok, result.error or "")
        entries = zip_entries(result.zip_path) if result.zip_path else []
        check("ZIP 里是 3 张成品", len(entries) == 3, str(entries))
        check("ZIP 里没有缓存图", not any("thumbs" in e or "derived" in e for e in entries))

        print("[3] 用内置 fallback 打包时也不带缓存目录")
        from skillbridge.packaging import _fallback_zip  # noqa: PLC0415

        staging2 = build_job(tmp / "job-b", with_cache=True)
        fallback = _fallback_zip(staging2, tmp / "job-b" / "outputs", "fb-batch")
        entries = zip_entries(fallback.zip_path) if fallback.zip_path else []
        check("fallback 只打成品", len(entries) == 3, str(entries))

        print("[4] 常量约定")
        check("thumbs/derived 都在 JUNK 名单里", {"thumbs", "derived"} <= STAGING_JUNK_DIRS,
              ",".join(sorted(STAGING_JUNK_DIRS)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项失败：{FAILED}")
        return 1
    print("[OK] 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
