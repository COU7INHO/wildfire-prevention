"""Fetch and cache a Portuguese municipality boundary polygon from OpenStreetMap.

We use Nominatim (polygon_geojson=1). The boundary is cached to disk so we do not
hammer the free public endpoint on every run.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "wildfire-prevention/0.1 (tiagomccoutinho@gmail.com)"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"


def fetch_municipality(name: str, country: str = "Portugal") -> dict:
    """Return the raw GeoJSON geometry dict for a municipality, cached to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"boundary_{name.lower()}.geojson"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    resp = requests.get(
        NOMINATIM_URL,
        params={
            "q": f"{name}, {country}",
            "format": "json",
            "limit": 5,
            "polygon_geojson": 1,
            "addressdetails": 1,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json()

    # Prefer an administrative boundary (the concelho), not a point/village.
    chosen = None
    for r in results:
        geom = r.get("geojson", {})
        if geom.get("type") in ("Polygon", "MultiPolygon"):
            chosen = r
            break
    if chosen is None:
        raise ValueError(f"No polygon boundary found for {name!r}")

    cache_file.write_text(json.dumps(chosen))
    return chosen


def municipality_polygon(name: str) -> tuple[BaseGeometry, tuple[float, float, float, float]]:
    """Return (shapely polygon in WGS84, bbox=(minlon, minlat, maxlon, maxlat))."""
    raw = fetch_municipality(name)
    geom = shape(raw["geojson"])
    minx, miny, maxx, maxy = geom.bounds
    return geom, (minx, miny, maxx, maxy)
