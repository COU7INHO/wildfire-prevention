"""Audit land cover / fuel type (COS 2018 v2, DGT) for a municipality.

Source: DGT GeoServer WMS, layer COS2018v2, GetMap with GeoJSON output
(format=application/json;type=geojson). The WFS is disabled, but GetMap returns
vector features. We tile the municipality bbox into sub-requests (the endpoint caps
features per request), dedupe by feature ID, then attribute each polygon to the
municipality by centroid-inside test and sum COS 'Area_ha' per class.

Nomenclature is hierarchical: COS18n1_L (broad, e.g. "Florestas") down to COS18n4_L
(species-level, e.g. "Florestas de eucalipto"). What the model gets from here: the
fuel TYPE per cell -- pine vs eucalyptus vs shrub burn very differently, and urban /
water are NON-FUEL (fire stops there).

Honest caveat: a polygon straddling the border is assigned whole (or dropped) by its
centroid; class-area totals are therefore approximate near the boundary. Fine for an
audit of the fuel mix.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import requests
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from .boundary import municipality_polygon

WMS_URL = "https://geo2.dgterritorio.gov.pt/geoserver/wms"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "cos"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"

# Available COS releases on the DGT GeoServer. Newer = better (land use changes).
VERSIONS = {
    "2023": {"layer": "COS-S2:cos2023v1-s2", "code": "COS23_n4_C", "label": "COS23_n4_L", "area": "AREA_ha"},
    "2018": {"layer": "COS2018:COS2018v2", "code": "COS18n4_C", "label": "COS18n4_L", "area": "Area_ha"},
    "2015": {"layer": "COS2015:COS2015v2", "code": "COS15n4_C", "label": "COS15n4_L", "area": "Area_ha"},
}

# COS level-1 legend: the first digit of the n4 code gives the broad class.
N1_LABELS = {
    "1": "Territórios artificializados",
    "2": "Agricultura",
    "3": "Pastagens",
    "4": "Superfícies agroflorestais",
    "5": "Florestas",
    "6": "Matos",
    "7": "Espaços descobertos ou com pouca vegetação",
    "8": "Zonas húmidas",
    "9": "Corpos de água",
}
# Fire stops here: urban, wetlands, water.
NON_FUEL_CODES = {"1", "8", "9"}


@dataclass
class CosAudit:
    municipality: str
    version: str
    total_ha: float
    by_level1: dict[str, float] = field(default_factory=dict)
    forest_species: dict[str, float] = field(default_factory=dict)
    fuel_ha: float = 0.0
    non_fuel_ha: float = 0.0


def _fetch_all(bbox, cfg: dict, version: str) -> list[dict]:
    """Fetch every COS polygon over the bbox in ONE high-resolution GetMap request.

    The WFS is disabled, so we use WMS GetMap with GeoJSON output. Render resolution
    (width/height) controls how many small polygons come back, so we size it to
    ~10 m/px (capped) — a single request then covers ~100% of a municipality.
    """
    minx, miny, maxx, maxy = bbox
    mid_lat = (miny + maxy) / 2
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))
    width = min(4096, max(1024, int((maxx - minx) * m_per_deg_lon / 10)))
    height = min(4096, max(1024, int((maxy - miny) * 111_320.0 / 10)))

    key = f"{version}_{minx:.4f}_{miny:.4f}_{maxx:.4f}_{maxy:.4f}_{width}x{height}"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{key}.geojson"
    if cache.exists():
        return json.loads(cache.read_text()).get("features", [])
    resp = requests.get(
        WMS_URL,
        params={
            "service": "WMS",
            "version": "1.1.1",
            "request": "GetMap",
            "layers": cfg["layer"],
            "srs": "EPSG:4326",
            "bbox": f"{minx},{miny},{maxx},{maxy}",
            "width": width,
            "height": height,
            "format": "application/json;type=geojson",
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    cache.write_text(json.dumps(data))
    return data.get("features", [])


# Fire-behaviour taxonomy from the COS level-4 code. The model still learns each
# class's effect — this only stops eucalyptus, pine and oak being collapsed into
# one "Florestas" bucket, which they are not: they burn very differently.
FUEL_CLASSES = {
    0: "Desconhecido",
    1: "Não combustível",       # urban, water, wetlands
    2: "Agricultura",
    3: "Pastagens/agroflorestal",
    4: "Matos",                  # fast-spreading understory
    5: "Eucalipto",              # high intensity, spotting
    6: "Resinosas (pinheiro)",
    7: "Folhosas (carvalho, castanheiro)",
    8: "Solo descoberto",
}


def fuel_class(code: str, label: str = "") -> int:
    """Map a COS level-4 code/label to the fire-behaviour class above."""
    if not code:
        return 0
    d = code[0]
    if d in ("1", "8", "9"):
        return 1
    if d == "2":
        return 2
    if d in ("3", "4"):
        return 3
    if d == "6":
        return 4
    if d == "7":
        return 8
    if d == "5":  # forests -> split by species
        lab = label.lower()
        if "eucalipto" in lab:
            return 5
        if any(s in lab for s in ("pinheiro", "resinosa")):
            return 6
        if any(s in lab for s in ("carvalho", "castanheiro", "sobreiro", "azinheira",
                                  "folhosa", "acácia", "acacia", "invasora")):
            return 7
        return 7  # unspecified forest -> broadleaf-ish default
    return 0


def fetch_fuel_polygons(name: str, version: str = "2023") -> list[tuple[BaseGeometry, str]]:
    """Return [(polygon, fuel-class id as str), ...] for the municipality.

    The class comes from the COS level-4 code AND label, so species detail
    (eucalyptus vs pine vs oak) survives instead of collapsing to "Florestas"."""
    cfg = VERSIONS[version]
    poly, bbox = municipality_polygon(name)
    features = {f["id"]: f for f in _fetch_all(bbox, cfg, version)}
    out = []
    for f in features.values():
        geom = _safe_shape(f.get("geometry"))
        if geom is None or not poly.intersects(geom):
            continue
        code = str(f["properties"].get(cfg["code"], ""))
        label = str(f["properties"].get(cfg["label"], ""))
        out.append((geom, str(fuel_class(code, label))))
    return out


def audit_municipality(name: str, version: str = "2023") -> CosAudit:
    cfg = VERSIONS[version]
    poly, bbox = municipality_polygon(name)

    features = {f["id"]: f for f in _fetch_all(bbox, cfg, version)}

    by_l1: dict[str, float] = defaultdict(float)
    species: dict[str, float] = defaultdict(float)
    fuel = non_fuel = 0.0

    for f in features.values():
        geom = _safe_shape(f.get("geometry"))
        if geom is None or not poly.contains(geom.representative_point()):
            continue
        p = f["properties"]
        area = float(p.get(cfg["area"]) or 0.0)
        code = str(p.get(cfg["code"], "?"))
        digit = code[0] if code else "?"
        l1 = N1_LABELS.get(digit, "?")
        by_l1[l1] += area
        if digit in NON_FUEL_CODES:
            non_fuel += area
        else:
            fuel += area
        if digit == "5":  # Florestas -> species detail
            species[p.get(cfg["label"], "?")] += area

    total = fuel + non_fuel
    return CosAudit(
        municipality=name,
        version=version,
        total_ha=round(total, 1),
        by_level1={k: round(v, 1) for k, v in sorted(by_l1.items(), key=lambda kv: -kv[1])},
        forest_species={k: round(v, 1) for k, v in sorted(species.items(), key=lambda kv: -kv[1])},
        fuel_ha=round(fuel, 1),
        non_fuel_ha=round(non_fuel, 1),
    )


def _safe_shape(geom) -> BaseGeometry | None:
    try:
        return shape(geom) if geom else None
    except Exception:
        return None


def print_report(a: CosAudit) -> None:
    print(f"\n=== Land cover / fuel type (COS {a.version}) audit: {a.municipality} ===\n")
    print(f"Total mapped area: {a.total_ha:,.0f} ha\n")
    print("Land cover (level 1):")
    non_fuel_labels = {N1_LABELS[c] for c in NON_FUEL_CODES}
    for label, ha in a.by_level1.items():
        pct = ha / a.total_ha * 100 if a.total_ha else 0
        tag = "  [NON-FUEL]" if label in non_fuel_labels else ""
        print(f"  {ha:>9,.0f} ha  {pct:>5.1f}%  {label}{tag}")
    print("\nForest by species (level 4):")
    for label, ha in a.forest_species.items():
        pct = ha / a.total_ha * 100 if a.total_ha else 0
        print(f"  {ha:>9,.0f} ha  {pct:>5.1f}%  {label}")
    print("\nFuel summary:")
    print(f"  flammable:  {a.fuel_ha:>9,.0f} ha  ({a.fuel_ha/a.total_ha*100:.1f}%)")
    print(f"  non-fuel:   {a.non_fuel_ha:>9,.0f} ha  ({a.non_fuel_ha/a.total_ha*100:.1f}%)")


def save_report(a: CosAudit) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"cos{a.version}_audit_{a.municipality.lower()}.json"
    out.write_text(json.dumps(vars(a), indent=2, ensure_ascii=False))
    return out


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "Baião"
    version = sys.argv[2] if len(sys.argv) > 2 else "2023"
    result = audit_municipality(name, version)
    print_report(result)
    path = save_report(result)
    print(f"\nSaved: {path}")
