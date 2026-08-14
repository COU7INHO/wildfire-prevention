"""Build the landing page's hero image from satellite tiles of the municipality.

Not stock photography and not generated imagery: this is the same Esri World
Imagery the map itself displays, of the actual terrain the tool ranks. A product
whose whole argument is "measure before asserting" cannot open on a picture of
somewhere else.

    make heroi

Writes webapp/public/hero.jpg. Attribution is printed on the landing page.

FRAMING. The landing lays text over the LEFT of this image, under a nearly
opaque veil, and the right side stays clear. Whatever should be seen has to end
up on the right. CROP_WIDTH below 1.0 is what creates the room to aim: at 1.0
the strip spans the whole mosaic and CROP_X has nothing left to move.
"""

from __future__ import annotations

import math
import time
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageEnhance

from .boundary import municipality_polygon

WEB_DIR = Path(__file__).resolve().parent.parent / "webapp" / "public"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "out"
TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile"
ZOOM = 13          # ~30 tiles for a municipality: enough detail, few requests
OUT_WIDTH = 1600   # a hero wider than this buys nothing visible
ASPECT = 16 / 9

CROP_WIDTH = 0.62  # share of the mosaic width kept; below 1.0 to leave room to aim
CROP_X = 0.22      # 0 = west, 0.5 = centred, 1 = east
CROP_Y = 0.92      # 0 = north, 0.5 = centred, 1 = the southern edge


def _tile_xy(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2**z
    lat_r = math.radians(lat)
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def _fetch(z: int, x: int, y: int) -> Image.Image | None:
    try:
        r = requests.get(f"{TILES}/{z}/{y}/{x}", timeout=30)  # Esri orders it z/y/x
        if r.status_code == 200:
            return Image.open(BytesIO(r.content)).convert("RGB")
    except Exception:
        pass
    return None


def mosaic(name: str = "Baião", z: int = ZOOM) -> Image.Image:
    """Stitched satellite tiles covering the municipality, cached on disk."""
    _, (w, s, e, n) = municipality_polygon(name)
    cache = CACHE_DIR / f"hero_mosaic_{name.lower()}_z{z}.png"
    if cache.exists():
        return Image.open(cache).convert("RGB")

    x0, y1 = _tile_xy(w, s, z)
    x1, y0 = _tile_xy(e, n, z)
    xs, ys = range(int(x0), int(x1) + 1), range(int(y0), int(y1) + 1)
    print(f"{len(xs)}x{len(ys)} tiles at zoom {z}")

    out = Image.new("RGB", (len(xs) * 256, len(ys) * 256))
    missing = 0
    for i, xi in enumerate(xs):
        for j, yj in enumerate(ys):
            tile = _fetch(z, xi, yj)
            if tile is None:
                missing += 1
                continue
            out.paste(tile, (i * 256, j * 256))
            time.sleep(0.05)  # be polite to a public tile server
    if missing:
        print(f"warning: {missing} tiles failed and are left black")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out.save(cache)
    return out


def build(name: str = "Baião", crop_width: float = CROP_WIDTH,
          crop_x: float = CROP_X, crop_y: float = CROP_Y,
          out: Path | None = None) -> Path:
    full = mosaic(name)
    mw, mh = full.size

    cw = int(mw * crop_width)
    ch = int(cw / ASPECT)
    if ch > mh:
        ch, cw = mh, int(mh * ASPECT)
    left = int((mw - cw) * crop_x)
    top = int((mh - ch) * crop_y)
    frame = full.crop((left, top, left + cw, top + ch))

    image = frame.resize((OUT_WIDTH, int(OUT_WIDTH / ASPECT)), Image.LANCZOS)
    # the CSS veil does most of the darkening; take only the edge off here, so
    # that the two together do not bury the terrain altogether
    image = ImageEnhance.Brightness(image).enhance(0.95)
    image = ImageEnhance.Color(image).enhance(0.92)

    out = Path(out) if out else WEB_DIR / "hero.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, "JPEG", quality=78, optimize=True, progressive=True)
    print(f"{out}  {image.size[0]}x{image.size[1]}  {out.stat().st_size / 1024:.0f} KB")
    return out


if __name__ == "__main__":
    import sys

    build(sys.argv[1] if len(sys.argv) > 1 else "Baião")


# --------------------------------------------------------------- social card
CARD = (1200, 630)   # what Facebook, LinkedIn, WhatsApp and X all expect


def social_card(name: str = "Baião", out: Path | None = None) -> Path:
    """The image shown when the link is pasted into a chat or a timeline.

    Deliberately carries NO text: the title and description are supplied per
    language by the meta tags, and a worded image would need one file per
    language and would be truncated differently by every platform.

    JPEG, not WebP: several crawlers still refuse WebP and fall back to nothing.
    """
    from PIL import ImageFilter

    card = Image.new("RGB", CARD, (15, 23, 32))

    # the same terrain as the landing, blurred back so it reads as texture
    hero = mosaic(name)
    mw, mh = hero.size
    cw = int(mw * CROP_WIDTH)
    ch = int(cw / (CARD[0] / CARD[1]))
    left, top = int((mw - cw) * CROP_X), int((mh - ch) * CROP_Y)
    terrain = hero.crop((left, top, left + cw, top + ch)).resize(CARD, Image.LANCZOS)
    terrain = terrain.filter(ImageFilter.GaussianBlur(1.2))
    card.paste(terrain, (0, 0))

    veil = Image.new("RGBA", CARD, (0, 0, 0, 0))
    for x in range(CARD[0]):
        a = int(238 - 150 * (x / CARD[0]))          # dark left, clearer right
        ImageDraw.Draw(veil).line([(x, 0), (x, CARD[1])], fill=(9, 14, 20, a))
    card = Image.alpha_composite(card.convert("RGBA"), veil)

    # the product itself, the same mockup the landing leads with
    shot = Image.open(WEB_DIR / "screens-2200.webp").convert("RGBA")
    w = 980
    shot = shot.resize((w, round(shot.height * w / shot.width)), Image.LANCZOS)
    card.alpha_composite(shot, ((CARD[0] - w) // 2, (CARD[1] - shot.height) // 2 + 10))

    out = Path(out) if out else WEB_DIR / "og.jpg"
    card.convert("RGB").save(out, "JPEG", quality=86, optimize=True, progressive=True)
    print(f"{out}  {CARD[0]}x{CARD[1]}  {out.stat().st_size / 1024:.0f} KB")
    return out
