"""Build the per-cell FEATURE GRID for a municipality — the model's dataset.

Every cell (~29 m, from the DEM grid) inside the municipality gets one row with:

  topography : elevation, slope, aspect (as northness/eastness)
  fuel       : COS 2023 level-1 class + a flammable flag
  exposure   : distance to nearest building, GHSL built-up in the cell
  fire history: how many years it burned (2009-2025), last burn year
  LABEL      : burned_ever (candidate target; temporal-holdout target comes later)

Sentinel NDVI/NDMI (current vegetation state) are appended in a later step; the
grid is built first as the foundation.

Output: data/out/features_<municipality>.npz — a compact table ready for the model.
Honest note: this is the *susceptibility* framing (which cells burn). Using
burned_ever both as a history feature and as the label would leak; the modelling
step splits by YEAR (train on some years, predict a held-out year) to avoid that.
Here we just assemble and report the grid so the class balance is visible.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely import STRtree
from shapely.geometry import shape

from . import cos, ghsl, icnf_labels, settlements, terrain

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"
FUEL_LABELS = cos.FUEL_CLASSES      # fire-behaviour classes (species-aware)
NON_FUEL = {"1"}                    # class 1 = urban / water / wetlands


def build(name: str, zoom: int = 12) -> dict:
    poly, mosaic, lons, lats, px_size = terrain.elevation_mosaic(name, zoom)

    # --- topography on the full mosaic, then keep only inside cells ---
    dzdy, dzdx = np.gradient(mosaic, px_size)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    aspect = np.radians(np.degrees(np.arctan2(dzdy, -dzdx)) % 360.0)

    lon_grid, lat_grid = np.meshgrid(lons, lats)
    inside = shapely.contains_xy(poly, lon_grid, lat_grid)

    cell_lon = lon_grid[inside]
    cell_lat = lat_grid[inside]
    n = cell_lon.size
    print(f"Grid: {n:,} cells (~{px_size:.0f} m) inside {name}")

    feat = {
        "lon": cell_lon,
        "lat": cell_lat,
        "elevation": mosaic[inside],
        "slope": slope[inside],
        "northness": np.cos(aspect[inside]),
        "eastness": np.sin(aspect[inside]),
    }

    mid_lat = float(cell_lat.mean())
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))
    cells_xy = np.column_stack([cell_lon * m_per_deg_lon, cell_lat * m_per_deg_lat])
    cell_points = shapely.points(cell_lon, cell_lat)

    # --- fuel type (COS 2023) ---
    print("Rasterizing COS 2023 fuel type...")
    fuel_polys = cos.fetch_fuel_polygons(name, "2023")
    fuel_code = _assign_polygon_value([g for g, _ in fuel_polys], [d for _, d in fuel_polys], cell_points, default="0")
    feat["fuel_code"] = np.array([int(c) for c in fuel_code], dtype=np.int8)
    feat["flammable"] = np.array([0 if c in NON_FUEL else 1 for c in fuel_code], dtype=np.int8)

    # --- fire history (ICNF, all years) ---
    print("Rasterizing ICNF fire history (all years)...")
    _, ibbox = _bbox(poly)
    burn_count = np.zeros(n, dtype=np.int16)
    last_year = np.zeros(n, dtype=np.int16)
    for layer_id, year in icnf_labels.list_year_layers():
        polys = [shape(f["geometry"]) for f in icnf_labels.fetch_year(layer_id, ibbox) if f.get("geometry")]
        if not polys:
            continue
        tree = STRtree(polys)
        hit_idx = np.unique(tree.query(cell_points, predicate="intersects")[0])
        burn_count[hit_idx] += 1
        last_year[hit_idx] = year
    feat["n_years_burned"] = burn_count
    feat["last_year_burned"] = last_year
    feat["burned_ever"] = (burn_count > 0).astype(np.int8)

    # --- exposure: distance to nearest building + GHSL built-up ---
    print("Computing distance to nearest building...")
    b_pts = settlements._fetch_buildings(name, _bbox(poly)[1])
    b_xy = np.array([[lo * m_per_deg_lon, la * m_per_deg_lat] for lo, la in b_pts])
    tree_b = cKDTree(b_xy)
    dist, _ = tree_b.query(cells_xy, k=1)
    feat["dist_building_m"] = dist.astype(np.float32)

    print("Sampling GHSL built-up surface...")
    feat["built_m2"] = _sample_ghsl(name, cell_lon, cell_lat).astype(np.float32)

    return {"name": name, "px_size_m": px_size, "n_cells": n, "features": feat}


def _bbox(poly):
    minx, miny, maxx, maxy = poly.bounds
    return poly, (minx, miny, maxx, maxy)


def _assign_polygon_value(polys, values, points, default):
    """For each point, the value of the polygon that contains it (default if none)."""
    out = [default] * len(points)
    if not polys:
        return out
    tree = STRtree(polys)
    pairs = tree.query(points, predicate="intersects")  # [point_idx, poly_idx]
    for pi, gi in zip(pairs[0], pairs[1]):
        out[pi] = values[gi]
    return out


def _sample_ghsl(name, lon, lat):
    import rasterio

    tif = ghsl._tif_path()
    with rasterio.open(tif) as src:
        rows, cols = zip(*[src.index(x, y) for x, y in zip(lon, lat)])
        rows = np.clip(np.array(rows), 0, src.height - 1)
        cols = np.clip(np.array(cols), 0, src.width - 1)
        band = src.read(1)
    vals = band[rows, cols].astype(np.float64)
    return np.where(vals < 0, 0, vals)


def save(grid: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"features_{grid['name'].lower()}.npz"
    np.savez_compressed(out, **grid["features"])
    return out


def report(grid: dict) -> None:
    f = grid["features"]
    n = grid["n_cells"]
    print(f"\n=== Feature grid: {grid['name']} ===\n")
    print(f"cells: {n:,}   columns: {len(f)}   resolution: ~{grid['px_size_m']:.0f} m\n")
    print("columns:", ", ".join(f.keys()))
    burned = int(f["burned_ever"].sum())
    print(f"\nLabel balance (burned_ever): {burned:,} burned / {n - burned:,} unburned "
          f"({burned / n * 100:.1f}% positive)")
    print("\nFuel mix (cells):")
    codes, counts = np.unique(f["fuel_code"], return_counts=True)
    for c, ct in sorted(zip(codes, counts), key=lambda kv: -kv[1]):
        print(f"  {FUEL_LABELS.get(int(c), '?'):<42} {ct:>8,} ({ct/n*100:4.1f}%)")
    print("\nSample cell (row 0):")
    for k, v in f.items():
        print(f"  {k:<18} {v[0]}")


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "Baião"
    grid = build(name)
    report(grid)
    path = save(grid)
    print(f"\nSaved: {path}")
