"""Wait out a CDSE lockout, then continue building the monthly archive.

The account gets temporarily locked when too many sessions are opened. We poll
auth infrequently (every 10 min — polling hard is what caused the lock) and, as
soon as it clears, resume the archive, which skips whatever is already built.
"""

from __future__ import annotations

import time

from . import monthly_archive, sentinel

POLL_S = 600
MAX_WAIT_H = 8


def main(name: str = "Baião") -> None:
    deadline = time.time() + MAX_WAIT_H * 3600
    while time.time() < deadline:
        try:
            sentinel.get_token(force=True)
            print("CDSE desbloqueado — a retomar o arquivo mensal\n", flush=True)
            monthly_archive.build(name)
            return
        except Exception:
            mins = int((deadline - time.time()) / 60)
            print(f"ainda bloqueado; nova tentativa em {POLL_S // 60} min "
                  f"(desisto em {mins} min)", flush=True)
            time.sleep(POLL_S)
    print("desisti de esperar — corre de novo mais tarde", flush=True)


if __name__ == "__main__":
    import sys

    main(sys.argv[1] if len(sys.argv) > 1 else "Baião")
