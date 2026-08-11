"""Current-year rural-fire OCCURRENCES (ignition points) from Civil Protection.

Source: api.fogos.pt (VOST Portugal), which aggregates ANEPC occurrence data —
the same feed behind fogos.pt. Unlike burnt-area cartography (ICNF, months of
delay), occurrences are near-real-time and include the small fires firefighters
kill early, which satellites never map.

We keep only vegetation-fire natures (Mato, Agrícola, Floresta/Povoamento,
Rescaldo) with coordinates, inside the municipality. Output feeds the app's
'Ignições <year>' overlay: dots to eyeball against the risk map — are this
year's ignitions landing in the zones the model flags?
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import requests
import shapely
from shapely.geometry import Point

from .boundary import municipality_polygon

API = "https://api.fogos.pt/v2/incidents/search"
WEB_DIR = Path(__file__).resolve().parent.parent / "webapp" / "public" / "data"
# True ignitions only: "Consolidação de Rescaldo" entries are revisits to
# already-extinguished fires (often duplicating the original dispatch), not
# new fire starts, so they are excluded.
FIRE_NATURES = ("mato", "agrícola", "agricola", "floresta", "povoamento")


def fetch_year(name: str, year: int) -> list[dict]:
    poly, _ = municipality_polygon(name)
    resp = requests.get(
        API,
        params={
            "after": f"{year}-01-01",
            "before": f"{year}-12-31",
            "concelho": name,
            "all": 1,
            "limit": 500,
        },
        timeout=60,
    )
    resp.raise_for_status()
    rows = resp.json().get("data", [])

    out = []
    for r in rows:
        nat = str(r.get("natureza", "")).lower()
        if not any(n in nat for n in FIRE_NATURES):
            continue
        lat, lng = r.get("lat"), r.get("lng")
        if not lat or not lng:
            continue
        if not poly.contains(Point(float(lng), float(lat))):
            continue
        out.append({
            "lat": float(lat),
            "lng": float(lng),
            "data": r.get("date", ""),
            "hora": r.get("hour", ""),
            "freguesia": r.get("freguesia", ""),
            "natureza": r.get("natureza", ""),
            "operacionais": int(r.get("man") or 0),
        })
    return out


def export(name: str = "Baião", year: int | None = None) -> Path:
    year = year or date.today().year
    occ = fetch_year(name, year)
    feats = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [o["lng"], o["lat"]]},
            "properties": {k: v for k, v in o.items() if k not in ("lat", "lng")},
        }
        for o in occ
    ]
    slug = name.lower().replace("ã", "a")
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    out = WEB_DIR / f"{slug}_ocorrencias.geojson"
    out.write_text(json.dumps(
        {"type": "FeatureCollection",
         "properties": {"year": year, "fetched": date.today().isoformat(), "source": "ANEPC via fogos.pt"},
         "features": feats},
        ensure_ascii=False,
    ))
    print(f"{len(feats)} ignições rurais de {year} em {name} -> {out}")
    return out


if __name__ == "__main__":
    import sys

    export(sys.argv[1] if len(sys.argv) > 1 else "Baião")
