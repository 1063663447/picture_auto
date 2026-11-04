"""Run the skill's own `scripts/package_outputs.py` to validate and zip outputs.

The script is used as-is (subprocess), so the skill remains the authority on
folder structure, the 2,000,000-byte limit, and ZIP layout. If the script cannot
run (missing Pillow, moved file), we fall back to a plain ZIP so the operator
still gets the images, and say so in the job log.
"""

from __future__ import annotations

import asyncio
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
# staging 下只应有「每个 item 一个目录」；这些是缓存目录，绝不能进 ZIP
JUNK_DIRS = {"thumbs", "derived", "exports", ".cache"}


@dataclass
class PackageResult:
    ok: bool
    zip_path: Path | None = None
    error: str | None = None
    info: dict = field(default_factory=dict)


def _fallback_zip(staging_dir: Path, output_dir: Path, batch_name: str) -> PackageResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"{batch_name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    count = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(staging_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            relative = path.relative_to(staging_dir)
            if any(part in JUNK_DIRS for part in relative.parts[:-1]):
                continue        # 缩略图/压缩版缓存不进 ZIP
            archive.write(path, Path(batch_name) / relative)
            count += 1
    return PackageResult(
        ok=count > 0,
        zip_path=zip_path if count else None,
        error=None if count else "没有可打包的图片",
        info={"fallback": True, "images": count},
    )


async def package_outputs(
    *,
    script: Path,
    staging_dir: Path,
    output_dir: Path,
    batch_name: str,
    max_bytes: int,
    expected_per_item: int | None = None,
) -> PackageResult:
    if not script.is_file():
        result = _fallback_zip(staging_dir, output_dir, batch_name)
        result.info["note"] = f"未找到 {script}，已使用内置 ZIP 打包"
        return result

    command = [
        sys.executable,
        str(script),
        "--staging-dir",
        str(staging_dir),
        "--output-dir",
        str(output_dir),
        "--batch-name",
        batch_name,
        "--max-bytes",
        str(max_bytes),
    ]
    if expected_per_item:
        command += ["--expected-images-per-item", str(expected_per_item)]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=900)
    except (OSError, asyncio.TimeoutError) as exc:
        result = _fallback_zip(staging_dir, output_dir, batch_name)
        result.info["note"] = f"打包脚本执行失败（{exc}），已使用内置 ZIP 打包"
        return result

    out = stdout.decode("utf-8", "replace").strip()
    err = stderr.decode("utf-8", "replace").strip()
    if process.returncode != 0:
        return PackageResult(ok=False, error=err or out or "打包脚本返回非零退出码")

    zip_path = output_dir / f"{batch_name}.zip"
    return PackageResult(
        ok=zip_path.is_file(),
        zip_path=zip_path if zip_path.is_file() else None,
        error=None if zip_path.is_file() else "打包脚本未生成 ZIP",
        info={"lines": [line for line in out.splitlines() if line.strip()], "stderr": err},
    )
