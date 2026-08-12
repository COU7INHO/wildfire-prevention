"""Monthly dryness history: NDMI composites for the last months of the season.

Builds one clean-scene NDMI composite per month, so the app can show HOW dryness
EVOLVED (map slider + trend line) instead of a single snapshot. Resumable: each
month is saved into dryness_history_<name>.npz as it completes.
"""

from __future__ import annotations

import calendar
import json
from datetime import date
from pathlib import Path

import numpy as np

from . import sentinel_bands

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"

MES_PT = {5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago", 9: "Set", 10: "Out"}
SEASON = range(5, 11)          # May-October: the months the archive baselines
N_MONTHS = 3                   # how many recent months the app shows


def recent_months(today: date | None = None, n: int = N_MONTHS):
    """The last n fire-season months up to today, as (label, (start, end))."""
    today = today or date.today()
    out = []
    y, m = today.year, today.month
    while len(out) < n and y >= 2015:
        if m in SEASON:
            last = calendar.monthrange(y, m)[1]
            out.append((MES_PT[m], (f"{y}-{m:02d}-01", f"{y}-{m:02d}-{last}")))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def build(name: str = "Baião", refresh_last: bool = False) -> Path:
    """Build the recent-months dryness series. refresh_last recomputes the most
    recent month even if cached — new cloud-free images arrive every few days."""
    path = OUT_DIR / f"dryness_history_{name.lower()}.npz"
    data = dict(np.load(path)) if path.exists() else {}
    meta_path = OUT_DIR / f"dryness_history_{name.lower()}.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"months": []}

    months = recent_months()
    if refresh_last and months:
        data.pop(f"ndmi_{months[-1][1][0][:7]}", None)   # force the current month

    # keep only the months we are showing now (the window slides with the season)
    keep = {f"ndmi_{w[0][:7]}" for _, w in months}
    data = {k: v for k, v in data.items() if k in keep}
    meta["months"] = [m for m in meta["months"] if m["key"] in keep]

    for label, window in months:
        key = f"ndmi_{window[0][:7]}"  # e.g. ndmi_2026-05
        cached = key in data

        # which images went into this month's composite (shown in the app)
        _, scenes = sentinel_bands.clear_scenes(
            max_scenes=3, year=int(window[0][:4]), window=window, max_cloud=20.0
        )
        dates = [{"data": s[3], "nuvem": round(s[0], 1)} for s in scenes]

        if not cached:
            _, ndmi = sentinel_bands.composite_for_year(
                name, int(window[0][:4]), max_scenes=3, window=window
            )
            data[key] = ndmi.astype(np.float32)
            np.savez_compressed(path, **data)
            print(f"[{label}] NDMI mediana {np.nanmedian(ndmi):.3f} -> guardado")
        else:
            print(f"[{label}] composto em cache ({len(dates)} imagens)")

        entry = {"label": label, "key": key, "window": list(window), "imagens": dates}
        meta["months"] = [m for m in meta["months"] if m["label"] != label] + [entry]
        meta["months"].sort(key=lambda m: m["window"][0])
        meta_path.write_text(json.dumps(meta, ensure_ascii=False))
    print(f"\nHistórico completo: {path}")
    return path


if __name__ == "__main__":
    import sys

    build(sys.argv[1] if len(sys.argv) > 1 else "Baião")
