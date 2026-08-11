"""Turn susceptibility into a PREVENTION-PRIORITY product for a municipality.

Prevention priority is not "where fire is likely" — it is "where acting reduces the
most risk to what we care about":

    priority = susceptibility  x  consequence

  susceptibility : the model's probability that the cell burns (LightGBM, all data)
  consequence    : exposure of values at risk — how close the cell is to buildings
                   (a fire far from people matters less than one beside a village)

We then aggregate cells into ~500 m intervention ZONES and rank them, so the output
is the sheet a municipal technician acts on: "treat here first, then here, and here
is why". Each zone carries its transparent factor profile — no black box.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from lightgbm import LGBMClassifier

from . import baseline, cos, panel_model

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"
ZONE_M = 500.0  # intervention zone size (fuel-management scale)


def _exposure(houses_500m: np.ndarray) -> np.ndarray:
    """Exposed value, 0.3-1.0. The forest itself carries a base value (0.3 —
    timber, habitat), and the housing CLUSTER size scales it up. The scale pivot
    adapts to the municipality's own cluster distribution (p99), so it keeps
    discriminating both in dispersed and in concentrated settlement patterns."""
    pivot = max(50.0, float(np.percentile(houses_500m, 99)))
    cluster = np.minimum(1.0, np.log1p(houses_500m) / np.log1p(pivot))
    return 0.3 + 0.7 * cluster


def response_minutes(dist_bombeiros_m: np.ndarray, name: str = "Baião") -> np.ndarray:
    """Travel time from the nearest fire station, in minutes.

    Uses the routed surface (real road network) when available. The old
    straight-line proxy was checked against real routing and came out at roughly
    HALF the true time: mountain roads wind, and a cell across a valley is far by
    road even when close as the crow flies. Measured on held-out cells, the
    proxy was off by ~4.3 min typically, the routed surface by ~0.6 min.

    The fallback keeps the tool working without the routed surface, but it is
    calibrated to local routing (19 km/h effective in straight-line terms)
    instead of the original optimistic 50 km/h.
    """
    from . import tempo_resposta

    real = tempo_resposta.load(name)
    if real is not None and real.size == dist_bombeiros_m.size:
        return real
    return dist_bombeiros_m / 1000.0 * 3.10


def _difficulty(dist_water_m, dist_road_m, dist_bombeiros_m) -> np.ndarray:
    """Suppression difficulty multiplier, 1.0-2.0. Far from water (tanker
    round-trips), far from roads (no access) and far from the fire brigade
    (the fire grows before anyone arrives) all weaken firefighting —
    prevention is worth MORE where suppression can't save the day."""
    water = np.minimum(dist_water_m, 3000.0) / 3000.0
    road = np.minimum(dist_road_m, 1000.0) / 1000.0
    # 25 min caps the scale: beyond that, help is late everywhere alike
    resp = np.minimum(response_minutes(dist_bombeiros_m), 25.0) / 25.0
    return 1.0 + 0.5 * water + 0.15 * road + 0.35 * resp


def build(name: str) -> dict:
    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    lon, lat = f["lon"], f["lat"]

    # STRUCTURAL propensity (multi-year), NOT next-single-year probability. A
    # next-year model correctly learns fuel depletion (just-burned areas won't
    # re-burn) and so AVOIDS the recurrent-fire zones a prevention plan must
    # target — measured: it anti-correlates with historical burn frequency.
    # This uses the multi-year window target, which aligns with reality (rho~0.5).
    print("Scoring structural fire propensity...")
    susceptibility = panel_model.structural_susceptibility(name)
    exposure = _exposure(f["houses_250m"])  # 250 m: aligned with legal fuel-management bands
    difficulty = _difficulty(f["dist_water_m"], f["dist_road_m"], f["dist_bombeiros_m"])
    consequence = exposure * difficulty
    priority = susceptibility * consequence
    # percentile rank (0-100): prevention priority is RELATIVE to the municipality
    # and its yearly budget — "top 5%" is what a câmara can actually treat.
    priority_pct = priority.argsort().argsort() / (len(priority) - 1) * 100.0

    # aggregate into ~500 m zones
    mid_lat = float(lat.mean())
    dlat = ZONE_M / 111_320.0
    dlon = ZONE_M / (111_320.0 * np.cos(np.radians(mid_lat)))
    zx = np.floor((lon - lon.min()) / dlon).astype(int)
    zy = np.floor((lat - lat.min()) / dlat).astype(int)
    zone_id = zx * 100000 + zy

    zones = _rank_zones(zone_id, priority, susceptibility, consequence, f, lon, lat)
    return {
        "name": name, "lon": lon, "lat": lat,
        "susceptibility": susceptibility, "consequence": consequence, "priority": priority,
        "priority_pct": priority_pct, "exposure": exposure, "difficulty": difficulty,
        "zones": zones,
    }


def _rank_zones(zone_id, priority, susc, cons, f, lon, lat, min_cells=25, top=10):
    uniq = np.unique(zone_id)
    rows = []
    for z in uniq:
        m = zone_id == z
        if m.sum() < min_cells:
            continue
        fuel_mode = int(np.bincount(f["fuel_code"][m].astype(int)).argmax())
        rows.append({
            "priority": float(priority[m].mean()),
            "susceptibility": float(susc[m].mean()),
            "consequence": float(cons[m].mean()),
            "lon": float(lon[m].mean()),
            "lat": float(lat[m].mean()),
            "n_cells": int(m.sum()),
            "area_ha": round(m.sum() * (29 * 29) / 1e4, 1),
            "mean_slope": round(float(f["slope"][m].mean()), 1),
            "dist_building_m": round(float(f["dist_building_m"][m].mean()), 0),
            "dominant_fuel": cos.FUEL_CLASSES.get(fuel_mode, "?"),
            "pct_burned_before": round(float((f["n_years_burned"][m] > 0).mean()) * 100, 0),
            "houses_500m": int(f["houses_500m"][m].max()),
            "dist_water_m": round(float(f["dist_water_m"][m].mean()), 0),
            "dist_road_m": round(float(f["dist_road_m"][m].mean()), 0),
        })
    rows.sort(key=lambda r: -r["priority"])
    return rows[:top]


def render(result: dict) -> Path:
    lon, lat = result["lon"], result["lat"]
    fig, axes = plt.subplots(1, 2, figsize=(16, 8), constrained_layout=True)
    aspect = 1.0 / np.cos(np.radians(lat.mean()))

    s0 = axes[0].scatter(lon, lat, c=result["susceptibility"], s=2, cmap="inferno", vmin=0, vmax=1)
    axes[0].set_title(f"Fire susceptibility — {result['name']}\n(where fire is likely)")
    fig.colorbar(s0, ax=axes[0], shrink=0.7)

    s1 = axes[1].scatter(lon, lat, c=result["priority"], s=2, cmap="viridis")
    axes[1].set_title(f"Prevention PRIORITY — {result['name']}\n(risk x consequence; where to act first)")
    fig.colorbar(s1, ax=axes[1], shrink=0.7)
    for i, z in enumerate(result["zones"], 1):
        axes[1].annotate(str(i), (z["lon"], z["lat"]), color="white", fontsize=11,
                         fontweight="bold", ha="center", va="center",
                         bbox=dict(boxstyle="circle,pad=0.15", fc="#c0392b", ec="white", lw=1))
    for ax in axes:
        ax.set_aspect(aspect)
        ax.set_xlabel("lon"); ax.set_ylabel("lat")

    out = OUT_DIR / f"priority_{result['name'].lower()}.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def print_zones(result: dict) -> None:
    print(f"\n=== Top prevention-priority zones — {result['name']} ===\n")
    print(f"{'#':<3}{'priority':>9}{'susc':>7}{'cons':>7}{'area ha':>9}{'slope':>7}{'~dist casa':>11}  fuel / history")
    print("-" * 95)
    for i, z in enumerate(result["zones"], 1):
        print(f"{i:<3}{z['priority']:>9.3f}{z['susceptibility']:>7.2f}{z['consequence']:>7.2f}"
              f"{z['area_ha']:>9.0f}{z['mean_slope']:>6.0f}°{z['dist_building_m']:>9.0f}m  "
              f"{z['dominant_fuel']}, {z['pct_burned_before']:.0f}% já ardeu")


def save(result: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"priority_zones_{result['name'].lower()}.json"
    out.write_text(json.dumps(result["zones"], indent=2, ensure_ascii=False))
    return out


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "Baião"
    result = build(name)
    print_zones(result)
    img = render(result)
    js = save(result)
    print(f"\nSaved: {img}\nSaved: {js}")
