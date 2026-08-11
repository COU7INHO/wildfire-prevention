"""Operational-access features: roads, water sources, settlement cluster size.

Adds three per-cell features to the grid, feeding the 3-dimensional priority
(probability x exposed value x suppression difficulty):

  dist_road_m    : distance to the nearest road/forest track (firefighting access;
                   roads are also an ignition source — dual role)
  dist_water_m   : distance to the nearest water source usable for refilling
                   (rivers, reservoirs, tanks, hydrants). Proxy from OSM — the
                   official ICNF DFCI water-point registry is not publicly served.
  houses_500m    : number of buildings within 500 m (cluster size — a village of
                   200 exposed houses is not the same as one isolated shed).
                   OSM undercounts rural buildings, so treat as a RELATIVE measure.

Source: OpenStreetMap via Overpass (cached).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from .boundary import municipality_polygon
from .buildings import get_buildings
from .settlements import OVERPASS_URL

import requests

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"
UA = {"User-Agent": "fire-plano-v2-audit/0.1 (tiagomccoutinho@gmail.com)"}

ROAD_RE = "^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|service|track)$"


def _overpass(query: str, cache_name: str) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / cache_name
    if cache.exists():
        return json.loads(cache.read_text())
    resp = requests.post(OVERPASS_URL, data={"data": query}, headers=UA, timeout=300)
    if resp.status_code in (406, 429, 504):
        resp = requests.post("https://overpass.kumi.systems/api/interpreter",
                             data={"data": query}, headers=UA, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    cache.write_text(json.dumps(data))
    return data


def _element_points(elements: list[dict], every: int = 3) -> list[tuple[float, float]]:
    """Sample (lon, lat) points from Overpass elements (nodes, way geometry, centers)."""
    pts = []
    for el in elements:
        if el["type"] == "node":
            pts.append((el["lon"], el["lat"]))
        elif "geometry" in el:
            geom = el["geometry"]
            pts.extend((g["lon"], g["lat"]) for g in geom[::every])
        elif "center" in el:
            pts.append((el["center"]["lon"], el["center"]["lat"]))
    return pts


def fetch_roads(name: str, bbox) -> list[tuple[float, float]]:
    minx, miny, maxx, maxy = bbox
    q = f"""
    [out:json][timeout:180];
    way["highway"~"{ROAD_RE}"]({miny},{minx},{maxy},{maxx});
    out geom;
    """
    data = _overpass(q, f"roads_{name.lower()}.json")
    return _element_points(data.get("elements", []))


def fetch_fire_stations(name: str, bbox, pad_deg: float = 0.25) -> list[tuple[float, float]]:
    """Fire stations in an EXPANDED box: neighbouring municipalities' quartéis
    also respond, so the nearest one may sit outside the boundary."""
    minx, miny, maxx, maxy = bbox
    bb = f"({miny - pad_deg},{minx - pad_deg},{maxy + pad_deg},{maxx + pad_deg})"
    q = f"""
    [out:json][timeout:120];
    (
      node["amenity"="fire_station"]{bb};
      way["amenity"="fire_station"]{bb};
    );
    out geom;
    """
    data = _overpass(q, f"fire_stations_{name.lower()}.json")
    pts = []
    for el in data.get("elements", []):
        if el["type"] == "node":
            pts.append((el["lon"], el["lat"]))
        elif "geometry" in el:  # building outline -> centroid
            g = el["geometry"]
            pts.append((sum(p["lon"] for p in g) / len(g), sum(p["lat"] for p in g) / len(g)))
    return pts


def fetch_water(name: str, bbox) -> list[tuple[float, float]]:
    minx, miny, maxx, maxy = bbox
    bb = f"({miny},{minx},{maxy},{maxx})"
    q = f"""
    [out:json][timeout:180];
    (
      node["emergency"="fire_hydrant"]{bb};
      way["natural"="water"]{bb};
      way["waterway"~"^(river|canal)$"]{bb};
      way["man_made"~"^(reservoir|water_tank)$"]{bb};
      node["man_made"~"^(water_tank|water_tower)$"]{bb};
    );
    out geom;
    """
    data = _overpass(q, f"water_{name.lower()}.json")
    return _element_points(data.get("elements", []))


def build(name: str = "Baião") -> Path:
    poly, bbox = municipality_polygon(name)
    npz_path = OUT_DIR / f"features_{name.lower()}.npz"
    f = np.load(npz_path)
    lon, lat = f["lon"], f["lat"]

    mid_lat = float(lat.mean())
    kx = 111_320.0 * math.cos(math.radians(mid_lat))
    ky = 111_320.0
    cells_xy = np.column_stack([lon * kx, lat * ky])

    print("Fetching roads (OSM)...")
    roads = fetch_roads(name, bbox)
    print(f"  {len(roads):,} road points")
    print("Fetching water sources (OSM rivers/tanks + official PMDFCI RPA)...")
    water = fetch_water(name, bbox)
    from .pmdfci import rpa_points, rpa_points_full

    rpa = rpa_points(name) + rpa_points_full(name)
    water = water + [(p["lon"], p["lat"]) for p in rpa]
    print(f"  {len(water):,} water points ({len(rpa)} official RPA, 3G+2G)")
    print("Fetching buildings (MS ML footprints, OSM fallback)...")
    buildings, b_source = get_buildings(name, bbox)
    print(f"  {len(buildings):,} buildings ({b_source})")
    print("Fetching fire stations...")
    stations_all = fetch_fire_stations(name, bbox)
    # Municipal-capacity view: only the municipality's own quartéis. (Real
    # dispatch includes neighbours via mutual aid — this is the conservative
    # planning view of what the câmara's own means cover.)
    import shapely as _shp

    s_lon = np.array([p[0] for p in stations_all])
    s_lat = np.array([p[1] for p in stations_all])
    inside_s = _shp.contains_xy(poly, s_lon, s_lat)
    stations = [p for p, ok in zip(stations_all, inside_s) if ok]
    if not stations:
        print("  WARNING: none mapped inside the municipality — using expanded set")
        stations = stations_all
    print(f"  {len(stations):,} fire stations (inside {name}; {len(stations_all):,} in expanded box)")

    def dist_to(pts):
        xy = np.array([[p[0] * kx, p[1] * ky] for p in pts])
        d, _ = cKDTree(xy).query(cells_xy, k=1)
        return d.astype(np.float32)

    print("Computing distances + cluster size...")
    dist_road = dist_to(roads)
    dist_water = dist_to(water)
    dist_building = dist_to(buildings)  # replaces the OSM-based column from features.py

    b_xy = np.array([[p[0] * kx, p[1] * ky] for p in buildings])
    b_tree = cKDTree(b_xy)
    houses_500 = b_tree.query_ball_point(cells_xy, r=500.0, return_length=True).astype(np.int16)
    # 250 m aligns with the legal fuel-management doctrine (50 m isolated /
    # 100 m aglomerados): protecting houses is decided in the first ~250 m,
    # not at 500 m — a generous halo overstates exposure deep in shrubland.
    houses_250 = b_tree.query_ball_point(cells_xy, r=250.0, return_length=True).astype(np.int16)

    dist_bombeiros = dist_to(stations)

    data = {k: f[k] for k in f.files}
    data["dist_road_m"] = dist_road
    data["dist_water_m"] = dist_water
    data["dist_building_m"] = dist_building
    data["houses_500m"] = houses_500
    data["houses_250m"] = houses_250
    data["dist_bombeiros_m"] = dist_bombeiros
    np.savez_compressed(npz_path, **data)

    print(f"\ndist_road_m : mediana {np.median(dist_road):.0f} m   p90 {np.percentile(dist_road,90):.0f} m")
    print(f"dist_water_m: mediana {np.median(dist_water):.0f} m   p90 {np.percentile(dist_water,90):.0f} m")
    print(f"houses_500m : mediana {np.median(houses_500):.0f}     p90 {np.percentile(houses_500,90):.0f}   max {houses_500.max()}")
    print(f"bombeiros   : mediana {np.median(dist_bombeiros):.0f} m   p90 {np.percentile(dist_bombeiros,90):.0f} m")
    print(f"Saved -> {npz_path}")
    return npz_path


if __name__ == "__main__":
    import sys

    build(sys.argv[1] if len(sys.argv) > 1 else "Baião")
