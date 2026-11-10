# -*- coding: utf-8 -*-
"""把客户给的 30#ZRT 扎染T 图片（尺寸表+颜色图）里三张商品图裁出来，做 30 款的参考图/效果图。

原图 1200×2447：下半部分有 3 张平铺商品图（白 / 藕粉 / 奶茶）。
做法：按大致区域切块 → 自动去掉白边 → 补成正方形 → 输出
  library/effects/30-tie-dye-tee/<色号>.jpg   （效果图，版型+颜色的权威参考）
  library/refs/30-tie-dye-tee/<label>.jpg     （界面缩略图 + 兜底参考图）

用法：python tools/add_style_30_tiedye.py --image=<图片> [--dry-run]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"
SLUG = "30-tie-dye-tee"

#: 名称 → (色号, 英文 label, 大致裁剪框（占整图的比例 x0,y0,x1,y1）)
#: 注意：要把「COLOR SELECTION」标题和下面的颜色名切在外面，否则会被当成印花
COLOURS = [
    ("白色", "WH[0]", "white", (0.075, 0.552, 0.515, 0.742)),
    ("藕粉色", "LPK[0]", "lotus-pink", (0.515, 0.552, 0.975, 0.742)),
    ("奶茶色", "MTC[0]", "milk-tea", (0.115, 0.772, 0.605, 0.950)),
]
WHITE_CUT = 236          # 背景白：R/G/B 都 ≥ 这个值就算背景
SIZE = 1000


def trim_white(image: Image.Image, pad: int = 24) -> Image.Image:
    """裁掉四边纯白，再留一点白边（商品图统一白底，方便当效果图）。"""
    rgb = image.convert("RGB")
    width, height = rgb.size
    pixels = rgb.load()
    left, top, right, bottom = width, height, -1, -1
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            r, g, b = pixels[x, y]
            if r < WHITE_CUT or g < WHITE_CUT or b < WHITE_CUT:
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
    if right <= left or bottom <= top:
        return rgb
    box = (
        max(0, left - pad),
        max(0, top - pad),
        min(width, right + pad),
        min(height, bottom + pad),
    )
    return rgb.crop(box)


def square(image: Image.Image, size: int = SIZE) -> Image.Image:
    """补成正方形并统一尺寸（白底）。"""
    width, height = image.size
    side = max(width, height)
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(image, ((side - width) // 2, (side - height) // 2))
    return canvas.resize((size, size), Image.LANCZOS)


def main() -> int:
    image_path = ""
    for item in sys.argv[1:]:
        if item.startswith("--image="):
            image_path = item.split("=", 1)[1]
    dry = "--dry-run" in sys.argv
    if not image_path:
        print("请给 --image=<客户图片路径>")
        return 2
    source = Image.open(image_path)
    source = ImageOps.exif_transpose(source).convert("RGB")
    print(f"源图 {source.size[0]}×{source.size[1]}")
    width, height = source.size

    eff_dir = LIB / "effects" / SLUG
    ref_dir = LIB / "refs" / SLUG
    out_dir = ROOT.parent / "scratch" / "style-30-preview"
    out_dir.mkdir(parents=True, exist_ok=True)

    total_white = 0
    for name_zh, code, label, box in COLOURS:
        left, top = int(box[0] * width), int(box[1] * height)
        right, bottom = int(box[2] * width), int(box[3] * height)
        chunk = source.crop((left, top, right, bottom))
        trimmed = trim_white(chunk)
        square_img = square(trimmed)
        # 白底占比：判断这张是不是真的把衣服切全了（白件容易切到纯白）
        data = square_img.convert("L").getdata()
        light = sum(1 for value in data if value >= WHITE_CUT)
        ratio = light / max(1, len(data))
        print(f"  {name_zh:<4} {code:<7} 裁 {trimmed.size[0]}×{trimmed.size[1]} → {SIZE}² "
              f"白底 {ratio:.0%}")
        if ratio > 0.93:
            print(f"    [!] {name_zh} 白底太多，可能没切到衣服（白色款正常会偏高）")
            total_white += 1
        square_img.save(out_dir / f"{label}.png")
        if dry:
            continue
        eff_dir.mkdir(parents=True, exist_ok=True)
        ref_dir.mkdir(parents=True, exist_ok=True)
        square_img.save(eff_dir / f"{code}.jpg", format="JPEG", quality=92, optimize=True)
        thumb = square_img.copy()
        thumb.thumbnail((900, 900), Image.LANCZOS)
        thumb.save(ref_dir / f"{label}.jpg", format="JPEG", quality=88, optimize=True)
    print(f"\n预览图：{out_dir}（先看一眼切得对不对）")
    if dry:
        print("[dry-run] 没有写入 library/")
    else:
        print(f"已写入 {eff_dir} 与 {ref_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
