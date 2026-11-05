"""原图主体判定：这一单是「平铺/挂拍的空衣服」还是「真人上身」。

为什么必须判：提示词里那句 *keep the same person, face, hair, skin, hands* 对真人图是对的，
对**纯衣服**的平铺图却等于在邀请模型补一个模特上去 —— 实测同一批里就出现过
「深酒红那张忽然变成女生穿着卖」，其余几张还是平铺。所以：

* 提示词层面（主修复，``prompts.SUBJECT_CLAUSE``）：改成**中性**表述 ——
  「原图是平铺就保持平铺，原图是真人就保持同一个人，绝不新增原图没有的人」。
  这句对两种原图都成立，也就不再存在「邀请」。
* 判定层面（加强，本模块）：皮肤像素几乎为零 → 铁定没有真人（平铺/挂拍），
  这时再叠 ``NO_PERSON_CLAUSE`` + ``NEGATIVE_NO_PERSON``，并对成品做一次复查。

判定只用**「皮肤证据是否缺席」**这一个方向：缺席是可靠的（没有皮肤就一定没人）；
有皮肤则不可靠（米色/杏色/木地板都像皮肤），所以"有皮肤"直接按「真人/不确定」处理，
走中性提示词，不额外禁止 —— 宁可少禁，也不要把模特图的人擦掉。

纯 Pillow 实现（项目里没有 numpy）。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

GARMENT = "garment"
PERSON = "person"
AUTO = "auto"
KINDS = (AUTO, GARMENT, PERSON)
LABELS = {
    AUTO: "自动判断",
    GARMENT: "只有衣服（平铺/挂拍）",
    PERSON: "真人上身 / 不确定",
}

#: 判定前缩到这个边长：2.5 万个像素以内，纯 Python 也够快
WORK_EDGE = 160

#: 皮肤像素占比低于它就断言「图里没有真人」。
#: 真人图里脸/手/手臂加起来远超 0.4%，所以这个阈值几乎不会误伤。
SKIN_ABSENT = 0.004
#: 最大连通皮肤块同样要接近零，才认定「没有真人」
BLOB_ABSENT = 0.002

#: 成品复查：原图是空衣服，成品里却有大片「原背景区域变成皮肤色」→ 疑似又画了个人
ALERT_SKIN = 0.02

#: 皮肤色相（度）——深酒红/正红在 350~5°，不在区间内
HUE_RANGE = (4.0, 52.0)
SAT_RANGE = (0.14, 0.78)
VAL_RANGE = (0.20, 0.99)
#: 皮肤在 YCbCr 上的经典区间
CB_RANGE = (77, 132)
CR_RANGE = (130, 178)


def normalise(value: object) -> str:
    """把外部传入的 subject 归一到 garment / person / auto。"""
    text = str(value or "").strip().lower()
    if text in {GARMENT, PERSON}:
        return text
    if text in {"cloth", "flat", "flatlay", "empty", "no_person", "noperson", "衣服", "平铺"}:
        return GARMENT
    if text in {"model", "human", "worn", "on_model", "真人", "模特"}:
        return PERSON
    return AUTO


def describe(subject: str) -> str:
    return LABELS.get(normalise(subject), LABELS[AUTO])


def is_garment(subject: str) -> bool:
    return normalise(subject) == GARMENT


def _is_skin(r: int, g: int, b: int) -> bool:
    """HSV + YCbCr 双条件：暖色、不太艳、不太亮也不太暗。"""
    mx, mn = max(r, g, b), min(r, g, b)
    val = mx / 255.0
    if val < VAL_RANGE[0] or val > VAL_RANGE[1]:
        return False
    delta = mx - mn
    sat = 0.0 if mx == 0 else delta / mx
    if sat < SAT_RANGE[0] or sat > SAT_RANGE[1]:
        return False
    if delta == 0:
        hue = 0.0
    elif mx == r:
        hue = (60.0 * ((g - b) / delta)) % 360.0
    elif mx == g:
        hue = 60.0 * ((b - r) / delta) + 120.0
    else:
        hue = 60.0 * ((r - g) / delta) + 240.0
    if not (HUE_RANGE[0] <= hue <= HUE_RANGE[1]):
        return False
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return CB_RANGE[0] <= cb <= CB_RANGE[1] and CR_RANGE[0] <= cr <= CR_RANGE[1]


def _thumb(image: Image.Image, edge: int = WORK_EDGE) -> Image.Image:
    thumb = image.convert("RGB")
    width, height = thumb.size
    scale = min(1.0, edge / max(1, max(width, height)))
    if scale < 1.0:
        thumb = thumb.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))), Image.BILINEAR
        )
    return thumb


def _skin_grid(image: Image.Image) -> tuple[int, int, list[list[bool]]]:
    thumb = _thumb(image)
    width, height = thumb.size
    pixels = list(thumb.getdata())
    rows: list[list[bool]] = []
    for y in range(height):
        offset = y * width
        rows.append([_is_skin(*pixels[offset + x][:3]) for x in range(width)])
    return width, height, rows


def _largest_blob(width: int, height: int, rows: list[list[bool]]) -> float:
    """最大 4 邻域连通块占整图的比例。"""
    seen = [[False] * width for _ in range(height)]
    best = 0
    total = max(1, width * height)
    for y in range(height):
        for x in range(width):
            if not rows[y][x] or seen[y][x]:
                continue
            stack = [(x, y)]
            seen[y][x] = True
            size = 0
            while stack:
                cx, cy = stack.pop()
                size += 1
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if 0 <= nx < width and 0 <= ny < height and rows[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            if size > best:
                best = size
    return best / total


def _open(source: "str | Path | Image.Image") -> Image.Image | None:
    if isinstance(source, Image.Image):
        return source.convert("RGB")
    try:
        path = Path(source)
        if not path.is_file():
            return None
        with Image.open(path) as handle:
            return handle.convert("RGB")
    except Exception:  # noqa: BLE001 —— 判定失败不该让整单挂掉
        return None


def measure(source: "str | Path | Image.Image") -> dict:
    """量一张图的皮肤占比。返回 {skin, blob, size}；读不了就是 0。"""
    image = _open(source)
    if image is None:
        return {"skin": 0.0, "blob": 0.0, "size": (0, 0)}
    try:
        width, height, rows = _skin_grid(image)
        total = max(1, width * height)
        skin = sum(sum(1 for flag in row if flag) for row in rows) / total
        return {
            "skin": round(skin, 4),
            "blob": round(_largest_blob(width, height, rows), 4),
            "size": image.size,
        }
    except Exception:  # noqa: BLE001
        return {"skin": 0.0, "blob": 0.0, "size": image.size}


def detect(source: "str | Path | Image.Image", override: str = "") -> str:
    """返回 garment / person。

    只有「皮肤证据完全缺席」才判 garment；其余一律 person（走中性提示词，不多禁）。
    """
    forced = normalise(override)
    if forced in {GARMENT, PERSON}:
        return forced
    stats = measure(source)
    if stats["skin"] <= SKIN_ABSENT and stats["blob"] <= BLOB_ABSENT:
        return GARMENT
    return PERSON


def _border_color(image: Image.Image) -> tuple[int, int, int]:
    """取四条边的中位色当背景色（和 local_recolor 里的思路一致）。"""
    thumb = image.convert("RGB")
    width, height = thumb.size
    pixels = list(thumb.getdata())
    samples: list[tuple[int, int, int]] = []
    for x in range(width):
        samples.append(pixels[x][:3])
        samples.append(pixels[(height - 1) * width + x][:3])
    for y in range(height):
        samples.append(pixels[y * width][:3])
        samples.append(pixels[y * width + (width - 1)][:3])
    if not samples:
        return (255, 255, 255)
    channels = []
    for index in range(3):
        values = sorted(pixel[index] for pixel in samples)
        channels.append(values[len(values) // 2])
    return (channels[0], channels[1], channels[2])


def person_alert(source: "str | Path | Image.Image", output: "str | Path | bytes") -> dict:
    """成品复查：原图是空衣服，成品里**原背景区域**是否冒出大片皮肤色。

    只在「原来是背景、现在像皮肤」的位置统计，所以把衣服改成杏色/粉色不会误报
    （那些像素原本就在衣服上）。命中即 ``alert=True``，由 runner 记一条警告。
    """
    src = _open(source)
    if src is None:
        return {"alert": False, "skin": 0.0}
    # 原图本来就有真人 → 这条复查不适用（人物是原图的一部分）
    if detect(src) == PERSON:
        return {"alert": False, "skin": 0.0, "reason": "source-has-person"}
    if isinstance(output, (bytes, bytearray)):
        import io

        try:
            with Image.open(io.BytesIO(output)) as handle:
                out = handle.convert("RGB")
        except Exception:  # noqa: BLE001
            return {"alert": False, "skin": 0.0}
    else:
        out = _open(output)
        if out is None:
            return {"alert": False, "skin": 0.0}

    edge = WORK_EDGE
    src_small = _thumb(src, edge)
    out_small = _thumb(out, edge).resize(src_small.size, Image.BILINEAR)
    background = _border_color(src_small)
    src_pixels = list(src_small.getdata())
    out_pixels = list(out_small.getdata())
    total = max(1, len(src_pixels))
    hits = 0
    for index, pixel in enumerate(src_pixels):
        near_bg = all(abs(int(pixel[c]) - background[c]) <= 26 for c in range(3))
        if not near_bg:
            continue
        if _is_skin(*out_pixels[index][:3]):
            hits += 1
    ratio = hits / total
    return {"alert": ratio >= ALERT_SKIN, "skin": round(ratio, 4)}
