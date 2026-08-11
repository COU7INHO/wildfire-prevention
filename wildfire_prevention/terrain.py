"""Audit terrain (DEM) for a municipality and derive slope + aspect.

Source: AWS Terrain Tiles, Terrarium encoding (free, no API key). Each 256x256
web-mercator PNG tile encodes elevation in RGB:

    elevation_m = (R * 256 + G + B / 256) - 32768

We mosaic the tiles covering the municipality bbox, decode elevation, derive slope
(degrees) and aspect (compass degrees), then mask to the real municipality polygon
so the statistics describe Baião and not its neighbours.

What the model gets from here:
- slope   : fire climbs faster uphill  -> strong spread factor
- aspect  : south/southwest slopes dry out more -> more fire-prone
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests
import shapely
from PIL import Image

from .boundary import municipality_polygon

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "terrarium"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"
TILE_SIZE = 256


def _lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2**z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


def _fetch_tile(z: int, x: int, y: int) -> np.ndarray:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{z}_{x}_{y}.png"
    if cache.exists():
        data = cache.read_bytes()
    else:
        resp = requests.get(TILE_URL.format(z=z, x=x, y=y), timeout=30)
        resp.raise_for_status()
        data = resp.content
        cache.write_bytes(data)
    img = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float64)
    return (img[..., 0] * 256.0 + img[..., 1] + img[..., 2] / 256.0) - 32768.0


@dataclass
class TerrainAudit:
    municipality: str
    zoom: int
    px_size_m: float
    n_cells: int
    elev_min: float
    elev_max: float
    elev_mean: float
    slope_mean: float
    slope_p90: float
    slope_max: float
    steep_share: float  # fraction of cells with slope > 20 degrees
    south_facing_share: float  # fraction facing 135-225 degrees


def elevation_mosaic(name: str, zoom: int = 12):
    """Return (poly, mosaic, lons, lats, px_size_m) for the municipality bbox.

    mosaic[j, i] is elevation (m); lons/lats are the pixel-centre coordinates of
    the columns/rows. Shared by the terrain audit and the feature grid builder.
    """
    poly, (minlon, minlat, maxlon, maxlat) = municipality_polygon(name)

    x0, y0 = _lonlat_to_tile(minlon, maxlat, zoom)  # top-left
    x1, y1 = _lonlat_to_tile(maxlon, minlat, zoom)  # bottom-right
    tx0, tx1 = int(math.floor(x0)), int(math.floor(x1))
    ty0, ty1 = int(math.floor(y0)), int(math.floor(y1))

    rows = ty1 - ty0 + 1
    cols = tx1 - tx0 + 1
    mosaic = np.empty((rows * TILE_SIZE, cols * TILE_SIZE), dtype=np.float64)
    for j, ty in enumerate(range(ty0, ty1 + 1)):
        for i, tx in enumerate(range(tx0, tx1 + 1)):
            mosaic[j * TILE_SIZE:(j + 1) * TILE_SIZE, i * TILE_SIZE:(i + 1) * TILE_SIZE] = _fetch_tile(zoom, tx, ty)

    n = 2**zoom
    px_x = tx0 * TILE_SIZE + np.arange(cols * TILE_SIZE)
    px_y = ty0 * TILE_SIZE + np.arange(rows * TILE_SIZE)
    lons = px_x / (TILE_SIZE * n) * 360.0 - 180.0
    lat_rad = np.pi - 2.0 * np.pi * px_y / (TILE_SIZE * n)
    lats = np.degrees(np.arctan(np.sinh(lat_rad)))
    px_size = 156543.03392 * math.cos(math.radians((minlat + maxlat) / 2)) / n
    return poly, mosaic, lons, lats, px_size


def audit_municipality(name: str, zoom: int = 12) -> TerrainAudit:
    poly, mosaic, lons, lats, px_size = elevation_mosaic(name, zoom)

    # Slope and aspect from the elevation gradient.
    dzdy, dzdx = np.gradient(mosaic, px_size)
    slope_deg = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    aspect = np.degrees(np.arctan2(dzdy, -dzdx)) % 360.0

    # Mask to the real municipality polygon.
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    inside = shapely.contains_xy(poly, lon_grid, lat_grid)

    elev_in = mosaic[inside]
    slope_in = slope_deg[inside]
    aspect_in = aspect[inside]
    south = (aspect_in >= 135) & (aspect_in <= 225)

    return TerrainAudit(
        municipality=name,
        zoom=zoom,
        px_size_m=round(px_size, 1),
        n_cells=int(inside.sum()),
        elev_min=round(float(elev_in.min()), 1),
        elev_max=round(float(elev_in.max()), 1),
        elev_mean=round(float(elev_in.mean()), 1),
        slope_mean=round(float(slope_in.mean()), 1),
        slope_p90=round(float(np.percentile(slope_in, 90)), 1),
        slope_max=round(float(slope_in.max()), 1),
        steep_share=round(float((slope_in > 20).mean()), 3),
        south_facing_share=round(float(south.mean()), 3),
    )


def print_report(a: TerrainAudit) -> None:
    print(f"\n=== Terrain (DEM) audit: {a.municipality} ===\n")
    print(f"Zoom {a.zoom}  |  ~{a.px_size_m} m/cell  |  {a.n_cells:,} cells inside boundary\n")
    print("Elevation (m):")
    print(f"  min {a.elev_min}   mean {a.elev_mean}   max {a.elev_max}")
    print("\nSlope (degrees):")
    print(f"  mean {a.slope_mean}   p90 {a.slope_p90}   max {a.slope_max}")
    print(f"  steep terrain (>20 deg): {a.steep_share*100:.1f}% of the municipality")
    print("\nAspect:")
    print(f"  south/SW-facing (135-225 deg): {a.south_facing_share*100:.1f}% of cells")


def save_report(a: TerrainAudit) -> Path:
    import json

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"terrain_audit_{a.municipality.lower()}.json"
    out.write_text(json.dumps(vars(a), indent=2, ensure_ascii=False))
    return out


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "Baião"
    result = audit_municipality(name)
    print_report(result)
    path = save_report(result)
    print(f"\nSaved: {path}")
