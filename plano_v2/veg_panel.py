"""Build the multi-year vegetation panel: era-appropriate vegetation per cell.

For each summer 2015-2024, a Sentinel-2 NDVI/NDMI median composite (vegetation as
it WAS that year), plus the fuel type from the COS epoch in force at the time
(2015 / 2018 / 2023). This feeds the panel training design:

    vegetation of year t  ->  predict fires of year t+1     (10 pairs)

Resumable: results are saved into veg_panel_<name>.npz after EVERY year, so an
interrupted run continues where it left off. All Sentinel band JP2s are kept
locally under data/cache/sentinel/ (a full local archive, ~5-7 GB for 10 years).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import shapely

from . import cos, sentinel_bands
from shapely import STRtree

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"
YEARS = list(range(2015, 2026))  # 2025 veg -> lets the readiness axis speak about 2026
COS_EPOCHS = ["2015", "2018", "2023"]


def _panel_path(name: str) -> Path:
    return OUT_DIR / f"veg_panel_{name.lower()}.npz"


def _fuel_for_version(name: str, version: str, lon, lat) -> np.ndarray:
    polys = cos.fetch_fuel_polygons(name, version)
    points = shapely.points(lon, lat)
    out = np.zeros(lon.size, dtype=np.int8)
    if polys:
        tree = STRtree([g for g, _ in polys])
        digits = [int(d) if d.isdigit() else 0 for _, d in polys]
        pairs = tree.query(points, predicate="intersects")
        for pi, gi in zip(pairs[0], pairs[1]):
            out[pi] = digits[gi]
    return out


def build(name: str = "Baião") -> Path:
    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    lon, lat = f["lon"], f["lat"]
    path = _panel_path(name)
    data = dict(np.load(path)) if path.exists() else {}

    # fuel per COS epoch (fast WMS fetches, cached)
    for ver in COS_EPOCHS:
        key = f"fuel_{ver}"
        if key in data:
            continue
        print(f"Rasterizing COS {ver} fuel...")
        data[key] = _fuel_for_version(name, ver, lon, lat)
        np.savez_compressed(path, **data)

    # NDVI/NDMI per summer (slow: band downloads) — resumable year by year
    for year in YEARS:
        if f"ndvi_{year}" in data:
            print(f"[{year}] already done, skipping")
            continue
        ndvi, ndmi = sentinel_bands.composite_for_year(name, year)
        data[f"ndvi_{year}"] = ndvi.astype(np.float32)
        data[f"ndmi_{year}"] = ndmi.astype(np.float32)
        np.savez_compressed(path, **data)
        n_valid = float((~np.isnan(ndvi)).mean()) * 100
        print(f"[{year}] NDVI median {np.nanmedian(ndvi):.3f}  valid {n_valid:.0f}%  -> saved (bands kept locally)")

    print(f"\nPanel complete: {path}")
    return path


if __name__ == "__main__":
    import sys

    build(sys.argv[1] if len(sys.argv) > 1 else "Baião")
