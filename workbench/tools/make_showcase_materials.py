"""把工作台真实截图 + 真实出图裁成「产品展示图」用的素材。

不生成任何新内容，只做裁剪 / 放大 / 拼图，所以素材里的界面文字全部是真机像素。
产物放在 --out 目录，供 tools/make_showcase.py 当参考图喂给生图接口。

用法：
    python tools/make_showcase_materials.py --raw E:\\picture_auto\\scratch\\showcase --out E:\\picture_auto\\scratch\\showcase\\mat
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "data" / "jobs"

# 真机截图里的卡片区域（截图 1876x926，坐标按实测像素写死）
CROPS = {
    # 左侧「款式与色彩」整块：10 款 / 49 色的款式手风琴 + 色块
    "panel-styles": ("raw-ui-01.png", (95, 118, 432, 848)),
    # 「生成设定」：接口 / 模型与单价
    "panel-settings": ("raw-ui-01.png", (95, 762, 432, 848)),
    # 提示词预览整块（右半屏，实际发送的完整提示词）
    "panel-prompt": ("raw-prompt-23.png", (1268, 22, 1876, 926)),
    # 提示词正文特写（左上 2/3）
    "prompt-zoom": ("raw-prompt-23.png", (1290, 96, 1870, 560)),
    # 历史任务：真实批次记录
    "panel-history": ("raw-history.png", (460, 430, 1865, 758)),
    # 运行日志 + 历史任务上半
    "panel-log": ("raw-history.png", (460, 90, 1865, 430)),
}

# 真实成品（data/jobs 里已经出过的图），挑不同款式 × 不同颜色
RESULTS = [
    ("06-hoodie", JOBS / "20261110-101836-69ab/outputs/06-hoodie-style-recolor-batch/06LMWY/06LMWY_BK[0]_2026111069ab.png"),
    ("06-hoodie", JOBS / "20261110-101836-69ab/outputs/06-hoodie-style-recolor-batch/06LMWY/06LMWY_FGY[0]_2026111069ab.png"),
    ("06-hoodie", JOBS / "20261110-101836-69ab/outputs/06-hoodie-style-recolor-batch/06LMWY/06LMWY_WH[0]_2026111069ab.png"),
    ("25-kids-ls", JOBS / "20261110-094504-981a/outputs/25-kids-long-sleeve-style-recolor-batch/25CXTZ/25CXTZ_RD[0]_20261110981a.png"),
    ("25-kids-ls", JOBS / "20261110-094504-981a/outputs/25-kids-long-sleeve-style-recolor-batch/25CXTZ/25CXTZ_GN[0]_20261110981a.png"),
    ("25-kids-ls", JOBS / "20261110-094504-981a/outputs/25-kids-long-sleeve-style-recolor-batch/25CXTZ/25CXTZ_BU[0]_20261110981a.png"),
    ("25-kids-ls", JOBS / "20261110-094504-981a/outputs/25-kids-long-sleeve-style-recolor-batch/25CXTZ/25CXTZ_WH[0]_20261110981a.png"),
    ("25-kids-ls", JOBS / "20261110-094504-981a/outputs/25-kids-long-sleeve-style-recolor-batch/25CXTZ/25CXTZ_BK[0]_20261110981a.png"),
    ("21-kids-tee", JOBS / "20261110-102836-72a6/outputs/21-kids-sorona-tee-style-recolor-batch/21TZSLN/21TZSLN_WH[0]_2026111072a6.png"),
    ("21-kids-tee", JOBS / "20261110-102836-72a6/outputs/21-kids-sorona-tee-style-recolor-batch/21TZSLN/21TZSLN_BK[0]_2026111072a6.png"),
    ("23-sorona-tee", JOBS / "20261110-104234-1463/outputs/23-sorona-tee-style-recolor-batch/23SLNDX/23SLNDX_RD[0]_202611101463.png"),
    ("23-sorona-tee", JOBS / "20261110-104234-1463/outputs/23-sorona-tee-style-recolor-batch/23SLNDX/23SLNDX_FGY[0]_202611101463.png"),
]


def _crop(src: Path, box: tuple[int, int, int, int], scale: float) -> Image.Image:
    with Image.open(src) as handle:
        image = handle.convert("RGB")
    image = image.crop(box)
    if scale != 1.0:
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)), Image.LANCZOS
        )
    return image


def build_crops(raw: Path, out: Path) -> list[Path]:
    made: list[Path] = []
    for name, (source, box) in CROPS.items():
        src = raw / source
        if not src.is_file():
            print(f"  skip {name}: {src.name} 不存在")
            continue
        # 面板类放大 1.6x，让文字在展示图里能看清
        image = _crop(src, box, 1.6)
        target = out / f"{name}.png"
        image.save(target)
        made.append(target)
        print(f"  {target.name}  {image.width}x{image.height}")
    return made


def build_result_grid(out: Path, *, cell: int = 420, gap: int = 18, cols: int = 4) -> Path | None:
    shots = [(tag, path) for tag, path in RESULTS if path.is_file()]
    if not shots:
        print("  skip grid: 没有可用成品")
        return None
    rows = (len(shots) + cols - 1) // cols
    width = cols * cell + gap * (cols + 1)
    height = rows * cell + gap * (rows + 1)
    sheet = Image.new("RGB", (width, height), (14, 18, 22))
    for index, (_tag, path) in enumerate(shots):
        x = gap + (index % cols) * (cell + gap)
        y = gap + (index // cols) * (cell + gap)
        with Image.open(path) as handle:
            image = handle.convert("RGB")
        scale = min((cell - 8) / image.width, (cell - 8) / image.height)
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.LANCZOS
        )
        sheet.paste(image, (x + (cell - image.width) // 2, y + (cell - image.height) // 2))
    target = out / "grid-results.png"
    sheet.save(target)
    print(f"  {target.name}  {sheet.width}x{sheet.height}  ({len(shots)} 张真实成品)")
    return target


def _fit(path: Path, box: tuple[int, int], *, pad: int = 0) -> Image.Image:
    """等比缩放到 box 之内，四周留 pad 像素白边（这里留黑边）。"""
    with Image.open(path) as handle:
        image = handle.convert("RGB")
    avail_w, avail_h = box[0] - pad * 2, box[1] - pad * 2
    scale = min(avail_w / image.width, avail_h / image.height)
    return image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.LANCZOS)


def build_boards(mat: Path, out: Path, raw: Path) -> dict[str, Path]:
    """把素材预排成「展示图版面」，生图接口只负责打光/材质/氛围，不改版面。

    这么做的好处：界面文字位置、批次记录、成品网格的相对关系由真机像素决定，
    模型只做摄影化处理，不会把中文重排成乱码。
    """
    boards: dict[str, Path] = {}

    # 02 款式 × 色彩：左「款式与色彩」面板，右真实成品网格
    canvas = Image.new("RGB", (2048, 1400), (11, 14, 18))
    left = _fit(mat / "panel-styles.png", (620, 1240))
    canvas.paste(left, (80, 90))
    right = _fit(mat / "grid-results.png", (1320, 1160))
    canvas.paste(right, (720, 130))
    target = out / "board-02-styles.png"
    canvas.save(target)
    boards["02"] = target

    # 03 提示词特写：只切英文正文（中文标题留在原截图里，模型重绘中文会糊成乱码），
    # 四周留足黑边，避免模型再次放大时把文字切掉。
    prompt_src = raw / "raw-prompt-23.png"
    if prompt_src.is_file():
        with Image.open(prompt_src) as handle:
            image = handle.convert("RGB")
        body = image.crop((1130, 178, 1876, 794))
        body = body.resize((int(body.width * 1.55), int(body.height * 1.55)), Image.LANCZOS)
        canvas = Image.new("RGB", (1300, 1180), (9, 11, 14))
        canvas.paste(body, ((1300 - body.width) // 2, 112))
        target = out / "board-03-prompt.png"
        canvas.save(target)
        boards["03"] = target

    # 04 批量与对账：真实成品网格 + 历史批次记录
    canvas = Image.new("RGB", (1520, 1560), (11, 14, 18))
    grid = _fit(mat / "grid-results.png", (1400, 1040))
    canvas.paste(grid, ((1520 - grid.width) // 2, 60))
    history = _fit(mat / "panel-history.png", (1400, 340))
    canvas.paste(history, ((1520 - history.width) // 2, 1140))
    target = out / "board-04-batch.png"
    canvas.save(target)
    boards["04"] = target

    # 05 原图 → 10 色成品（真实 job 的原图 + 出图）
    source = JOBS / "20261110-104234-1463/inputs/item-01/view-01.png"
    outs = [
        JOBS / "20261110-104234-1463/outputs/23-sorona-tee-style-recolor-batch/23SLNDX/23SLNDX_WH[0]_202611101463.png",
        JOBS / "20261110-104234-1463/outputs/23-sorona-tee-style-recolor-batch/23SLNDX/23SLNDX_RD[0]_202611101463.png",
        JOBS / "20261110-104234-1463/outputs/23-sorona-tee-style-recolor-batch/23SLNDX/23SLNDX_BK[0]_202611101463.png",
        JOBS / "20261110-104234-1463/outputs/23-sorona-tee-style-recolor-batch/23SLNDX/23SLNDX_BU[0]_202611101463.png",
    ]
    if source.is_file() and all(p.is_file() for p in outs):
        canvas = Image.new("RGB", (2048, 1220), (13, 15, 18))
        big = _fit(source, (760, 1000))
        canvas.paste(big, (70, 110))
        cell_w, cell_h = 430, 480
        for index, path in enumerate(outs):
            x = 900 + (index % 2) * (cell_w + 40)
            y = 110 + (index // 2) * (cell_h + 60)
            cell = _fit(path, (cell_w, cell_h))
            canvas.paste(cell, (x + (cell_w - cell.width) // 2, y + (cell_h - cell.height) // 2))
        target = out / "board-05-recolor.png"
        canvas.save(target)
        boards["05"] = target
    else:
        print("  skip board-05: 原图或成品缺失")
    for key, path in boards.items():
        with Image.open(path) as handle:
            print(f"  {path.name}  {handle.width}x{handle.height}")
    return boards


def build_contact_sheet(out: Path, pieces: list[Path], *, width: int = 1400) -> Path | None:
    """素材总览，方便人眼核对裁剪是否准。"""
    if not pieces:
        return None
    scaled: list[Image.Image] = []
    for path in pieces:
        with Image.open(path) as handle:
            image = handle.convert("RGB")
        ratio = min(1.0, width / image.width)
        if ratio < 1.0:
            image = image.resize((int(image.width * ratio), int(image.height * ratio)), Image.LANCZOS)
        scaled.append(image)
    total = sum(image.height + 12 for image in scaled)
    sheet = Image.new("RGB", (width, total + 12), (10, 12, 14))
    y = 12
    for image in scaled:
        sheet.paste(image, (0, y))
        y += image.height + 12
    target = out / "materials-overview.png"
    sheet.save(target)
    print(f"  {target.name}  {sheet.width}x{sheet.height}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default=r"E:\picture_auto\scratch\showcase", help="放真机截图的目录")
    parser.add_argument("--out", default=r"E:\picture_auto\scratch\showcase\mat", help="素材输出目录")
    args = parser.parse_args()

    raw = Path(args.raw)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"素材输出：{out}")
    pieces = build_crops(raw, out)
    grid = build_result_grid(out)
    if grid:
        pieces.append(grid)
    build_boards(out, out, raw)
    # 01 主视觉直接用整屏真机截图（含微光），不需要再排版
    screen = raw / "raw-ui-glow.png"
    if screen.is_file():
        target = out / "board-01-screen.png"
        with Image.open(screen) as handle:
            handle.convert("RGB").save(target)
        print(f"  {target.name}  (整屏真机截图)")
    build_contact_sheet(out, [grid] + [p for p in pieces if p != grid] if grid else pieces)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
