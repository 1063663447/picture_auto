"""从效果图里裁一块「面料色卡」，给派生色当颜色参考（不带任何衣服形状）。

为什么要这么做：派生色（跟随基准只改颜色）如果还发整张效果图，模型偶尔会把效果图的**版型**
也一起搬过来（2026-11-10 飞书 1526~1542：21 童装索罗娜·黑色 变成了带帽卫衣）。
只发一块纯面料色卡，模型就只能取颜色，形状没得抄。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

SWATCH_DIR = "swatches"
SWATCH_SIZE = 256
#: 效果图是「白底平铺产品图」，衣服主体大概占据中间这一块
CROP_BOX = (0.32, 0.35, 0.68, 0.62)


def swatch_path(effect: Path, base_dir: Path) -> Path | None:
    """按效果图生成（并缓存）面料色卡；失败返回 None（调用方退回原效果图）。"""
    if effect is None or not Path(effect).is_file():
        return None
    try:
        digest = hashlib.sha256(Path(effect).read_bytes()).hexdigest()[:16]
        target_dir = Path(base_dir) / SWATCH_DIR
        target = target_dir / f"{Path(effect).stem}-{digest}.png"
        if target.is_file():
            return target
        target_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(effect) as handle:
            image = handle.convert("RGB")
        width, height = image.size
        box = (
            int(CROP_BOX[0] * width),
            int(CROP_BOX[1] * height),
            int(CROP_BOX[2] * width),
            int(CROP_BOX[3] * height),
        )
        crop = image.crop(box)
        # 取中间 60% 再放大，避开袖口/领口的阴影和高光边缘
        w, h = crop.size
        inner = crop.crop((int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8)))
        swatch = inner.resize((SWATCH_SIZE, SWATCH_SIZE), Image.LANCZOS)
        tmp = target.with_suffix(".tmp.png")
        swatch.save(tmp, format="PNG")
        tmp.replace(target)
        return target
    except Exception:  # noqa: BLE001 - 色卡是加分项，失败就让调用方退回效果图
        return None
