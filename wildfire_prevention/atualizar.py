"""Scheduled refresh of everything that changes over time.

Run weekly (see `make cron`). Each step is independent and failure-tolerant:
one source being down must not stop the others, and the app keeps serving the
last good data.

  ignicoes  : civil-protection occurrences (near real time)
  fogos     : ICNF burnt-area perimeters (a new year appears after each season)
  secura    : Sentinel-2 composite for the current month + the two before it
  export    : rebuild the GeoJSON the app reads

The model itself is NOT retrained here — its inputs (fire history, COS) move on
a yearly cadence. Use `make retreinar` after a season closes.
"""

from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime

from . import export_web, occurrences, seca_history


def _step(nome: str, fn, *args, **kwargs) -> bool:
    t0 = time.time()
    print(f"\n=== {nome} ===", flush=True)
    try:
        fn(*args, **kwargs)
        print(f"--- {nome}: OK ({time.time() - t0:.0f}s)", flush=True)
        return True
    except Exception:
        print(f"--- {nome}: FALHOU ({time.time() - t0:.0f}s)", flush=True)
        traceback.print_exc()
        return False


def main(name: str = "Baião") -> int:
    print(f"Atualização de {name} — {datetime.now():%Y-%m-%d %H:%M}", flush=True)
    ok = {
        "ignições": _step("ignições (Proteção Civil)", occurrences.export, name),
        "fogos": _step("áreas ardidas (ICNF)", export_web.export_fires, name),
        "secura": _step("secura (Sentinel-2)", seca_history.build, name, refresh_last=True),
    }
    # the export re-reads whatever the steps above produced, so run it regardless
    ok["mapa"] = _step("exportar dados do mapa", export_web.export, name)
    _step("camadas de contexto", export_web.export_context, name)

    falhas = [k for k, v in ok.items() if not v]
    print("\n" + "=" * 46)
    print("Atualização concluída" if not falhas else f"Concluída COM FALHAS: {', '.join(falhas)}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "Baião"))
