"""Monthly NDVI/NDMI archive for the fire season, 2015-present.

Builds one composite per (year, month) for May-October, so we can compare the
CURRENT month against the SAME month in past years — the only fair baseline for
a dryness anomaly (comparing July to a Jun-Sep composite would be biased, since
August/September are systematically drier).

Resumable: each (year, month) is saved as it completes; re-running skips what is
already there. Band files stay cached on disk and are reused across months.
"""

from __future__ import annotations

import calendar
import json
from pathlib import Path

import numpy as np

from . import sentinel_bands

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"
YEARS = range(2015, 2026)
MONTHS = [5, 6, 7, 8, 9, 10]
MAX_SCENES = 3


def _path(name: str) -> Path:
    return OUT_DIR / f"monthly_archive_{name.lower()}.npz"


def build(name: str = "Baião") -> Path:
    path = _path(name)
    data = dict(np.load(path)) if path.exists() else {}
    meta_path = OUT_DIR / f"monthly_archive_{name.lower()}.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"entries": {}}

    todo = [(y, m) for y in YEARS for m in MONTHS if f"ndmi_{y}-{m:02d}" not in data]
    print(f"a construir {len(todo)} composições em falta (de {len(YEARS) * len(MONTHS)})\n")

    for i, (year, month) in enumerate(todo, 1):
        key = f"{year}-{month:02d}"
        last_day = calendar.monthrange(year, month)[1]
        window = (f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day}")
        try:
            _, scenes = sentinel_bands.clear_scenes(
                max_scenes=MAX_SCENES, year=year, window=window, max_cloud=25.0
            )
            if not scenes:
                print(f"[{i}/{len(todo)}] {key}: sem imagens utilizáveis — a saltar")
                meta["entries"][key] = {"imagens": [], "nota": "sem imagens"}
                meta_path.write_text(json.dumps(meta, ensure_ascii=False))
                continue
            ndvi, ndmi = sentinel_bands.composite_for_year(
                name, year, max_scenes=MAX_SCENES, window=window
            )
        except Exception as exc:
            print(f"[{i}/{len(todo)}] {key}: ERRO ({exc}) — a saltar")
            continue

        data[f"ndvi_{key}"] = ndvi.astype(np.float32)
        data[f"ndmi_{key}"] = ndmi.astype(np.float32)
        np.savez_compressed(path, **data)
        meta["entries"][key] = {
            "imagens": [{"data": s[3], "nuvem": round(s[0], 1)} for s in scenes]
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False))
        print(f"[{i}/{len(todo)}] {key}: NDMI mediana {np.nanmedian(ndmi):.3f} "
              f"({len(scenes)} imagens) -> guardado")

    print(f"\nArquivo mensal: {path}")
    return path


if __name__ == "__main__":
    import sys

    build(sys.argv[1] if len(sys.argv) > 1 else "Baião")
