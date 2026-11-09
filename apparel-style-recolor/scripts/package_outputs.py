#!/usr/bin/env python3
"""Validate an apparel output tree and create a downloadable ZIP archive."""

from __future__ import annotations

import argparse
import io
import shutil
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_MAX_BYTES = 2_000_000
RESIZE_SCALES = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.25, 0.2, 0.15, 0.1)
JPEG_QUALITIES = (95, 90, 85, 80, 75, 70, 65, 60, 55, 50, 45, 40, 35, 30, 25, 20, 15, 10)
PNG_COLOR_COUNTS = (None, 256, 128, 64, 32, 16, 8, 4)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package per-item apparel recolor folders into a ZIP archive."
    )
    parser.add_argument(
        "--staging-dir",
        required=True,
        type=Path,
        help="Directory containing one subdirectory per uploaded image.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Parent directory for the final folder and ZIP.",
    )
    parser.add_argument(
        "--batch-name",
        default="apparel-recolor-batch",
        help="Name used for the final folder and ZIP file.",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"Strict maximum size for each packaged image (default: {DEFAULT_MAX_BYTES}).",
    )
    parser.add_argument(
        "--expected-images-per-item",
        type=int,
        help="Require exactly this many images in every item directory.",
    )
    return parser.parse_args()


def is_intermediate(path: Path) -> bool:
    stem = path.stem.lower().replace("-", "_")
    return stem.endswith("_clean") or stem.endswith("_base") or "intermediate" in stem


def validate_staging(
    staging_dir: Path, expected_images_per_item: int | None = None
) -> tuple[list[Path], list[Path]]:
    if not staging_dir.is_dir():
        raise FileNotFoundError(f"Staging directory does not exist: {staging_dir}")
    if expected_images_per_item is not None and expected_images_per_item <= 0:
        raise ValueError("expected-images-per-item must be greater than zero.")

    item_dirs = sorted(path for path in staging_dir.iterdir() if path.is_dir())
    if not item_dirs:
        raise ValueError("Staging directory must contain at least one item subdirectory.")

    image_files: list[Path] = []
    for item_dir in item_dirs:
        nested_dirs = [path for path in item_dir.iterdir() if path.is_dir()]
        if nested_dirs:
            names = ", ".join(path.name for path in nested_dirs)
            raise ValueError(
                f"Nested directories are not allowed in item directory {item_dir}: {names}"
            )
        files = sorted(
            path
            for path in item_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )
        if not files:
            raise ValueError(f"Item directory contains no supported images: {item_dir}")
        unsupported_files = [
            path
            for path in item_dir.iterdir()
            if path.is_file() and path.suffix.lower() not in IMAGE_EXTENSIONS
        ]
        if unsupported_files:
            names = ", ".join(path.name for path in unsupported_files)
            raise ValueError(
                f"Unsupported files found in item directory {item_dir}: {names}. "
                "Only PNG, JPG, JPEG, and WEBP images are allowed."
            )
        if (
            expected_images_per_item is not None
            and len(files) != expected_images_per_item
        ):
            raise ValueError(
                f"Item directory {item_dir} contains {len(files)} images; "
                f"expected {expected_images_per_item}."
            )
        intermediate_files = [path for path in files if is_intermediate(path)]
        if intermediate_files:
            names = ", ".join(path.name for path in intermediate_files)
            raise ValueError(
                f"Intermediate images found in final item directory {item_dir}: {names}. "
                "Move them outside item folders before packaging."
            )
        image_files.extend(files)
    return item_dirs, image_files


def paths_overlap(first: Path, second: Path) -> bool:
    """Return whether either path is inside the other path."""
    first = first.resolve()
    second = second.resolve()
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _resize_for_scale(image: Image.Image, scale: float) -> Image.Image:
    if scale == 1.0:
        return image
    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _encode_image(
    image: Image.Image,
    suffix: str,
    quality: int | None = None,
    colors: int | None = None,
) -> bytes:
    buffer = io.BytesIO()
    suffix = suffix.lower()

    if suffix in {".jpg", ".jpeg"}:
        output = image.convert("RGB")
        output.save(
            buffer,
            format="JPEG",
            quality=quality or 85,
            optimize=True,
            progressive=True,
        )
    elif suffix == ".webp":
        output = image
        output.save(
            buffer,
            format="WEBP",
            quality=quality or 85,
            method=6,
        )
    elif suffix == ".png":
        output = image
        if colors is not None:
            if output.mode in {"RGBA", "LA"}:
                output = output.convert("RGBA").quantize(
                    colors=colors,
                    method=Image.Quantize.FASTOCTREE,
                    dither=Image.Dither.FLOYDSTEINBERG,
                )
            else:
                output = output.convert("RGB").quantize(
                    colors=colors,
                    method=Image.Quantize.MEDIANCUT,
                    dither=Image.Dither.FLOYDSTEINBERG,
                )
        output.save(
            buffer,
            format="PNG",
            optimize=True,
            compress_level=9,
        )
    else:
        raise ValueError(f"Unsupported image extension: {suffix}")

    return buffer.getvalue()


def _compressed_bytes(image: Image.Image, suffix: str, max_bytes: int) -> bytes:
    image = ImageOps.exif_transpose(image)
    suffix = suffix.lower()

    if suffix in {".jpg", ".jpeg", ".webp"}:
        for scale in RESIZE_SCALES:
            scaled = _resize_for_scale(image, scale)
            for quality in JPEG_QUALITIES:
                data = _encode_image(scaled, suffix, quality=quality)
                if len(data) < max_bytes:
                    return data
    elif suffix == ".png":
        for scale in RESIZE_SCALES:
            scaled = _resize_for_scale(image, scale)
            for colors in PNG_COLOR_COUNTS:
                data = _encode_image(scaled, suffix, colors=colors)
                if len(data) < max_bytes:
                    return data
    else:
        raise ValueError(f"Unsupported image extension: {suffix}")

    raise ValueError(
        f"Could not compress image below {max_bytes} bytes without more aggressive processing."
    )


def copy_or_compress_image(
    source: Path, destination: Path, max_bytes: int
) -> bool:
    """Copy small images unchanged; recompress oversized images.

    Returns True when the image was recompressed.
    """
    if source.stat().st_size < max_bytes:
        shutil.copy2(source, destination)
        return False

    try:
        with Image.open(source) as image:
            image.load()
            data = _compressed_bytes(image, source.suffix, max_bytes)
    except Exception as exc:
        raise ValueError(f"Unable to compress image {source}: {exc}") from exc

    destination.write_bytes(data)
    return True


def create_archive(
    staging_dir: Path,
    output_dir: Path,
    batch_name: str,
    item_dirs: list[Path],
    max_bytes: int,
) -> tuple[Path, Path, int, int]:
    safe_name = Path(batch_name).name
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError("batch-name must be a simple folder name.")
    if max_bytes <= 0:
        raise ValueError("max-bytes must be greater than zero.")

    output_dir.mkdir(parents=True, exist_ok=True)
    final_dir = output_dir / safe_name
    zip_path = output_dir / f"{safe_name}.zip"

    if final_dir.exists():
        shutil.rmtree(final_dir)
    if zip_path.exists():
        zip_path.unlink()

    final_dir.mkdir()
    recompressed_count = 0
    total_files = sum(1 for item_dir in item_dirs for path in item_dir.iterdir() if path.is_file())
    processed = 0
    for item_dir in item_dirs:
        destination = final_dir / item_dir.name
        destination.mkdir()
        for source in sorted(item_dir.iterdir()):
            target = destination / source.name
            print(f"PACKAGING {processed}/{total_files} {source.name}", flush=True)
            if source.is_file() and source.suffix.lower() in IMAGE_EXTENSIONS:
                if copy_or_compress_image(source, target, max_bytes):
                    recompressed_count += 1
            elif source.is_file():
                shutil.copy2(source, target)
            else:
                shutil.copytree(source, target)
            if source.is_file():
                processed += 1
                print(f"PACKAGING {processed}/{total_files} {source.name}", flush=True)

    packaged_images = [
        path
        for path in final_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    oversized = [path for path in packaged_images if path.stat().st_size >= max_bytes]
    if oversized:
        names = ", ".join(str(path.relative_to(final_dir)) for path in oversized)
        raise ValueError(f"Packaged images are still too large: {names}")

    print(f"PACKAGING {processed}/{total_files} ZIP", flush=True)
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for path in sorted(final_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir))

    return final_dir, zip_path, recompressed_count, max(
        (path.stat().st_size for path in packaged_images), default=0
    )


def main() -> int:
    args = parse_args()
    try:
        staging_dir = args.staging_dir.resolve()
        output_dir = args.output_dir.resolve()
        safe_batch_name = Path(args.batch_name).name
        if not safe_batch_name or safe_batch_name in {".", ".."}:
            raise ValueError("batch-name must be a simple folder name.")
        final_dir = output_dir / safe_batch_name
        if paths_overlap(staging_dir, final_dir):
            raise ValueError(
                "The final output directory must not overlap the staging directory."
            )
        item_dirs, image_files = validate_staging(
            staging_dir, args.expected_images_per_item
        )
        final_dir, zip_path, recompressed_count, largest_image_bytes = create_archive(
            staging_dir,
            output_dir,
            args.batch_name,
            item_dirs,
            args.max_bytes,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Final directory: {final_dir}")
    print(f"Images packaged: {len(image_files)}")
    print(f"Images recompressed: {recompressed_count}")
    print(f"Largest packaged image: {largest_image_bytes} bytes")
    print(f"ZIP archive: {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
