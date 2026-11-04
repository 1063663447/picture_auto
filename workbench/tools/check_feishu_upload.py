"""测飞书附件上传：大图能不能传、要多久（这决定自动同步在免费实例上会不会超时）。"""
from __future__ import annotations

import io
import time

from PIL import Image

from skillbridge import feishu
from skillbridge.config import load_config, load_env


def make_png(size: tuple[int, int], noise: bool = True) -> bytes:
    from PIL import ImageDraw
    import random

    image = Image.new("RGB", size, (120, 140, 120))
    if noise:
        draw = ImageDraw.Draw(image)
        random.seed(7)
        for _ in range(4000):
            x = random.randrange(size[0])
            y = random.randrange(size[1])
            draw.point((x, y), (random.randrange(256), random.randrange(256), random.randrange(256)))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    env = load_env()
    cfg = load_config(None, env)
    fcfg = feishu.load_config(cfg, env)
    client = feishu.FeishuBitable(fcfg)
    print("ready:", fcfg.ready())

    for size in [(1024, 1536), (1536, 2048), (2048, 2048), (3072, 3072)]:
        data = make_png(size)
        path = feishu.Path(__file__).resolve().parent.parent / "data" / "tmp" / f"upload-{size[0]}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        started = time.time()
        try:
            token = client.upload_file(path)
            cost = time.time() - started
            print(f"  {size[0]}x{size[1]}  {len(data)/1024/1024:.2f} MB → token={bool(token)} 耗时 {cost:.2f}s")
        except Exception as exc:  # noqa: BLE001
            print(f"  {size[0]}x{size[1]}  {len(data)/1024/1024:.2f} MB → 失败 {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
