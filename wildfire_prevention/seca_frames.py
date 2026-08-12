"""Render one image per month of vegetation dryness, ready to be animated.

These are presentation images, not the application: the cell grid is smoothed
into a continuous surface and laid over satellite imagery of the municipality.
The application draws hard 29 m cells because each one is a decision; here the
point is to show a season changing.

    make secura-imagens              -> ~/Desktop
    make secura-imagens OUT=/tmp     -> anywhere else

The colour scale is SHARED by every month and printed on each image. Rescaling
per month would make each frame look equally dry and the animation would show
nothing at all.
"""

from __future__ import annotations

import json
import math
import time
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import gaussian_filter

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"
TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile"
ZOOM = 14          # ~9.5 m per pixel here: finer than the 29 m cells
# Match the mosaic's own width at this zoom (~2788 px here). Exporting smaller
# throws satellite detail away in the downscale for nothing.
LARGURA = 2800
OPACIDADE = 0.88
SUAVIZAR = 0.8     # gaussian sigma in cells: keep it under one cell (29 m),
                   # otherwise smoothing erases the detail it is meant to soften

# moist to dry. Deliberately not the application's green-to-red, which means
# priority: a viewer who saw both should not read them as the same quantity.
CORES = LinearSegmentedColormap.from_list(
    "secura",
    ["#1b6b8f", "#4fa3a5", "#c9d17a", "#e8a33d", "#c4451c", "#7d1d0f"],
)

MESES = {
    "01": "Janeiro", "02": "Fevereiro", "03": "Março", "04": "Abril",
    "05": "Maio", "06": "Junho", "07": "Julho", "08": "Agosto",
    "09": "Setembro", "10": "Outubro", "11": "Novembro", "12": "Dezembro",
}


# ----------------------------------------------------------------- basemap
def _merc(lon, lat, z):
    n = 2**z
    lat_r = np.radians(lat)
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / np.pi) / 2.0 * n
    return x, y


def _basemap(bbox, z=ZOOM):
    """Satellite mosaic cropped exactly to bbox, plus its pixel geometry."""
    w, s, e, n = bbox
    x0, y1 = _merc(w, s, z)
    x1, y0 = _merc(e, n, z)
    tx, ty = range(int(x0), int(x1) + 1), range(int(y0), int(y1) + 1)
    geom = (x0, y0, x1, y1)

    # 130-odd tile requests per run adds up fast when iterating on the look
    cache = OUT_DIR / f"mosaico_z{z}_{w:.4f}_{s:.4f}_{e:.4f}_{n:.4f}.png"
    if cache.exists():
        return Image.open(cache).convert("RGB"), geom
    print(f"mosaico: {len(tx)}x{len(ty)} tiles no zoom {z}")

    mosaico = Image.new("RGB", (len(tx) * 256, len(ty) * 256))
    for i, xi in enumerate(tx):
        for j, yj in enumerate(ty):
            try:
                r = requests.get(f"{TILES}/{z}/{yj}/{xi}", timeout=30)
                if r.status_code == 200:
                    mosaico.paste(Image.open(BytesIO(r.content)).convert("RGB"), (i * 256, j * 256))
            except Exception:
                pass
            time.sleep(0.04)

    # crop to the exact bbox, in mosaic pixel coordinates
    px0, py0 = (x0 - int(x0)) * 256, (y0 - int(y0)) * 256
    px1, py1 = px0 + (x1 - x0) * 256, py0 + (y1 - y0) * 256
    recorte = mosaico.crop((int(px0), int(py0), int(px1), int(py1)))
    recorte.save(cache)
    return recorte, geom


# -------------------------------------------------------------------- data
def _raster(valores, lon, lat):
    """The cells sit on a regular lon/lat grid, so this is pure indexing."""
    ulon, ulat = np.unique(lon), np.unique(lat)
    grelha = np.full((ulat.size, ulon.size), np.nan, dtype=np.float32)
    ix = np.searchsorted(ulon, lon)
    iy = np.searchsorted(ulat, lat)
    grelha[iy, ix] = valores
    return grelha[::-1], ulon, ulat[::-1]      # north at the top


def _resample(grelha, ulon, ulat, geom, size, z=ZOOM):
    """Plate carree cells onto the Web Mercator pixels of the basemap."""
    x0, y0, x1, y1 = geom
    larg, alt = size
    n = 2**z

    px = x0 + (np.arange(larg) + 0.5) / larg * (x1 - x0)
    lons = px / n * 360.0 - 180.0
    py = y0 + (np.arange(alt) + 0.5) / alt * (y1 - y0)
    lats = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * py / n))))

    ci = np.clip(np.searchsorted(ulon, lons) - 1, 0, ulon.size - 1)
    ri = np.clip(np.searchsorted(-ulat, -lats) - 1, 0, ulat.size - 1)
    return grelha[np.ix_(ri, ci)]


# ------------------------------------------------------------------ layout
def _fonte(tamanho, negrito=False):
    for caminho in ("/System/Library/Fonts/HelveticaNeue.ttc",
                    "/System/Library/Fonts/Helvetica.ttc",
                    "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if Path(caminho).exists():
            try:
                return ImageFont.truetype(caminho, tamanho, index=1 if negrito else 0)
            except Exception:
                continue
    return ImageFont.load_default()


def _moldura(img, titulo, subtitulo):
    """Title, shared colour bar and attribution, over a soft dark gradient."""
    larg, alt = img.size
    d = ImageDraw.Draw(img, "RGBA")

    faixa = int(alt * 0.30)
    for i in range(faixa):                       # top: darken for the title
        a = int(150 * (1 - i / faixa) ** 1.5)
        d.line([(0, i), (larg, i)], fill=(9, 14, 20, a))
    for i in range(faixa):                       # bottom: darken for the scale
        a = int(190 * (1 - i / faixa) ** 1.3)
        d.line([(0, alt - 1 - i), (larg, alt - 1 - i)], fill=(9, 14, 20, a))

    # every measurement below is relative, so the frame holds together at any
    # export width instead of shrinking into the corner
    k = larg / 1600.0
    def px(v):
        return int(round(v * k))

    d.text((px(54), px(44)), subtitulo.upper(), font=_fonte(px(21)), fill=(190, 205, 218, 255))
    d.text((px(54), px(76)), titulo, font=_fonte(px(64), negrito=True), fill=(255, 255, 255, 255))

    bx, by, bw, bh = px(54), alt - px(96), px(420), px(14)
    for i in range(bw):                          # the colour scale itself
        r, g, b, _ = CORES(i / (bw - 1))
        d.line([(bx + i, by), (bx + i, by + bh)], fill=(int(r * 255), int(g * 255), int(b * 255), 255))
    d.rectangle([bx, by, bx + bw, by + bh], outline=(255, 255, 255, 90))
    f = _fonte(px(19))
    d.text((bx, by + bh + px(11)), "Vegetação húmida", font=f, fill=(210, 222, 232, 255))
    seco = "Vegetação seca"
    d.text((bx + bw - d.textlength(seco, font=f), by + bh + px(11)), seco,
           font=f, fill=(210, 222, 232, 255))
    d.text((bx, by - px(30)), "Escala igual nos três meses", font=_fonte(px(17)),
           fill=(160, 176, 192, 255))

    credito = "Sentinel-2 (Copernicus) · imagem de satélite © Esri"
    fc = _fonte(px(17))
    d.text((larg - px(54) - d.textlength(credito, font=fc), alt - px(44)),
           credito, font=fc, fill=(150, 166, 182, 255))
    return img


# -------------------------------------------------------------------- main
def camadas(name: str = "Baião", largura: int = LARGURA) -> list[dict]:
    """One finished map image per month, WITHOUT the frame.

    The caption is left off on purpose so an animation can cross-fade the maps
    while switching the month label cleanly. Dissolving two different titles
    into each other produces unreadable ghosted text.
    """
    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    lon, lat = f["lon"], f["lat"]
    seca = np.load(OUT_DIR / f"seca_history_{name.lower()}.npz")
    meta = json.loads((OUT_DIR / f"seca_history_{name.lower()}.json").read_text())
    meses = meta["months"]

    # NDMI lives in [-1, 1]; cloud and division artefacts push a handful of
    # cells far outside it, and one of those would flatten the whole scale.
    limpos = {}
    for m in meses:
        v = np.asarray(seca[m["key"]], dtype=np.float32)
        v[~np.isfinite(v)] = np.nan
        limpos[m["key"]] = np.clip(v, -1.0, 1.0)

    todos = np.concatenate([v[np.isfinite(v)] for v in limpos.values()])
    lo, hi = np.percentile(todos, [2, 98])
    print(f"escala partilhada: NDMI {lo:.3f} a {hi:.3f}")

    bbox = (float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max()))
    base, geom = _basemap(bbox)
    escala = largura / base.size[0]
    base = base.resize((largura, int(base.size[1] * escala)), Image.LANCZOS)

    saidas = []
    for m in meses:
        grelha, ulon, ulat = _raster(limpos[m["key"]], lon, lat)

        dentro = np.isfinite(grelha)
        cheia = np.where(dentro, grelha, np.nanmedian(grelha))
        # smooth values and mask together, so the edge fades instead of fraying
        suave = gaussian_filter(cheia, SUAVIZAR)
        peso = gaussian_filter(dentro.astype(np.float32), SUAVIZAR)

        campo = _resample(suave, ulon, ulat, geom, base.size)
        alfa = _resample(peso, ulon, ulat, geom, base.size)

        # dryness is the inverse of moisture: high NDMI is a wet plant
        secura = np.clip((hi - campo) / (hi - lo), 0.0, 1.0)
        cor = CORES(secura)[..., :3]

        # Modulate the colour by the satellite's own brightness. Flat alpha
        # blending buries the terrain under a wash of colour; multiplying by the
        # shading keeps ridges, valleys and tracks visible through it, and that
        # is where the eye reads detail.
        lum = np.asarray(base.convert("L"), dtype=np.float32)[..., None] / 255.0
        cor = np.clip(cor * (0.68 + 0.66 * lum), 0.0, 1.0)

        rgba = np.empty(cor.shape[:2] + (4,), dtype=np.uint8)
        rgba[..., :3] = (cor * 255).astype(np.uint8)
        rgba[..., 3] = (np.clip(alfa, 0, 1) * 255 * OPACIDADE).astype(np.uint8)

        quadro = base.copy().convert("RGBA")
        quadro.alpha_composite(Image.fromarray(rgba, "RGBA"))

        ano, mes = m["key"].split("_")[1].split("-")
        saidas.append({
            "ano": ano, "mes": mes,
            "titulo": f"{MESES[mes]} de {ano}",
            "imagem": quadro.convert("RGB"),
        })

    return saidas


def build(name: str = "Baião", out_dir: Path | None = None) -> list[Path]:
    out_dir = Path(out_dir) if out_dir else Path.home() / "Desktop"
    out_dir.mkdir(parents=True, exist_ok=True)

    saidas = []
    for c in camadas(name):
        quadro = _moldura(c["imagem"], c["titulo"], f"Secura da vegetação · {name}")
        destino = out_dir / f"secura_{name.lower()}_{c['ano']}-{c['mes']}.png"
        quadro.save(destino)
        saidas.append(destino)
        print(f"  {destino}  {quadro.size[0]}x{quadro.size[1]}")
    return saidas


if __name__ == "__main__":
    import sys

    args = [a for a in sys.argv[1:] if a]
    nome = args[0] if args else "Baião"
    destino = args[1] if len(args) > 1 else None
    build(nome, destino)
