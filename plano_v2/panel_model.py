"""Multi-year PANEL susceptibility model — era-appropriate vegetation per year.

Each cell appears once per target year 2016-2025. Features describe the world
BEFORE that year (strict temporal hygiene):

  vegetation : NDVI/NDMI from the PREVIOUS summer (year T-1), from the veg panel
  fuel type  : the COS epoch in force at T-1 (2015 / 2018 / 2023, never the future)
  history    : ICNF fire history up to T-1 (n_burns, years since last)
  fixed      : topography, buildings, water, roads, fire brigade

  LABEL      : did the cell burn in year T?  (ICNF)

Validation is TEMPORAL: train on target years <= 2022, test on 2023/2024/2025,
which the model has never seen. This answers the municipality's real question —
"does it predict every year, or did it just get lucky on 2024?" — that a single
train/test slice cannot.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from lightgbm import LGBMClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from . import baseline
from .boundary import municipality_polygon  # noqa: F401 (ensures boundary cache)

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"

FIXED = [
    "elevation", "slope", "northness", "eastness", "built_m2",
    "dist_building_m", "houses_250m", "dist_water_m", "dist_road_m", "dist_bombeiros_m",
]
TARGET_YEARS = range(2016, 2026)
TRAIN_UNTIL = 2022  # train on target years <= this; test the rest


def cos_epoch_for(veg_year: int) -> str:
    """Newest COS epoch that is NOT after the vegetation year (no future leak)."""
    if veg_year >= 2023:
        return "2023"
    if veg_year >= 2018:
        return "2018"
    return "2015"


# Combat/suppression proxies — belong in the priority's "difficulty" formula,
# NOT in the fire-occurrence probability (redundant with terrain remoteness).
PROD_DROP = ("dist_bombeiros_m", "dist_water_m")

FEAT_NAMES = FIXED + ["fuel_code", "ndvi", "ndmi", "n_burns_hist", "years_since_burn"]

# Tuned on fires 2020-2021 (validation), then measured once on 2022-2025:
# AUC 0.736 -> 0.771, beating the official 2021 hazard (0.760). Heavy
# regularization is the key: the panel repeats the same ~210k cells across 10
# years, so loose trees memorize cell identity instead of learning patterns.
PARAMS = dict(
    num_leaves=15,
    min_child_samples=2000,
    learning_rate=0.03,
    n_estimators=300,
    reg_lambda=0.0,
    colsample_bytree=0.6,
    # NOTE: subsample is inert without subsample_freq > 0 — LightGBM ignores it.
    # Leaving it as it was during tuning (so results stay reproducible) and NOT
    # enabling bagging, which would make the published map shift between runs.
    subsample=0.8,
    random_state=42,   # explicit: the same data must always give the same map
    verbosity=-1,
)


def _year_features(veg_year: int, panel, burn, fixed, n):
    """Feature matrix for one vegetation year (no label). Shared by training and
    production scoring so columns stay identical."""
    ndvi = np.nan_to_num(panel[f"ndvi_{veg_year}"], nan=0.0)
    ndmi = np.nan_to_num(panel[f"ndmi_{veg_year}"], nan=0.0)
    fuel = panel[f"fuel_{cos_epoch_for(veg_year)}"].astype(np.float32)

    hist_years = [yr for yr in burn if yr <= veg_year]
    n_burns = np.sum([burn[yr] for yr in hist_years], axis=0).astype(np.float32)
    last = np.zeros(n, dtype=np.float32)
    for yr in sorted(hist_years):
        last[burn[yr]] = yr
    years_since = np.where(last > 0, veg_year - last, 99).astype(np.float32)
    return np.column_stack([fixed, fuel, ndvi, ndmi, n_burns, years_since])


def _load(name):
    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    panel = np.load(OUT_DIR / f"veg_panel_{name.lower()}.npz")
    burn = baseline._burn_by_year(name, f["lon"], f["lat"])
    fixed = np.column_stack([f[c].astype(np.float32) for c in FIXED])
    return f, panel, burn, fixed, f["lon"].size


def build_panel(name: str):
    f, panel, burn, fixed, n = _load(name)
    Xs, ys, yrs = [], [], []
    for T in TARGET_YEARS:
        vy = T - 1
        if f"ndvi_{vy}" not in panel.files or T not in burn:
            continue
        Xs.append(_year_features(vy, panel, burn, fixed, n))
        ys.append(burn[T].astype(np.int8))
        yrs.append(np.full(n, T, dtype=np.int16))
    return np.vstack(Xs), np.concatenate(ys), np.concatenate(yrs), FEAT_NAMES


def production_susceptibility(name: str = "Baião", drop=PROD_DROP) -> np.ndarray:
    """Next-single-year burn probability. NOTE: not for the prevention map — a
    next-year model learns fuel depletion and avoids recurrent-fire zones. Kept
    for reference / operational (seasonal) use. Use structural_susceptibility()
    for the prevention product."""
    X, y, _, names = build_panel(name)
    keep = [i for i, nm in enumerate(names) if nm not in drop]
    clf = LGBMClassifier(**PARAMS)
    clf.fit(X[:, keep], y)
    f, panel, burn, fixed, n = _load(name)
    latest_vy = max(int(k.split("_")[1]) for k in panel.files if k.startswith("ndvi_"))
    Xscore = _year_features(latest_vy, panel, burn, fixed, n)
    return clf.predict_proba(Xscore[:, keep])[:, 1]


def fuel_load(ndvi: np.ndarray, ndmi: np.ndarray) -> np.ndarray:
    """Transparent fuel load from the CURRENT satellite image, 0-1.
    biomass amount (NDVI) x dryness (low NDMI). Recently-burned cells read low
    (bare/regrowing = little NDVI); long-unburned, dry cells read high. No ML —
    just this week's measured vegetation, so it can be recomputed on every new
    Sentinel pass."""
    biomass = np.clip((np.nan_to_num(ndvi) - 0.2) / 0.6, 0, 1)
    moisture = np.clip(np.nan_to_num(ndmi) / 0.4, 0, 1)
    return biomass * (1.0 - moisture)


def readiness(name: str = "Baião", veg_year: int | None = None) -> np.ndarray:
    """Seasonal READINESS = structural propensity x CURRENT fuel load (Sentinel).
    Transparent: the structural model says 'is this place fire-prone?', the latest
    satellite image says 'is there dry fuel here NOW?'. Recomputed on each new
    image — the readiness map breathes with the season."""
    structural = structural_susceptibility(name)
    panel = np.load(OUT_DIR / f"veg_panel_{name.lower()}.npz")
    if veg_year is None:
        veg_year = max(int(k.split("_")[1]) for k in panel.files if k.startswith("ndvi_"))
    load = fuel_load(panel[f"ndvi_{veg_year}"], panel[f"ndmi_{veg_year}"])
    return structural * load


def model_path(name: str) -> Path:
    return OUT_DIR / f"modelo_{name.lower()}.txt"


def model_meta_path(name: str) -> Path:
    return OUT_DIR / f"modelo_{name.lower()}.json"


def train(name: str = "Baião", drop=PROD_DROP) -> dict:
    """Train the production model and SAVE it, with a record of how it was made.

    A published map is a basis for spending public money, so it must be possible
    to answer later: which model produced the map we acted on, trained when, on
    what data? Without a saved artefact that question has no answer — the model
    would have been retrained since."""
    import json
    from datetime import datetime

    X, y, year, names = build_panel(name)
    keep = [i for i, nm in enumerate(names) if nm not in drop]
    feats = [names[i] for i in keep]

    clf = LGBMClassifier(**PARAMS)
    clf.fit(X[:, keep], y)
    clf.booster_.save_model(str(model_path(name)))

    meta = {
        "municipio": name,
        "treinado_em": datetime.now().isoformat(timespec="seconds"),
        "anos_alvo": [int(np.min(year)), int(np.max(year))],
        "n_linhas": int(len(y)),
        "n_celulas": int((year == year[0]).sum()),
        "features": feats,
        "parametros": {k: v for k, v in PARAMS.items() if k != "verbosity"},
    }
    model_meta_path(name).write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    print(f"modelo guardado: {model_path(name).name} "
          f"({meta['n_linhas']:,} linhas, anos {meta['anos_alvo'][0]}-{meta['anos_alvo'][1]})")
    return meta


def structural_susceptibility(name: str = "Baião", drop=PROD_DROP,
                              retrain: bool = False) -> np.ndarray:
    """STRUCTURAL fire propensity for the prevention map = each cell's MEAN annual
    burn probability across all panel years. Averaging over years cancels the
    single-year fuel-depletion effect (just-burned = low that year, recovered =
    high later), leaving the long-run 'how fire-prone is this place' signal that
    aligns with historical burn frequency.

    Uses the saved model when it matches the current features; trains and saves
    one otherwise. So the weekly refresh scores with a known, auditable model
    instead of quietly producing a new one."""
    import json

    import lightgbm as lgb

    X, y, year, names = build_panel(name)
    keep = [i for i, nm in enumerate(names) if nm not in drop]
    feats = [names[i] for i in keep]

    booster = None
    if not retrain and model_path(name).exists() and model_meta_path(name).exists():
        meta = json.loads(model_meta_path(name).read_text())
        if meta.get("features") == feats:          # stale if the inputs changed
            booster = lgb.Booster(model_file=str(model_path(name)))

    if booster is None:
        train(name, drop=drop)
        booster = lgb.Booster(model_file=str(model_path(name)))

    p = booster.predict(X[:, keep])
    n_cells = int((year == year[0]).sum())
    return p.reshape(-1, n_cells).mean(axis=0)


def _eval(X, y, year, names, drop=(), label=""):
    keep = [i for i, nm in enumerate(names) if nm not in drop]
    Xk = X[:, keep]
    tr = year <= TRAIN_UNTIL
    clf = LGBMClassifier(**PARAMS)
    clf.fit(Xk[tr], y[tr])
    aucs = {}
    for ty in sorted(np.unique(year[year > TRAIN_UNTIL])):
        m = year == ty
        aucs[ty] = roc_auc_score(y[m], clf.predict_proba(Xk[m])[:, 1])
    line = "  ".join(f"{ty}:{a:.3f}" for ty, a in aucs.items())
    print(f"  {label:<32} {line}   média {np.mean(list(aucs.values())):.3f}")
    return clf, keep


def run(name: str = "Baião"):
    X, y, year, names = build_panel(name)
    print(f"\n=== Panel model: {name} ===")
    print(f"rows: {len(y):,}  ({len(np.unique(year))} target years)\n")

    print("Ablação — AUC por ano de teste (2023 / 2024 / 2025):")
    clf_full, _ = _eval(X, y, year, names, drop=(), label="modelo completo")
    _eval(X, y, year, names, drop=("dist_bombeiros_m",), label="sem bombeiros")
    _eval(X, y, year, names, drop=("dist_bombeiros_m", "dist_water_m"), label="sem bombeiros + sem água")

    imp = sorted(zip(names, clf_full.feature_importances_), key=lambda kv: -kv[1])
    total = sum(v for _, v in imp) or 1
    print("\nFeature importance (modelo completo):")
    for nm, v in imp:
        print(f"  {nm:<18} {v/total*100:5.1f}%")


if __name__ == "__main__":
    import sys

    run(sys.argv[1] if len(sys.argv) > 1 else "Baião")
