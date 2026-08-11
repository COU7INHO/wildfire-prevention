"""Official PMDFCI cartography for a municipality (downloaded from ICNF).

The municipal fire-defence plan (PMDFCI) publishes its geographic annexes as
zipped shapefiles at fogos.icnf.pt. For Baiao (DICO 1302) we use:

  RPA_Baiao.shp  - Rede de Pontos de Agua: the OFFICIAL water points firefighters
                   use (tanks, weirs, reservoirs) with name, type and volume.
  RVF_Baiao.shp  - official forest road network (future use)
  FGC_Baiao.shp  - planned fuel-management strips (future: plan-vs-model compare)

Coordinates are ETRS89 / Portugal TM06 (EPSG:3763) -> reprojected to WGS84.

Note: paths are wired to the Baiao package for now; generalizing = resolving the
per-municipality zip from the ICNF PMDFCI index layer (LinkMapa field).
"""

from __future__ import annotations

from pathlib import Path

import shapefile
from pyproj import Transformer

PACK_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "pmdfci_1302"
PACK2G_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "pmdfci_1302_2g"
_TO_WGS84 = Transformer.from_crs(3763, 4326, always_xy=True)


def rpa_points_full(name: str = "Baião") -> list[dict]:
    """FULL water-point network from the 2G plan (2014): 109 points, classes
    T (terrestres), M (mistos), A (aéreos). Older vintage than the 11 curated 3G
    points, but it is the complete inventory shown on the plan's Mapa n.24.
    CRS is read from the shapefile's own .prj (Datum Lisboa Hayford-Gauss)."""
    from pyproj import CRS

    shp = PACK2G_DIR / "RPA_1302"
    if not (PACK2G_DIR / "RPA_1302.shp").exists():
        return []
    crs = CRS.from_wkt((PACK2G_DIR / "RPA_1302.prj").read_text())
    to_wgs = Transformer.from_crs(crs, 4326, always_xy=True)
    reader = shapefile.Reader(str(shp))
    fields = [f[0] for f in reader.fields[1:]]
    out = []
    for sr in reader.iterShapeRecords():
        if not sr.shape.points:
            continue
        x, y = sr.shape.points[0]
        lon, lat = to_wgs.transform(x, y)
        rec = dict(zip(fields, sr.record))
        out.append({
            "lon": lon,
            "lat": lat,
            "nome": str(rec.get("NOME", "")).strip(),
            "classe": str(rec.get("CLASSE_PA", "")).strip(),
            "volume_m3": float(rec.get("VOL_MAX") or 0),
        })
    return out


def rpa_points(name: str = "Baião") -> list[dict]:
    """Official water points: [{lon, lat, nome, tipo, volume_m3}, ...]."""
    shp = PACK_DIR / "RPA_Baiao"
    if not (PACK_DIR / "RPA_Baiao.shp").exists():
        return []
    reader = shapefile.Reader(str(shp))
    fields = [f[0] for f in reader.fields[1:]]
    out = []
    for sr in reader.iterShapeRecords():
        if not sr.shape.points:
            continue
        x, y = sr.shape.points[0]
        lon, lat = _TO_WGS84.transform(x, y)
        rec = dict(zip(fields, sr.record))
        out.append({
            "lon": lon,
            "lat": lat,
            "nome": str(rec.get("NOME", "")).strip(),
            "tipo": rec.get("TIPO_PA"),
            "volume_m3": float(rec.get("VOL_ACT") or 0),
        })
    return out
