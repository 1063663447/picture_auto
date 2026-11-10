"""把 5 张产品展示图拼成一张「总览大图」，并加中文说明条。

图片本身是生图接口直出的，这里只做排版和说明文字（用系统雅黑渲染，保证中文不乱码）。

用法：
    python tools/make_showcase_sheet.py --dir E:\\picture_auto\\workbench\\showcase
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CAPTIONS = {
    "01-hero": ("整体界面", "创作空间 · 10 款 / 49 色 · 批量队列 · 一键生成"),
    "02-styles": ("款式与色彩", "左侧款式面板 + 右侧真实批量成品（同一原图 × 多个款式）"),
    "03-prompt": ("款式专属提示词", "每个款式一套默认提示词，这是实际发送给模型的全文"),
    "04-batch": ("批量与对账", "12 张成品网格 + 历史任务批次（每批的完成数 / 总数）"),
    "05-recolor": ("原图 → 多色成品", "同一人物、同一版型、同一印花，只换面料颜色"),
}

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhl.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]
BOLD_CANDIDATES = [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf"]


def _font(paths: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in paths:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=r"E:\picture_auto\workbench\showcase")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    folder = Path(args.dir)
    shots = [(key, folder / f"{key}.png") for key in CAPTIONS]
    shots = [(key, path) for key, path in shots if path.is_file()]
    if not shots:
        print(f"{folder} 里没有展示图")
        return 2

    cell_w, cell_h, gap, cap_h = 900, 600, 30, 76
    cols = 2
    rows = (len(shots) + cols - 1) // cols
    width = cols * cell_w + gap * (cols + 1)
    height = rows * (cell_h + cap_h) + gap * (rows + 1)
    sheet = Image.new("RGB", (width, height), (12, 15, 19))
    draw = ImageDraw.Draw(sheet)
    title_font = _font(BOLD_CANDIDATES, 34)
    body_font = _font(FONT_CANDIDATES, 22)

    for index, (key, path) in enumerate(shots):
        col, row = index % cols, index // cols
        x = gap + col * (cell_w + gap)
        y = gap + row * (cell_h + cap_h + gap)
        with Image.open(path) as handle:
            image = handle.convert("RGB")
        scale = min(cell_w / image.width, cell_h / image.height)
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
        # 竖版图放进横格子里会留大片黑边：用自身模糊版做底，再把清晰版叠上去
        backdrop = Image.open(path).convert("RGB")
        cover = max(cell_w / backdrop.width, cell_h / backdrop.height)
        backdrop = backdrop.resize(
            (int(backdrop.width * cover), int(backdrop.height * cover)), Image.LANCZOS
        )
        left = (backdrop.width - cell_w) // 2
        top = (backdrop.height - cell_h) // 2
        frame = backdrop.crop((left, top, left + cell_w, top + cell_h))
        frame = frame.filter(ImageFilter.GaussianBlur(18))
        frame = Image.blend(frame, Image.new("RGB", (cell_w, cell_h), (6, 8, 11)), 0.55)
        frame.paste(image, ((cell_w - image.width) // 2, (cell_h - image.height) // 2))
        sheet.paste(frame, (x, y))
        draw.rectangle([x, y, x + cell_w - 1, y + cell_h - 1], outline=(40, 48, 58), width=1)

        head, sub = CAPTIONS[key]
        draw.text((x + 4, y + cell_h + 12), head, font=title_font, fill=(226, 233, 242))
        head_w = draw.textlength(head, font=title_font)
        draw.text((x + 20 + head_w, y + cell_h + 22), sub, font=body_font, fill=(126, 148, 168))

    # 空出来的最后一个格子写成说明块（数据都来自这次实际产出）
    if len(shots) % cols:
        col, row = len(shots) % cols, len(shots) // cols
        x = gap + col * (cell_w + gap)
        y = gap + row * (cell_h + cap_h + gap)
        lines = [
            ("出图方式", "工作台真机截图 + data/jobs 里的真实成品 → 生图接口"),
            ("模型", "gpt-image-2.6-sunburst（65535 网关 · 备用接口）"),
            ("成本", "5 张 × $0.038 ≈ $0.19，单张 95~125 秒"),
            ("规模", "10 款 × 49 色 · 13 份款式专属提示词 · 17 项离线自检"),
            ("原则", "版面像素不改：中文和数字全部取自真机截图"),
        ]
        draw.rectangle([x, y, x + cell_w - 1, y + cell_h + cap_h - 1], fill=(17, 21, 26))
        ly = y + 70
        for head, body in lines:
            draw.text((x + 40, ly), head, font=title_font, fill=(150, 199, 172))
            draw.text((x + 40, ly + 44), body, font=body_font, fill=(176, 190, 204))
            ly += 118

    out = Path(args.out) if args.out else folder / "00-总览.png"
    sheet.save(out)
    print(f"{out}  {sheet.width}x{sheet.height}  共 {len(shots)} 张")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
