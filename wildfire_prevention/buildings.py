"""Building locations from Microsoft Global ML Building Footprints.

Why: OSM building coverage in rural Portugal is very incomplete (Baiao: 1,438
mapped vs ~23k GHSL building-equivalents), which silently under-weights the
exposure dimension of the priority. Microsoft's dataset is ML-extracted from
satellite imagery — it sees every roof the satellite sees. Free, open licence.

Distribution: a links CSV maps (country, level-9 quadkey) -> a gzipped
GeoJSONL file of building polygons. We find the quadkey(s) covering the
municipality bbox, download, and keep polygon CENTROIDS inside the bbox.

Fallback: OSM (settlements._fetch_buildings) if the download fails.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
from pathlib import Path

import requests

from .settlements import _fetch_buildings as _osm_buildings

LINKS_CSV = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
ZOOM = 9


def _tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def _quadkey(x: int, y: int, z: int) -> str:
    qk = []
    for i in range(z, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if x & mask:
            digit += 1
        if y & mask:
            digit += 2
        qk.append(str(digit))
    return "".join(qk)


def _bbox_quadkeys(bbox) -> set[str]:
    minx, miny, maxx, maxy = bbox
    x0, y0 = _tile(minx, maxy, ZOOM)
    x1, y1 = _tile(maxx, miny, ZOOM)
    return {_quadkey(x, y, ZOOM) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}


def _portugal_links() -> dict[str, str]:
    """quadkey -> url for Portugal, cached."""
    cache = CACHE_DIR / "ms_links_portugal.json"
    if cache.exists():
        return json.loads(cache.read_text())
    links: dict[str, str] = {}
    with requests.get(LINKS_CSV, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        lines = (line.decode("utf-8") for line in resp.iter_lines())
        for row in csv.DictReader(lines):
            if row.get("Location") == "Portugal":
                links[str(row["QuadKey"])] = row["Url"]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(links))
    return links


def fetch_ms_buildings(name: str, bbox) -> list[tuple[float, float]]:
    cache = CACHE_DIR / f"ms_buildings_{name.lower()}.json"
    if cache.exists():
        return [tuple(p) for p in json.loads(cache.read_text())]

    minx, miny, maxx, maxy = bbox
    links = _portugal_links()
    pts: list[tuple[float, float]] = []
    for qk in _bbox_quadkeys(bbox):
        url = links.get(qk)
        if not url:
            continue
        print(f"  downloading MS footprints tile {qk}...")
        resp = requests.get(url, timeout=600)
        resp.raise_for_status()
        with gzip.open(io.BytesIO(resp.content), "rt") as fh:
            for line in fh:
                geom = json.loads(line).get("geometry", {})
                ring = geom.get("coordinates", [[]])[0]
                if not ring:
                    continue
                lon = sum(c[0] for c in ring) / len(ring)
                lat = sum(c[1] for c in ring) / len(ring)
                if minx <= lon <= maxx and miny <= lat <= maxy:
                    pts.append((lon, lat))
    if pts:
        cache.write_text(json.dumps(pts))
    return pts


def get_buildings(name: str, bbox) -> tuple[list[tuple[float, float]], str]:
    """Return (building centroids, source_label). MS footprints, OSM fallback."""
    try:
        pts = fetch_ms_buildings(name, bbox)
        if len(pts) > 100:
            return pts, "microsoft-ml"
    except Exception as exc:  # network / format failure -> honest fallback
        print(f"  MS footprints unavailable ({exc}); falling back to OSM")
    return _osm_buildings(name, bbox), "osm"
