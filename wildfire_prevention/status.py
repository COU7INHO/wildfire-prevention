"""What is built, and how old is it — a one-glance health check.

Every source in this project moves at its own pace (terrain never, land cover in
years, fires yearly, satellite weekly). This prints what exists and when it was
last refreshed, so nobody has to guess whether the map on screen is current.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "out"
WEB = ROOT / "webapp" / "public" / "data"


def _age(p: Path) -> str:
    if not p.exists():
        return "em falta"
    days = (datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)).days
    if days == 0:
        return "hoje"
    if days == 1:
        return "ontem"
    return f"há {days} dias"


def main(name: str = "Baião") -> None:
    slug = name.lower()
    web = slug.replace("ã", "a")
    print(f"\n=== Estado do projeto — {name} ===\n")

    print("DADOS BASE")
    for label, p in [
        ("grelha de células (terreno, COS, histórico)", OUT / f"features_{slug}.npz"),
        ("painel de vegetação por ano", OUT / f"veg_panel_{slug}.npz"),
        ("arquivo mensal (base da anomaly)", OUT / f"monthly_archive_{slug}.npz"),
        ("secura dos meses recentes", OUT / f"dryness_history_{slug}.npz"),
    ]:
        print(f"  {'✓' if p.exists() else '✗'} {label:<44} {_age(p)}")

    print("\nMODELO EM PRODUÇÃO")
    mp = OUT / f"model_{slug}.txt"
    mm = OUT / f"model_{slug}.json"
    if mp.exists() and mm.exists():
        m = json.loads(mm.read_text())
        print(f"  ✓ treinado em {m['treinado_em'][:16].replace('T', ' ')}  ({_age(mp)})")
        print(f"    anos {m['anos_alvo'][0]}-{m['anos_alvo'][1]} · "
              f"{m['n_linhas']:,} linhas · {len(m['features'])} variáveis")
    else:
        print("  ✗ sem modelo guardado — será treinado na próxima exportação")

    print("\nDADOS QUE A APLICAÇÃO LÊ")
    for label, p in [
        ("prioridade e risco", WEB / f"{web}_priority.geojson"),
        ("ignições do ano (Proteção Civil)", WEB / f"{web}_ocorrencias.geojson"),
        ("áreas ardidas por ano (ICNF)", WEB / f"{web}_fogos.geojson"),
        ("casas / estradas / água", WEB / f"{web}_casas.geojson"),
        ("comparação com o plano oficial", WEB / f"{web}_comparacao.json"),
    ]:
        print(f"  {'✓' if p.exists() else '✗'} {label:<44} {_age(p)}")

    # content-level freshness, which matters more than file dates
    print("\nATUALIDADE DO CONTEÚDO")
    pri = WEB / f"{web}_priority.geojson"
    if pri.exists():
        props = json.loads(pri.read_text()).get("properties", {})
        if props.get("sentinel_date"):
            print(f"  imagem de satélite mais recente usada        {props['sentinel_date']}")
        serie = props.get("seca_series") or []
        if serie:
            print(f"  meses de secura mostrados                    {', '.join(s['mes'] for s in serie)}")
    occ = WEB / f"{web}_ocorrencias.geojson"
    if occ.exists():
        d = json.loads(occ.read_text())
        p = d.get("properties", {})
        print(f"  ignições: {len(d.get('features', []))} de {p.get('year','?')}, "
              f"obtidas em {p.get('fetched','?')}")
    fog = WEB / f"{web}_fogos.geojson"
    if fog.exists():
        years = {f["properties"]["ano"] for f in json.loads(fog.read_text())["features"]}
        print(f"  áreas ardidas: {min(years)}-{max(years)}")
    print()


if __name__ == "__main__":
    import sys

    main(sys.argv[1] if len(sys.argv) > 1 else "Baião")
