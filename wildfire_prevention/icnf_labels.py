"""Audit ICNF burnt-area labels for a municipality.

This is the FIRST and most important data-audit step: before any model, we must
know how many real fire labels exist and over which years. Labels are the binding
constraint for supervised training.

Source: ICNF ArcGIS REST MapServer "Territorios ardidos" (one layer per year).
We query each yearly layer by the municipality bounding box, then clip to the real
municipality polygon so we do not count fires that belong to neighbouring concelhos.

Honest caveat: a fire polygon that straddles the border is counted whole (its full
AreaHaPoly), not clipped proportionally. The feature COUNT is exact (intersects);
the AREA total is a slight over-estimate for border fires. Good enough for an audit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import requests
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from .boundary import municipality_polygon

MAPSERVER = "https://sigservices.icnf.pt/server/rest/services/BDG/areas_ardidas/MapServer"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"


@dataclass
class YearAudit:
    year: int
    layer_id: int
    n_fires: int
    total_ha: float
    largest_ha: float


@dataclass
class LabelsAudit:
    municipality: str
    years: list[YearAudit] = field(default_factory=list)

    @property
    def total_fires(self) -> int:
        return sum(y.n_fires for y in self.years)

    @property
    def total_ha(self) -> float:
        return sum(y.total_ha for y in self.years)

    @property
    def year_span(self) -> tuple[int, int] | None:
        present = [y.year for y in self.years if y.n_fires > 0]
        return (min(present), max(present)) if present else None


def list_year_layers() -> list[tuple[int, int]]:
    """Return [(layer_id, year), ...] parsed from the MapServer root."""
    resp = requests.get(MAPSERVER, params={"f": "json"}, timeout=30)
    resp.raise_for_status()
    layers = resp.json().get("layers", [])
    out: list[tuple[int, int]] = []
    for lyr in layers:
        # Layer names look like "Áreas Ardidas 2017".
        digits = "".join(c for c in lyr["name"] if c.isdigit())
        if len(digits) == 4:
            out.append((lyr["id"], int(digits)))
    return sorted(out, key=lambda t: t[1])


def fetch_year(layer_id: int, bbox: tuple[float, float, float, float]) -> list[dict]:
    """Fetch burnt-area features for one yearly layer intersecting the bbox (GeoJSON)."""
    minx, miny, maxx, maxy = bbox
    resp = requests.get(
        f"{MAPSERVER}/{layer_id}/query",
        params={
            "geometry": f"{minx},{miny},{maxx},{maxy}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "outSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "Ano,AreaHaPoly",
            "returnGeometry": "true",
            "f": "geojson",
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("features", [])


def audit_municipality(name: str) -> LabelsAudit:
    poly, bbox = municipality_polygon(name)
    audit = LabelsAudit(municipality=name)

    for layer_id, year in list_year_layers():
        features = fetch_year(layer_id, bbox)
        inside = _clip_to_polygon(features, poly)
        areas = [f["properties"].get("AreaHaPoly") or 0.0 for f in inside]
        audit.years.append(
            YearAudit(
                year=year,
                layer_id=layer_id,
                n_fires=len(inside),
                total_ha=round(sum(areas), 2),
                largest_ha=round(max(areas), 2) if areas else 0.0,
            )
        )
    return audit


def _clip_to_polygon(features: list[dict], poly: BaseGeometry) -> list[dict]:
    kept = []
    for f in features:
        geom = f.get("geometry")
        if geom is None:
            continue
        try:
            if shape(geom).intersects(poly):
                kept.append(f)
        except Exception:
            continue
    return kept


def print_report(audit: LabelsAudit) -> None:
    print(f"\n=== ICNF burnt-area label audit: {audit.municipality} ===\n")
    print(f"{'Year':<6}{'Fires':>7}{'Total ha':>12}{'Largest ha':>13}")
    print("-" * 38)
    for y in audit.years:
        marker = "" if y.n_fires else "   (no data)"
        print(f"{y.year:<6}{y.n_fires:>7}{y.total_ha:>12.1f}{y.largest_ha:>13.1f}{marker}")
    print("-" * 38)
    span = audit.year_span
    print(f"{'TOTAL':<6}{audit.total_fires:>7}{audit.total_ha:>12.1f}")
    if span:
        print(f"\nYears with fires: {span[0]}–{span[1]}  ({sum(1 for y in audit.years if y.n_fires)} of {len(audit.years)} layers)")
    print(f"Total fire labels available for training: {audit.total_fires}")


def save_report(audit: LabelsAudit) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"labels_audit_{audit.municipality.lower()}.json"
    out.write_text(
        json.dumps(
            {
                "municipality": audit.municipality,
                "total_fires": audit.total_fires,
                "total_ha": round(audit.total_ha, 2),
                "year_span": audit.year_span,
                "by_year": [vars(y) for y in audit.years],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return out


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "Baião"
    result = audit_municipality(name)
    print_report(result)
    path = save_report(result)
    print(f"\nSaved: {path}")
