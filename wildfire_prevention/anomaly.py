"""Dryness ANOMALY: this month compared with the SAME month in past years.

"69/100 in July" means nothing on its own — July is always dry. What a decision
maker needs is: is this July drier than the July we usually get here?

Baseline: the median NDMI of the same calendar month across 2015-2025 (from the
monthly archive), per cell. Comparing month-to-same-month is the only fair
baseline; comparing July against a Jun-Sep composite would be biased, since
August and September are systematically drier.

anomaly = current NDMI - historical median NDMI for that month
  negative -> drier than usual        positive -> wetter than usual
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"


def _load(name: str):
    arch = np.load(OUT_DIR / f"monthly_archive_{name.lower()}.npz")
    hist = np.load(OUT_DIR / f"dryness_history_{name.lower()}.npz")
    return arch, hist


def baseline_for_month(name: str, month: int) -> tuple[np.ndarray, list[int]]:
    """Per-cell median NDMI for that calendar month across past years."""
    arch, _ = _load(name)
    keys = [k for k in arch.files if k.startswith("ndmi_") and k.endswith(f"-{month:02d}")]
    years = sorted(int(k.split("_")[1][:4]) for k in keys)
    stack = np.vstack([arch[f"ndmi_{y}-{month:02d}"] for y in years])
    return np.nanmedian(stack, axis=0), years


def anomaly(name: str = "Baião", ym: str = "2026-07"):
    """Return (anomaly, current, baseline, years_used) for a given YYYY-MM."""
    _, hist = _load(name)
    key = f"ndmi_{ym}"
    if key not in hist.files:
        arch, _ = _load(name)
        if key not in arch.files:
            raise KeyError(f"sem composição para {ym}")
        current = arch[key]
    else:
        current = hist[key]

    month = int(ym[5:7])
    base, years = baseline_for_month(name, month)
    return current - base, current, base, years


def summary(name: str = "Baião", ym: str = "2026-07") -> dict:
    a, cur, base, years = anomaly(name, ym)
    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    veg = f["fuel_code"] != 1  # exclude urban/water
    av = a[veg]
    av = av[~np.isnan(av)]
    return {
        "mes": ym,
        "anos_referencia": years,
        "anomalia_media": round(float(av.mean()), 4),
        "pct_mais_seco_que_o_normal": round(float((av < 0).mean()) * 100),
        "pct_muito_mais_seco": round(float((av < -0.05).mean()) * 100),
        "ndmi_atual": round(float(np.nanmedian(cur[veg])), 3),
        "ndmi_normal": round(float(np.nanmedian(base[veg])), 3),
    }


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "Baião"
    for ym in ("2026-05", "2026-06", "2026-07"):
        try:
            s = summary(name, ym)
        except KeyError as exc:
            print(f"{ym}: {exc}")
            continue
        sinal = "MAIS SECO" if s["anomalia_media"] < 0 else "menos seco"
        print(f"{ym}  atual {s['ndmi_atual']:.3f} vs normal {s['ndmi_normal']:.3f} "
              f"({len(s['anos_referencia'])} anos)  ->  {sinal} que o normal")
        print(f"         {s['pct_mais_seco_que_o_normal']}% do território acima do normal de secura "
              f"({s['pct_muito_mais_seco']}% muito acima)")
