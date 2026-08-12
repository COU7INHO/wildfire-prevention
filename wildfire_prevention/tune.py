"""Honest hyperparameter search for the structural susceptibility model.

Three disjoint periods so the reported number is not tuned on the test set:
  train      : target years <= 2019
  validation : fires 2020-2021   (used to PICK the configuration)
  test       : fires 2022-2025   (touched once, at the end)

The final test period is the same one the official PMDFCI 2021 hazard was
measured on (AUC 0.760), so the comparison stays fair.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
from lightgbm import LGBMClassifier
from sklearn.metrics import roc_auc_score

from . import baseline, panel_model, official_plan

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"

GRID = {
    "num_leaves": [15, 31, 63],
    "min_child_samples": [20, 200, 2000],
    "learning_rate": [0.03, 0.05],
    "n_estimators": [300, 600],
    "reg_lambda": [0.0, 10.0],
    "colsample_bytree": [0.6, 0.8],
}


def _structural(clf, Xk, year, upto):
    """Mean predicted annual probability over the years the model was trained on."""
    n_cells = int((year == year[0]).sum())
    p = clf.predict_proba(Xk)[:, 1].reshape(-1, n_cells)
    blocks = year.reshape(-1, n_cells)[:, 0]
    return p[blocks <= upto].mean(axis=0)


def _burned_in(burn, years, n):
    t = np.zeros(n, bool)
    for yr in years:
        if yr in burn:
            t |= burn[yr]
    return t


def run(name: str = "Baião", max_configs: int = 48):
    X, y, year, names = panel_model.build_panel(name)
    keep = [i for i, n in enumerate(names) if n not in panel_model.PROD_DROP]
    Xk = X[:, keep]

    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    burn = baseline._burn_by_year(name, f["lon"], f["lat"])
    n = f["lon"].size
    oficial = official_plan.sample_cells(name)
    ok = ~np.isnan(oficial)

    val_target = _burned_in(burn, (2020, 2021), n)
    test_target = _burned_in(burn, (2022, 2023, 2024, 2025), n)

    keys = list(GRID)
    combos = list(itertools.product(*(GRID[k] for k in keys)))
    rng = np.random.default_rng(0)
    if len(combos) > max_configs:
        combos = [combos[i] for i in rng.choice(len(combos), max_configs, replace=False)]

    print(f"a testar {len(combos)} configurações (validação = fogos 2020-2021)\n")
    tr = year <= 2019
    results = []
    for i, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))
        clf = LGBMClassifier(subsample=0.8, verbosity=-1, n_jobs=-1, **params)
        clf.fit(Xk[tr], y[tr])
        s = _structural(clf, Xk, year, 2019)
        auc = roc_auc_score(val_target[ok], s[ok])
        results.append((auc, params))
        print(f"  [{i}/{len(combos)}] val AUC {auc:.4f}  {params}", flush=True)

    results.sort(key=lambda r: -r[0])
    best_auc, best = results[0]
    print(f"\nmelhor em validação: {best_auc:.4f}\n  {best}")

    # ---- final, single evaluation on the untouched test period ----
    tr2 = year <= 2021
    clf = LGBMClassifier(subsample=0.8, verbosity=-1, n_jobs=-1, **best)
    clf.fit(Xk[tr2], y[tr2])
    s_best = _structural(clf, Xk, year, 2021)

    base = LGBMClassifier(n_estimators=600, learning_rate=0.05, num_leaves=31,
                          subsample=0.8, colsample_bytree=0.8, verbosity=-1, n_jobs=-1)
    base.fit(Xk[tr2], y[tr2])
    s_base = _structural(base, Xk, year, 2021)

    print("\n=== TESTE FINAL (fogos 2022-2025, nunca usados na busca) ===")
    print(f"  Plano 2021 (oficial):        AUC {roc_auc_score(test_target[ok], oficial[ok]):.3f}")
    print(f"  Nosso — parâmetros atuais:   AUC {roc_auc_score(test_target[ok], s_base[ok]):.3f}")
    print(f"  Nosso — parâmetros afinados: AUC {roc_auc_score(test_target[ok], s_best[ok]):.3f}")

    (OUT_DIR / f"tune_{name.lower()}.json").write_text(
        json.dumps({"best_params": best, "val_auc": best_auc}, indent=2)
    )


if __name__ == "__main__":
    import sys

    run(sys.argv[1] if len(sys.argv) > 1 else "Baião")
