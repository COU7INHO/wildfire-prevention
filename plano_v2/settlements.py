"""Audit settlements / values-at-risk (buildings) for a municipality.

Source: OpenStreetMap via the Overpass API (free, continuously updated).
We fetch building footprints inside the municipality, count them, and measure how
much of the municipality lies within 100 m / 250 m of a building — the
wildland-urban interface (WUI), where fire threatens people and property.

This is the CONSEQUENCE layer of the risk equation: prevention priority is not
just where fire is likely, but where it would hurt.

Honest caveats: OSM building coverage in rural Portugal is good but not complete
(some isolated sheds/annexes are unmapped). Counts are a lower bound. We audit
counts and WUI share here; exact per-cell distance rasters come later in the
feature-building phase.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests
import shapely
from shapely.geometry import Point
from shapely.strtree import STRtree

from .boundary import municipality_polygon

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"


@dataclass
class SettlementsAudit:
    municipality: str
    n_buildings: int
    wui_100m_share: float  # municipality fraction within 100 m of a building
    wui_250m_share: float
    grid_step_m: float


def _fetch_buildings(name: str, bbox) -> list[tuple[float, float]]:
    """Return building centroids (lon, lat) inside the bbox, cached."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"buildings_{name.lower()}.json"
    if cache.exists():
        return json.loads(cache.read_text())

    minx, miny, maxx, maxy = bbox
    # 'out center' gives one representative point per building way/relation —
    # enough for distance analysis without downloading full footprints.
    query = f"""
    [out:json][timeout:120];
    (
      way["building"]({miny},{minx},{maxy},{maxx});
      relation["building"]({miny},{minx},{maxy},{maxx});
    );
    out center;
    """
    headers = {"User-Agent": "fire-plano-v2-audit/0.1 (tiagomccoutinho@gmail.com)"}
    resp = requests.post(OVERPASS_URL, data={"data": query}, headers=headers, timeout=180)
    if resp.status_code in (406, 429, 504):
        # main instance refused/overloaded -> try the mirror
        resp = requests.post(
            "https://overpass.kumi.systems/api/interpreter",
            data={"data": query},
            headers=headers,
            timeout=180,
        )
    resp.raise_for_status()
    elements = resp.json().get("elements", [])
    pts = [
        (el["center"]["lon"], el["center"]["lat"])
        for el in elements
        if "center" in el
    ]
    cache.write_text(json.dumps(pts))
    return pts


def audit_municipality(name: str, grid_step_m: float = 100.0) -> SettlementsAudit:
    poly, bbox = municipality_polygon(name)
    all_pts = _fetch_buildings(name, bbox)

    lons = np.array([p[0] for p in all_pts])
    lats = np.array([p[1] for p in all_pts])
    inside_mask = shapely.contains_xy(poly, lons, lats)
    pts_in = [(lo, la) for (lo, la), ok in zip(all_pts, inside_mask) if ok]

    # Sample the municipality on a regular grid and measure distance to the
    # nearest building using an STRtree (fast spatial index).
    minx, miny, maxx, maxy = bbox
    mid_lat = (miny + maxy) / 2
    deg_lat = grid_step_m / 111_320.0
    deg_lon = grid_step_m / (111_320.0 * math.cos(math.radians(mid_lat)))

    xs = np.arange(minx, maxx, deg_lon)
    ys = np.arange(miny, maxy, deg_lat)
    gx, gy = np.meshgrid(xs, ys)
    sample_inside = shapely.contains_xy(poly, gx, gy)
    sample_pts = [Point(x, y) for x, y in zip(gx[sample_inside], gy[sample_inside])]

    tree = STRtree([Point(lo, la) for lo, la in pts_in])
    nearest_idx = tree.nearest(sample_pts)
    building_pts = [Point(lo, la) for lo, la in pts_in]

    within_100 = 0
    within_250 = 0
    for sp, bi in zip(sample_pts, nearest_idx):
        b = building_pts[bi]
        # approximate meters per degree at this latitude
        dx = (sp.x - b.x) * 111_320.0 * math.cos(math.radians(mid_lat))
        dy = (sp.y - b.y) * 111_320.0
        d = math.hypot(dx, dy)
        if d <= 100:
            within_100 += 1
        if d <= 250:
            within_250 += 1

    n = len(sample_pts)
    return SettlementsAudit(
        municipality=name,
        n_buildings=len(pts_in),
        wui_100m_share=round(within_100 / n, 3) if n else 0.0,
        wui_250m_share=round(within_250 / n, 3) if n else 0.0,
        grid_step_m=grid_step_m,
    )


def print_report(a: SettlementsAudit) -> None:
    print(f"\n=== Settlements / values-at-risk audit: {a.municipality} ===\n")
    print(f"Buildings (OSM, inside boundary): {a.n_buildings:,}")
    print(f"\nWildland-urban interface (sampled every {a.grid_step_m:.0f} m):")
    print(f"  within 100 m of a building: {a.wui_100m_share*100:.1f}% of the municipality")
    print(f"  within 250 m of a building: {a.wui_250m_share*100:.1f}% of the municipality")


def save_report(a: SettlementsAudit) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"settlements_audit_{a.municipality.lower()}.json"
    out.write_text(json.dumps(vars(a), indent=2, ensure_ascii=False))
    return out


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "Baião"
    result = audit_municipality(name)
    print_report(result)
    path = save_report(result)
    print(f"\nSaved: {path}")
