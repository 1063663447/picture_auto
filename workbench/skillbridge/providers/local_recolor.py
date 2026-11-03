"""Local fabric recolor provider — zero-loss artwork, no API call, no cost.

Why this exists: when the garment *type* is already correct and only the colour has
to change, sending the photo to a generative model is both wasteful and risky — the
model re-draws the printed artwork and it drifts (different typeface, letter spacing,
scale, or a flattened 3D appliqué). This adapter never regenerates anything:

1. estimate the garment tone and the background tone,
2. segment the fabric by hue window + saturation floor (saturated garments) or by
   RGB distance (near-neutral garments),
3. exclude the background and *protect the artwork*: printed/embroidered pixels have
   a much lower saturation or an extreme value than the fabric, so they — plus a few
   pixels of halo around them — are copied through untouched,
4. recolour only the fabric by multiplying the target colour with the fabric's own
   luminance ratio, so folds, seams, ribbing and texture survive,
5. composite back over the untouched pixels and save at the original resolution.

Use it for `<garment type> + colour change`; use a real image model when the
silhouette itself must change.
"""

from __future__ import annotations

import io
import re

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from .base import EditRequest, Provider, ProviderError

HEX_RE = re.compile(r"#([0-9a-fA-F]{6})")
#: cheap guards: if the segmentation looks wrong we refuse instead of ruining a photo
MIN_COVERAGE = 0.03
MAX_COVERAGE = 0.94


def _median(values: list[int]) -> int:
    values = sorted(values)
    return values[len(values) // 2] if values else 128


def _border_color(image: Image.Image) -> tuple[int, int, int]:
    small = image.copy()
    small.thumbnail((96, 96))
    width, height = small.size
    pixels = small.load()
    samples: list[tuple[int, int, int]] = []
    for y in range(height):
        for x in range(width):
            if x < 2 or y < 2 or x >= width - 2 or y >= height - 2:
                samples.append(pixels[x, y])
    return (
        _median([p[0] for p in samples]),
        _median([p[1] for p in samples]),
        _median([p[2] for p in samples]),
    )


def _distance_max(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


def _odd(value: int) -> int:
    value = max(3, int(value))
    return value if value % 2 == 1 else value + 1


class LocalRecolorProvider(Provider):
    kind = "local_recolor"
    label = "本地换色（免模型 · 图案零失真）"
    notes = (
        "不调用任何接口：按颜色分割出面料后只替换底色，图案/文字/刺绣像素原样保留，"
        "版型和褶皱也不变。只适用于“款式已经对、只换颜色”；要改款请用生图接口。"
    )

    def __init__(self, name: str, cfg: dict, *, env: dict | None = None) -> None:
        super().__init__(name, cfg, env=env)
        self.max_images = 1
        self.supports_negative = False
        self.label = self.cfg.get("label") or self.label

    def ready(self) -> tuple[bool, str]:
        return True, "本地处理"

    async def list_models(self, timeout: float = 30.0) -> list[dict]:
        return [{"id": "local-recolor", "owned_by": "local"}]

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        match = HEX_RE.search(request.prompt or "")
        if not match:
            raise ProviderError(
                "本地换色只能用「仅换色」模式（提示词里没有目标色值，说明这次需要改款）",
                provider=self.name,
            )
        if not request.images:
            raise ProviderError("没有可用的输入图片", provider=self.name)
        hexv = match.group(1)
        target_rgb = (int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16))
        return self.recolor(request.images[0], target_rgb)

    # ------------------------------------------------------------- segmentation --
    def _fabric_mask(
        self, image: Image.Image, garment: tuple[int, int, int]
    ) -> dict:
        """Fabric mask plus the numbers needed to protect the artwork."""
        hsv = image.convert("HSV")
        hue, sat, val = hsv.split()
        h_garment, s_garment, v_garment = Image.new("RGB", (1, 1), garment).convert("HSV").getpixel(
            (0, 0)
        )

        if s_garment >= 90:
            hue_tolerance = int(self.cfg.get("hue_tolerance", 20))
            flat_hue = Image.new("L", image.size, h_garment)
            raw = ImageChops.difference(hue, flat_hue)
            wrapped = ImageChops.darker(raw, ImageChops.invert(raw))
            hue_mask = wrapped.point(lambda v: 255 if v <= hue_tolerance else 0)
            sat_floor = int(self.cfg.get("sat_ratio", 0.62) * s_garment)
            sat_floor = max(70, min(sat_floor, s_garment - 30))
            sat_mask = sat.point(lambda v: 255 if v >= sat_floor else 0)
            val_mask = val.point(lambda v: 255 if v >= 16 else 0)
            mask = ImageChops.multiply(ImageChops.multiply(hue_mask, sat_mask), val_mask)
            return {
                "mask": mask,
                "strategy": "hue",
                "sat": sat,
                "val": val,
                "sat_floor": sat_floor,
                "v_garment": v_garment,
            }

        tolerance = int(self.cfg.get("color_tolerance", 52))
        flat_garment = Image.new("RGB", image.size, garment)
        diff = ImageChops.difference(image, flat_garment)
        channels = diff.split()
        distance = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        mask = distance.point(lambda v: 255 if v <= tolerance else 0)
        return {
            "mask": mask,
            "strategy": "distance",
            "sat": sat,
            "val": val,
            "sat_floor": 0,
            "v_garment": v_garment,
        }

    def _artwork_protection(self, info: dict, width: int, height: int) -> tuple[Image.Image, Image.Image] | None:
        """Split the artwork area into `core` (never recolour) and `halo` (recolour weakly).

        Artwork pixels are much less saturated, or much darker/brighter, than the fabric.
        The halo band around them is a *blend* of artwork and fabric, so it is recoloured
        with reduced alpha: that removes the old colour without destroying the letter
        edges (a hard exclusion leaves a visible old-colour ring instead).
        """
        if info["strategy"] != "hue":
            return None
        mask: Image.Image = info["mask"]
        sat: Image.Image = info["sat"]
        val: Image.Image = info["val"]
        sat_floor = info["sat_floor"]
        v_garment = info["v_garment"]

        growth = _odd(max(5, width // 80))
        shrink = _odd(growth + 2 * max(3, width // 140))
        interior = mask.filter(ImageFilter.MaxFilter(growth)).filter(
            ImageFilter.MinFilter(shrink)
        )

        core = ImageChops.lighter(
            ImageChops.lighter(
                sat.point(lambda v: 255 if v < sat_floor * 0.55 else 0),
                val.point(lambda v: 255 if v < max(28, int(v_garment * 0.34)) else 0),
            ),
            val.point(lambda v: 255 if v > 242 else 0),
        )
        core = ImageChops.multiply(core, interior)
        core = core.filter(ImageFilter.MaxFilter(_odd(max(3, width // 320))))

        halo = ImageChops.lighter(
            sat.point(lambda v: 255 if v < sat_floor else 0),
            val.point(lambda v: 255 if v < max(40, int(v_garment * 0.55)) else 0),
        )
        halo = ImageChops.multiply(halo, interior)
        halo = ImageChops.subtract(halo, core)
        halo = halo.filter(ImageFilter.MaxFilter(_odd(max(3, width // 200))))
        return core, halo

    # -------------------------------------------------------------------- core --
    def recolor(self, source, target_rgb: tuple[int, int, int]) -> bytes:
        with Image.open(source) as handle:
            image = ImageOps.exif_transpose(handle).convert("RGB")
        width, height = image.size
        if width < 64 or height < 64:
            raise ProviderError("图片太小，无法可靠分割面料", provider=self.name)

        background = _border_color(image)
        garment = self._estimate_garment(image, background)
        info = self._fabric_mask(image, garment)
        mask: Image.Image = info["mask"]

        # never recolour obvious background (a beige wall can share the fabric's hue)
        flat_bg = Image.new("RGB", image.size, background)
        bg_diff = ImageChops.difference(image, flat_bg)
        bg_channels = bg_diff.split()
        bg_distance = ImageChops.lighter(
            ImageChops.lighter(bg_channels[0], bg_channels[1]), bg_channels[2]
        )
        bg_mask = bg_distance.point(lambda v: 255 if v <= 10 else 0)
        mask = ImageChops.subtract(mask, bg_mask)

        coverage = ImageStat.Stat(mask).mean[0] / 255.0
        if coverage < MIN_COVERAGE:
            raise ProviderError(
                f"只识别到 {coverage * 100:.1f}% 面料区域，这张图不适合本地换色（请改用生图接口）",
                provider=self.name,
            )
        if coverage > MAX_COVERAGE:
            raise ProviderError(
                f"面料区域占到 {coverage * 100:.1f}%，分割不可靠（请改用生图接口）",
                provider=self.name,
            )

        # cover the anti-aliased silhouette edge so no old-colour fringe survives
        grown = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(3))
        mask = ImageChops.lighter(mask, grown)

        protection = self._artwork_protection(info, width, height)
        alpha = mask
        if protection is not None:
            core, halo = protection
            bg_inside = bg_mask.filter(ImageFilter.MinFilter(9))
            core = ImageChops.subtract(core, bg_inside)
            halo = ImageChops.subtract(halo, bg_inside)
            # halo = the blend band between artwork and fabric: recolour it strongly
            # enough to remove the old colour, softly enough to keep the letter edges
            halo_alpha = int(self.cfg.get("halo_alpha", 190))
            alpha = ImageChops.lighter(alpha, halo.point(lambda v: halo_alpha if v else 0))
            alpha = ImageChops.subtract(alpha, core)

        # luminance ratio of the fabric, so shading/folds/texture survive the recolour
        stats = ImageStat.Stat(image, mask=mask)
        garment_mean = stats.mean
        luminance = (
            0.299 * garment_mean[0] + 0.587 * garment_mean[1] + 0.114 * garment_mean[2]
        ) or 1.0
        grey = image.convert("L")
        scale = 255.0 / max(1.0, luminance)
        ceiling = float(self.cfg.get("highlight_ceiling", 1.30)) * 255.0
        floor = float(self.cfg.get("shadow_floor", 0.32)) * 255.0

        def _ratio(value: int) -> int:
            ratio = min(max(value * scale, floor), ceiling) / 255.0
            return int(min(255, max(0, ratio * 255)))

        ratio_channel = grey.point(_ratio)
        tint = Image.new("RGB", image.size, target_rgb)
        shaded = ImageChops.multiply(tint, Image.merge("RGB", (ratio_channel,) * 3))

        # very dark targets lose all shading in a pure multiply: keep a little base
        if max(target_rgb) < 90:
            base = Image.new("RGB", image.size, target_rgb)
            shaded = Image.blend(shaded, base, 0.35)

        alpha = alpha.filter(ImageFilter.GaussianBlur(max(0.8, min(width, height) / 700)))
        result = Image.composite(shaded, image, alpha)

        buffer = io.BytesIO()
        result.save(buffer, format="PNG", optimize=True, compress_level=9)
        return buffer.getvalue()

    def _estimate_garment(
        self, image: Image.Image, background: tuple[int, int, int]
    ) -> tuple[int, int, int]:
        """Median-ish tone of the biggest non-background cluster in the middle of the frame."""
        small = image.copy()
        small.thumbnail((128, 128))
        width, height = small.size
        pixels = small.load()
        buckets: dict[tuple[int, int, int], list[int]] = {}
        for y in range(int(height * 0.12), int(height * 0.95)):
            for x in range(int(width * 0.06), int(width * 0.94)):
                pixel = pixels[x, y]
                if _distance_max(pixel, background) <= 26:
                    continue
                key = (pixel[0] // 24, pixel[1] // 24, pixel[2] // 24)
                entry = buckets.setdefault(key, [0, 0, 0, 0])
                entry[0] += pixel[0]
                entry[1] += pixel[1]
                entry[2] += pixel[2]
                entry[3] += 1
        if not buckets:
            return background
        sums = max(buckets.values(), key=lambda item: item[3])
        return (sums[0] // sums[3], sums[1] // sums[3], sums[2] // sums[3])
