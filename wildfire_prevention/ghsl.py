"""Audit built-up surface (GHSL) for a municipality — the satellite view of houses.

Source: JRC Global Human Settlement Layer, GHS-BUILT-S R2023A, epoch 2020,
WGS84 3 arc-second grid (~90 m at the equator, ~70 m E-W at Baiao's latitude).
Each cell value = built-up surface in m2 within that cell (from Sentinel imagery
via ML classification — independent of OSM volunteer mapping).

Why: the OSM audit undercounted buildings (1,438 vs >10k expected from INE), so
WUI shares from OSM are a lower bound. GHSL sees every roof the satellite sees.
Here we quantify total built-up surface and how it spreads across the municipality,
to complement (not replace) OSM point locations.

One 10x10 degree tile (R5_C18: 40-50N, 10W-0) covers all of northern Portugal.
The ~26 MB zip is downloaded once and cached.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests
import shapely

from .boundary import municipality_polygon

TILE = "R5_C18"
BASE = (
    "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/"
    "GHS_BUILT_S_E2020_GLOBE_R2023A_4326_3ss/V1-0/tiles/"
)
ZIP_NAME = f"GHS_BUILT_S_E2020_GLOBE_R2023A_4326_3ss_V1_0_{TILE}.zip"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "ghsl"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"


@dataclass
class GhslAudit:
    municipality: str
    epoch: str
    cell_size_arcsec: float
    n_cells: int
    built_cells: int
    built_share: float          # fraction of cells with any built-up surface
    total_built_m2: float
    est_buildings_equiv: int    # rough equivalent at 150 m2 per building footprint


def _tif_path() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tifs = list(CACHE_DIR.glob("*.tif"))
    if tifs:
        return tifs[0]
    resp = requests.get(BASE + ZIP_NAME, timeout=300)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for name in zf.namelist():
            if name.endswith(".tif"):
                zf.extract(name, CACHE_DIR)
                return CACHE_DIR / name
    raise RuntimeError("No .tif found inside GHSL zip")


def audit_municipality(name: str) -> GhslAudit:
    import rasterio
    from rasterio.windows import from_bounds

    poly, (minx, miny, maxx, maxy) = municipality_polygon(name)
    tif = _tif_path()

    with rasterio.open(tif) as src:
        window = from_bounds(minx, miny, maxx, maxy, src.transform)
        data = src.read(1, window=window).astype(np.float64)
        wt = src.window_transform(window)
        rows, cols = data.shape
        xs = wt.c + wt.a * (np.arange(cols) + 0.5)
        ys = wt.f + wt.e * (np.arange(rows) + 0.5)

    gx, gy = np.meshgrid(xs, ys)
    inside = shapely.contains_xy(poly, gx, gy)

    vals = data[inside]
    vals = np.where(vals < 0, 0, vals)  # nodata guard
    built = vals > 0

    total_m2 = float(vals.sum())
    return GhslAudit(
        municipality=name,
        epoch="2020",
        cell_size_arcsec=3.0,
        n_cells=int(inside.sum()),
        built_cells=int(built.sum()),
        built_share=round(float(built.mean()), 4),
        total_built_m2=round(total_m2, 0),
        est_buildings_equiv=int(total_m2 / 150.0),
    )


def print_report(a: GhslAudit) -> None:
    print(f"\n=== Built-up surface (GHSL {a.epoch}) audit: {a.municipality} ===\n")
    print(f"Grid: 3 arc-second (~90 m), {a.n_cells:,} cells inside boundary")
    print(f"Cells containing built-up surface: {a.built_cells:,} ({a.built_share*100:.1f}%)")
    print(f"Total built-up surface: {a.total_built_m2/1e4:,.1f} ha")
    print(f"Rough building equivalent (150 m2 each): ~{a.est_buildings_equiv:,}")


def save_report(a: GhslAudit) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"ghsl_audit_{a.municipality.lower()}.json"
    out.write_text(json.dumps(vars(a), indent=2, ensure_ascii=False))
    return out


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "Baião"
    result = audit_municipality(name)
    print_report(result)
    path = save_report(result)
    print(f"\nSaved: {path}")
