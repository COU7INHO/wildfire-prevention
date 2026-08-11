"""Baseline fire-susceptibility model — does the signal exist?

Target framing (prevention, not operational nowcast):
  y = 1 if the cell burned in the HELD-OUT recent period (2021-2024)
  X = static conditions + fire history known through 2020 only

We deliberately do NOT predict one specific fire's footprint (that needs the day's
ignition + weather — the hard operational problem). We ask the prevention question:
"which cells are structurally prone to burn?", learned from prior conditions.

Validation is SPATIAL: cells are grouped into spatial blocks and we cross-validate
across blocks, so the score measures generalization to unseen ground — not
memorization of autocorrelated neighbours. Reporting ROC-AUC and PR-AUC (PR because
the positive rate matters) plus LightGBM gain importances.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import shapely
from lightgbm import LGBMClassifier
from shapely import STRtree
from shapely.geometry import shape
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GroupKFold

from . import icnf_labels
from .boundary import municipality_polygon

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"

# Temporal hygiene: features describe the world up to and including COS-2023 /
# fire history 2023, and we predict the FUTURE relative to that (2024-2025). This
# keeps the freshest data (COS 2023) while ensuring features predate the label —
# no leakage from post-fire land cover into the target.
HISTORY_CUTOFF = 2023          # features may use fire history up to and including this
TEST_PERIOD = range(2024, 2026)  # label = burned in any of these years (after the features)

STATIC_COLS = [
    "elevation", "slope", "northness", "eastness",
    "fuel_code", "flammable", "dist_building_m", "built_m2",
    "ndvi", "ndmi",  # Sentinel-2 summer-2023 vegetation state (load + dryness)
]


def _burn_by_year(name: str, lon: np.ndarray, lat: np.ndarray,
                  refresh: bool = False) -> dict[int, np.ndarray]:
    """Which cells burned, per year — cached.

    Rasterizing the ICNF perimeters costs 17 network calls on every export, and
    the fire history only changes once a year. Caching it makes the scheduled
    refresh fast and, more importantly, survivable when the ICNF service is
    down. The cache is invalidated when a new year is published or when the
    grid changes size.
    """
    cache = OUT_DIR / f"burn_{name.lower()}.npz"

    try:
        layers = icnf_labels.list_year_layers()
        anos = sorted(y for _, y in layers)
    except Exception:
        layers, anos = None, None          # ICNF unreachable: cache is all we have

    if cache.exists() and not refresh:
        d = np.load(cache)
        em_cache = sorted(int(k[1:]) for k in d.files if k.startswith("y"))
        if int(d["n_cells"]) == lon.size and (anos is None or em_cache == anos):
            return {int(k[1:]): d[k] for k in d.files if k.startswith("y")}

    if layers is None:
        raise RuntimeError("histórico ICNF indisponível e sem cache utilizável")

    poly, bbox = municipality_polygon(name)
    points = shapely.points(lon, lat)
    out: dict[int, np.ndarray] = {}
    for layer_id, year in layers:
        polys = [shape(f["geometry"]) for f in icnf_labels.fetch_year(layer_id, bbox) if f.get("geometry")]
        arr = np.zeros(lon.size, dtype=bool)
        if polys:
            hit = np.unique(STRtree(polys).query(points, predicate="intersects")[0])
            arr[hit] = True
        out[year] = arr

    np.savez_compressed(cache, n_cells=lon.size, **{f"y{y}": v for y, v in out.items()})
    return out


def build_xy(name: str):
    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    lon, lat = f["lon"], f["lat"]

    burn = _burn_by_year(name, lon, lat)
    hist_years = [y for y in burn if y <= HISTORY_CUTOFF]
    n_burns_hist = np.sum([burn[y] for y in hist_years], axis=0).astype(np.int16)

    last_burn = np.zeros(lon.size, dtype=np.int16)
    for y in sorted(hist_years):
        last_burn[burn[y]] = y
    years_since = np.where(last_burn > 0, HISTORY_CUTOFF - last_burn, 99).astype(np.int16)

    y = np.zeros(lon.size, dtype=np.int8)
    for yr in TEST_PERIOD:
        if yr in burn:
            y |= burn[yr]

    cols = {c: f[c] for c in STATIC_COLS}
    cols["n_burns_hist"] = n_burns_hist
    cols["years_since_burn"] = years_since
    feat_names = list(cols)
    X = np.column_stack([cols[c] for c in feat_names]).astype(np.float64)

    # spatial blocks (~6x6) for grouped CV
    bx = np.digitize(lon, np.linspace(lon.min(), lon.max(), 7))
    by = np.digitize(lat, np.linspace(lat.min(), lat.max(), 7))
    groups = bx * 10 + by
    return X, y.astype(int), feat_names, groups


def run(name: str):
    X, y, feat_names, groups = build_xy(name)
    pos = y.mean()
    print(f"\n=== Baseline susceptibility model: {name} ===\n")
    print(f"cells: {len(y):,}   positive (burned {TEST_PERIOD.start}-{TEST_PERIOD.stop-1}): {pos*100:.1f}%")
    print(f"features: {', '.join(feat_names)}\n")

    gkf = GroupKFold(n_splits=5)
    oof = np.zeros(len(y))
    aucs, aps = [], []
    for k, (tr, te) in enumerate(gkf.split(X, y, groups), 1):
        clf = LGBMClassifier(
            n_estimators=400, learning_rate=0.05, num_leaves=31,
            subsample=0.8, colsample_bytree=0.8, verbosity=-1,
        )
        clf.fit(X[tr], y[tr])
        p = clf.predict_proba(X[te])[:, 1]
        oof[te] = p
        aucs.append(roc_auc_score(y[te], p))
        aps.append(average_precision_score(y[te], p))
        print(f"  fold {k}: ROC-AUC {aucs[-1]:.3f}   PR-AUC {aps[-1]:.3f}   (test cells {len(te):,})")

    print(f"\nSpatial CV  ROC-AUC: {np.mean(aucs):.3f} ± {np.std(aucs):.3f}")
    print(f"Spatial CV  PR-AUC : {np.mean(aps):.3f} ± {np.std(aps):.3f}   (baseline = {pos:.3f})")
    lift = np.mean(aps) / pos
    print(f"PR-AUC lift over random: {lift:.1f}x")

    # importances from a full fit
    clf = LGBMClassifier(n_estimators=400, learning_rate=0.05, num_leaves=31, verbosity=-1)
    clf.fit(X, y)
    imp = sorted(zip(feat_names, clf.feature_importances_), key=lambda kv: -kv[1])
    print("\nFeature importance (LightGBM gain):")
    total = sum(v for _, v in imp) or 1
    for name_, v in imp:
        print(f"  {name_:<18} {v/total*100:5.1f}%")

    return {"roc_auc": float(np.mean(aucs)), "pr_auc": float(np.mean(aps)), "pos_rate": float(pos)}


if __name__ == "__main__":
    import sys

    run(sys.argv[1] if len(sys.argv) > 1 else "Baião")
