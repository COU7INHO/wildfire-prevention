"""Render a validation map: predicted susceptibility vs where it actually burned.

Honest rendering: predictions are OUT-OF-FOLD (each cell scored by a model that
never saw its spatial block), so the map is not an overfit self-portrait. Side by
side with the real 2021-2025 burned cells, a person who knows the municipality can
eyeball whether the model's high-risk areas match reality.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from lightgbm import LGBMClassifier
from sklearn.model_selection import GroupKFold

from . import baseline

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"


def oof_predictions(X, y, groups):
    oof = np.zeros(len(y))
    for tr, te in GroupKFold(n_splits=5).split(X, y, groups):
        clf = LGBMClassifier(
            n_estimators=400, learning_rate=0.05, num_leaves=31,
            subsample=0.8, colsample_bytree=0.8, verbosity=-1,
        )
        clf.fit(X[tr], y[tr])
        oof[te] = clf.predict_proba(X[te])[:, 1]
    return oof


def render(name: str) -> Path:
    X, y, feat_names, groups = baseline.build_xy(name)
    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    lon, lat = f["lon"], f["lat"]

    print("Scoring cells (out-of-fold)...")
    risk = oof_predictions(X, y, groups)

    fig, axes = plt.subplots(1, 2, figsize=(15, 8), constrained_layout=True)
    aspect = 1.0 / np.cos(np.radians(lat.mean()))

    sc = axes[0].scatter(lon, lat, c=risk, s=2, cmap="inferno", vmin=0, vmax=1)
    axes[0].set_title(f"Predicted fire susceptibility — {name}\n(out-of-fold model score)")
    fig.colorbar(sc, ax=axes[0], shrink=0.7, label="probability")

    axes[1].scatter(lon, lat, c="#dddddd", s=2)
    burned = y == 1
    axes[1].scatter(lon[burned], lat[burned], c="#c0392b", s=2)
    yr0, yr1 = baseline.TEST_PERIOD.start, baseline.TEST_PERIOD.stop - 1
    axes[1].set_title(f"Actually burned {yr0}–{yr1} — {name}\n(ICNF, red = burned)")

    for ax in axes:
        ax.set_aspect(aspect)
        ax.set_xlabel("lon")
        ax.set_ylabel("lat")

    out = OUT_DIR / f"map_{name.lower()}.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "Baião"
    path = render(name)
    print(f"Saved: {path}")
