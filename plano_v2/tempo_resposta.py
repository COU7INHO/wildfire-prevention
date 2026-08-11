"""Fire-brigade travel time per cell, from real road routing.

The first version estimated it from straight-line distance with a detour factor.
Checked against real routing, it was roughly half the true time: mountain roads
wind, and a cell across a valley can be minutes away by road while being metres
away in a straight line. A distance proxy cannot know that.

This routes a sample grid through OSRM (real road network, driving profile) and
interpolates to every cell by inverse-distance weighting over the nearest routed
points. Validation against held-out points is printed when run directly.

What it reports is TRAVEL time from the nearest station, which is what a
municipal technician can verify on any mapping service. Real response time is
larger: a volunteer corps also needs mobilisation time before the vehicle leaves.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import requests
from scipy.spatial import cKDTree
from shapely.geometry import Point

from .access import fetch_fire_stations
from .boundary import municipality_polygon

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"
OSRM = "http://router.project-osrm.org/table/v1/driving"
LOTE = 90          # destinations per request (public server limit)
ESPACAMENTO_M = 700


def _stations(name: str):
    poly, bbox = municipality_polygon(name)
    dentro = [p for p in fetch_fire_stations(name, bbox) if poly.contains(Point(p[0], p[1]))]
    return dentro or fetch_fire_stations(name, bbox), poly


def _grid(poly, espacamento_m: float = ESPACAMENTO_M):
    minx, miny, maxx, maxy = poly.bounds
    mid = (miny + maxy) / 2
    dlon = espacamento_m / (111_320.0 * np.cos(np.radians(mid)))
    dlat = espacamento_m / 111_320.0
    pts = [(x, y)
           for x in np.arange(minx, maxx, dlon)
           for y in np.arange(miny, maxy, dlat)
           if poly.contains(Point(x, y))]
    return pts


def _route_minutes(quarteis, destinos):
    """Minutes from the fastest station to each destination (NaN if unroutable)."""
    out = np.full(len(destinos), np.nan)
    src = ";".join(str(i) for i in range(len(quarteis)))
    for i in range(0, len(destinos), LOTE):
        lote = destinos[i:i + LOTE]
        coords = ";".join(f"{x},{y}" for x, y in list(quarteis) + lote)
        dst = ";".join(str(j) for j in range(len(quarteis), len(quarteis) + len(lote)))
        try:
            r = requests.get(f"{OSRM}/{coords}?sources={src}&destinations={dst}", timeout=90).json()
            if r.get("code") == "Ok":
                dur = np.array(r["durations"], dtype=float)
                out[i:i + len(lote)] = np.nanmin(dur, axis=0) / 60.0
        except Exception:
            pass
        time.sleep(1.0)          # be polite to the public routing server
    return out


def build(name: str = "Baião") -> np.ndarray:
    quarteis, poly = _stations(name)
    pts = _grid(poly)
    print(f"{len(quarteis)} quartéis | {len(pts)} pontos a encaminhar "
          f"({(len(pts) + LOTE - 1) // LOTE} pedidos)")

    mins = _route_minutes(quarteis, pts)
    ok = np.isfinite(mins)
    print(f"rotas obtidas: {ok.sum()} de {len(pts)}")
    pts_ok = np.array(pts)[ok]
    mins_ok = mins[ok]

    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    lon, lat = f["lon"], f["lat"]
    mid = float(lat.mean())
    kx, ky = 111_320.0 * np.cos(np.radians(mid)), 111_320.0

    tree = cKDTree(np.column_stack([pts_ok[:, 0] * kx, pts_ok[:, 1] * ky]))
    d, i = tree.query(np.column_stack([lon * kx, lat * ky]), k=4)
    w = 1.0 / np.maximum(d, 1.0) ** 2          # inverse distance weighting
    tempo = (mins_ok[i] * w).sum(axis=1) / w.sum(axis=1)

    np.save(OUT_DIR / f"tempo_bombeiros_{name.lower()}.npy", tempo.astype(np.float32))
    print(f"tempo de viagem por célula: mediana {np.median(tempo):.0f} min | "
          f"p90 {np.percentile(tempo, 90):.0f} min | máx {tempo.max():.0f} min")
    return tempo


def load(name: str = "Baião") -> np.ndarray | None:
    p = OUT_DIR / f"tempo_bombeiros_{name.lower()}.npy"
    return np.load(p) if p.exists() else None


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "Baião"
    tempo = build(name)

    # validate on random cells never used to build the surface
    quarteis, poly = _stations(name)
    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    rng = np.random.default_rng(99)
    idx = rng.choice(len(f["lon"]), 60, replace=False)
    dest = [(float(f["lon"][i]), float(f["lat"][i])) for i in idx]
    real = _route_minutes(quarteis, dest)
    est = tempo[idx]
    antigo = f["dist_bombeiros_m"][idx] * 1.4 / 50_000 * 60
    m = np.isfinite(real)
    print(f"\nvalidação em {m.sum()} células não usadas na construção:")
    print(f"  fórmula antiga: erro típico {np.median(np.abs(antigo[m] - real[m])):.1f} min")
    print(f"  superfície nova: erro típico {np.median(np.abs(est[m] - real[m])):.1f} min")
