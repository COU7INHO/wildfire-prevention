"""Official PMDFCI 2021 hazard surface, sampled onto our grid.

The plan's geographic package ships the hazard ("perigosidade") raster used to
produce Mapa n.19 — georeferenced at 10 m (EPSG:3763), not a screenshot. We
sample it per cell so it can be shown as a real overlay AND compared numerically
with our own susceptibility.

The raster is a continuous product of factors (69 distinct values, 4..720), not
1-5 classes, so we compare RANKS (percentile within the municipality), which is
invariant to the exact scaling both sides use.

Source: PMDFCI_1302_Info_Geografica.zip -> Perigosidade/1302bpif.tif
(Câmara Municipal de Baião / ICNF, plan in force 2021-2030).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"
TIF = Path(__file__).resolve().parent.parent / "data" / "cache" / "pmdfci_1302" / "Perigosidade" / "1302bpif.tif"


def sample_cells(name: str = "Baião") -> np.ndarray:
    """Official hazard value per grid cell (NaN where the plan has no data)."""
    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    lon, lat = f["lon"], f["lat"]

    with rasterio.open(TIF) as src:
        to_ras = Transformer.from_crs(4326, src.crs, always_xy=True)
        xs, ys = to_ras.transform(lon, lat)
        t = src.transform
        cols = ((np.asarray(xs) - t.c) / t.a).astype(int)
        rows = ((np.asarray(ys) - t.f) / t.e).astype(int)
        inside = (rows >= 0) & (rows < src.height) & (cols >= 0) & (cols < src.width)
        band = src.read(1)

    vals = np.full(lon.size, np.nan)
    vals[inside] = band[rows[inside], cols[inside]]
    vals[vals <= 0] = np.nan  # 0 = nodata / excluded by the plan
    return vals


def head_to_head(name: str = "Baião", plan_year: int = 2021) -> dict:
    """Fair comparison: our model trained ONLY on data the plan also had
    (<= plan_year), both scored against the fires that came after.

    Written to data/out/comparacao_<name>.json for the app to display."""
    import json

    from lightgbm import LGBMClassifier
    from sklearn.metrics import roc_auc_score

    from . import baseline, panel_model

    X, y, year, names = panel_model.build_panel(name)
    keep = [i for i, n in enumerate(names) if n not in panel_model.PROD_DROP]
    Xk = X[:, keep]
    tr = year <= plan_year
    clf = LGBMClassifier(**panel_model.PARAMS)
    clf.fit(Xk[tr], y[tr])

    n_cells = int((year == year[0]).sum())
    blocks = year.reshape(-1, n_cells)[:, 0]
    ours = clf.predict_proba(Xk)[:, 1].reshape(-1, n_cells)[blocks <= plan_year].mean(axis=0)

    official = sample_cells(name)
    ok = ~np.isnan(official)
    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    burn = baseline._burn_by_year(name, f["lon"], f["lat"])

    anos, nosso_w, plano_w = [], 0, 0
    for yr in sorted(y_ for y_ in burn if y_ > plan_year):
        t = burn[yr][ok]
        if t.sum() < 200:      # too few burned cells to score meaningfully
            continue
        a_of = float(roc_auc_score(t, official[ok]))
        a_no = float(roc_auc_score(t, ours[ok]))
        nosso_w += a_no > a_of
        plano_w += a_of > a_no
        anos.append({"ano": int(yr), "ardeu_pct": round(float(t.mean()) * 100, 1),
                     "plano": round(a_of, 3), "nosso": round(a_no, 3)})

    # Is the gap real, or noise? Spatial block bootstrap: resample BLOCKS (not
    # cells, which are autocorrelated) and look at the spread of the difference.
    lon, lat = f["lon"][ok], f["lat"][ok]
    bx = np.digitize(lon, np.linspace(lon.min(), lon.max(), 7))
    by = np.digitize(lat, np.linspace(lat.min(), lat.max(), 7))
    blk = bx * 10 + by
    ublk = np.unique(blk)
    idx_by_blk = {b: np.where(blk == b)[0] for b in ublk}
    rng = np.random.default_rng(0)
    o_, s_ = official[ok], ours[ok]

    per_year_difs = []
    for yr in [a["ano"] for a in anos]:
        t = burn[yr][ok]
        d = []
        for _ in range(300):
            sel = np.concatenate([idx_by_blk[b] for b in rng.choice(ublk, len(ublk), replace=True)])
            tt = t[sel]
            if tt.sum() < 50 or tt.sum() == len(tt):
                continue
            d.append(roc_auc_score(tt, o_[sel]) - roc_auc_score(tt, s_[sel]))
        per_year_difs.append(np.array(d))

    k = min(len(d) for d in per_year_difs)
    mean_dif = np.mean(np.vstack([d[:k] for d in per_year_difs]), axis=0)
    lo, hi = (float(v) for v in np.percentile(mean_dif, [2.5, 97.5]))

    total = np.zeros(f["lon"].size, bool)
    for yr in (y_ for y_ in burn if y_ > plan_year):
        total |= burn[yr]
    out = {
        "plan_year": plan_year,
        "anos": anos,
        # two honest summaries, because they answer different questions:
        # "media" = average of the per-year scores (predicting a GIVEN year)
        # "global" = one pooled score for "burned at any point in the period"
        #            (a multi-year horizon, dominated by the big-fire year)
        "media": {"plano": round(float(np.mean([a["plano"] for a in anos])), 3),
                  "nosso": round(float(np.mean([a["nosso"] for a in anos])), 3)},
        "global": {"plano": round(float(roc_auc_score(total[ok], official[ok])), 3),
                   "nosso": round(float(roc_auc_score(total[ok], ours[ok])), 3)},
        "vitorias": {"nosso": nosso_w, "plano": plano_w},
        "incerteza": {
            "dif_media": round(float(mean_dif.mean()), 3),   # plano - nosso
            "ic95": [round(lo, 3), round(hi, 3)],
            "significativo": bool(lo > 0 or hi < 0),
            "prob_plano_melhor": round(float((mean_dif > 0).mean()) * 100),
        },
    }
    (OUT_DIR / f"comparacao_{name.lower()}.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    from scipy.stats import spearmanr

    from . import panel_model

    v = sample_cells("Baião")
    ours = panel_model.structural_susceptibility("Baião")
    ok = ~np.isnan(v)
    print(f"células com perigosidade oficial: {ok.sum():,} de {len(v):,} ({ok.mean()*100:.0f}%)")
    print(f"concordância de ranking (Spearman) plano 2021 vs nosso modelo: "
          f"{spearmanr(v[ok], ours[ok]).correlation:+.2f}")

    # who tracks reality better? compare both against actual burn frequency
    f = np.load(OUT_DIR / "features_baião.npz")
    truth = f["n_years_burned"].astype(float)[ok]
    print(f"\nalinhamento com a frequência real de fogo (2009-2025):")
    print(f"  plano 2021 (perigosidade oficial): {spearmanr(v[ok], truth).correlation:+.2f}")
    print(f"  nosso modelo:                      {spearmanr(ours[ok], truth).correlation:+.2f}")
