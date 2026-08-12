"""Export the priority product as GeoJSON for the web app.

We aggregate the ~29 m cells into ~200 m squares (browser-friendly: a few thousand
features, each clickable) and emit one GeoJSON polygon per square with the priority,
plus the plain-language factors a technician cares about — NO ML jargon in the output.
The web app just renders this; all modelling stays in Python.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import cos, priority

WEB_DIR = Path(__file__).resolve().parent.parent / "webapp" / "public" / "data"
SQUARE_M = 200.0


def _fuel_label(code: int) -> str:
    return cos.FUEL_CLASSES.get(int(code), "Desconhecido")


# Plain-language names for the model inputs, grouped the way a technician thinks
# about them. Anything not listed here still shows up, by its raw name — better
# an ugly label than a silently hidden variable.
VARIAVEIS = {
    "elevation": ("Terreno", "Altitude"),
    "slope": ("Terreno", "Declive"),
    "northness": ("Terreno", "Orientação da encosta (norte–sul)"),
    "eastness": ("Terreno", "Orientação da encosta (este–oeste)"),
    "fuel_code": ("Vegetação", "Tipo de vegetação e espécie"),
    "ndvi": ("Vegetação", "Vigor da vegetação, medido por satélite"),
    "ndmi": ("Vegetação", "Humidade da vegetação, medida por satélite"),
    "dist_building_m": ("Presença humana", "Distância à habitação mais próxima"),
    "houses_250m": ("Presença humana", "Habitações num raio de 250 m"),
    "built_m2": ("Presença humana", "Área construída na célula"),
    "dist_road_m": ("Presença humana", "Distância a estradas e caminhos"),
    "n_burns_hist": ("Histórico", "Número de vezes que já ardeu"),
    "years_since_burn": ("Histórico", "Anos desde o último incêndio"),
}


_ROTULO_EXCLUIDA = {
    "dist_bombeiros_m": "A distância aos quartéis de bombeiros",
    "dist_water_m": "a distância a pontos de água",
}


def _model_meta(name: str) -> dict | None:
    """How the model in production was actually built — read from the artefact
    itself, so this documentation cannot drift from what is running."""
    from . import panel_model

    meta_path = panel_model.model_meta_path(name)
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())

    grupos: dict[str, list[str]] = {}
    for f in meta.get("features", []):
        grupo, rotulo = VARIAVEIS.get(f, ("Outras", f))
        grupos.setdefault(grupo, []).append(rotulo)

    return {
        "treinado_em": meta.get("treinado_em", "")[:10],
        "anos": meta.get("anos_alvo"),
        "n_linhas": meta.get("n_linhas"),
        "n_celulas": meta.get("n_celulas"),
        "variaveis": [{"grupo": g, "itens": v} for g, v in grupos.items()],
        "n_variaveis": len(meta.get("features", [])),
        # kept out of the risk on purpose — they belong to the response side,
        # not to how likely a place is to burn (measured: redundant proxies).
        # Derived from the model's own drop list so the text cannot go stale.
        "excluidas": [_ROTULO_EXCLUIDA.get(f, f) for f in panel_model.PROD_DROP],
    }


def _sources(name: str, sentinel_date: str | None) -> list[dict]:
    """Provenance of every layer, built from the data actually on disk so the
    panel in the app cannot drift from what is really being used."""
    from . import icnf_labels

    slug = name.lower().replace("ã", "a")
    years = [y for _, y in icnf_labels.list_year_layers()]
    occ_path = WEB_DIR / f"{slug}_ignitions.geojson"
    occ = json.loads(occ_path.read_text()).get("properties", {}) if occ_path.exists() else {}

    return [
        {"o_que": "Áreas ardidas e histórico de fogo",
         "quem": "ICNF — Instituto da Conservação da Natureza e das Florestas",
         "quando": f"{min(years)}–{max(years)}" if years else "—"},
        {"o_que": "Ignições do ano em curso",
         "quem": "Proteção Civil (ANEPC), via fogos.pt",
         "quando": f"{occ.get('year', '')}, obtidas a {occ.get('fetched', '—')}"},
        {"o_que": "Estado da vegetação (vigor e secura)",
         "quem": "Sentinel-2, programa Copernicus (ESA)",
         "quando": f"imagem de {sentinel_date}" if sentinel_date else "—"},
        {"o_que": "Ocupação do solo e espécies florestais",
         "quem": "Carta de Uso e Ocupação do Solo (COS), DGT",
         "quando": "2023"},
        {"o_que": "Terreno: declive e exposição",
         "quem": "Modelo digital de elevação (~29 m)",
         "quando": "estável"},
        {"o_que": "Edifícios",
         "quem": "Microsoft Global ML Building Footprints",
         "quando": "18 914 no concelho"},
        {"o_que": "Pontos de água, faixas de gestão e perigosidade oficial",
         "quem": "PMDFCI do município (ICNF)",
         "quando": "2021–2030"},
        {"o_que": "Estradas e quartéis de bombeiros",
         "quem": "OpenStreetMap",
         "quando": "contínua"},
    ]


def export(name: str = "Baião") -> Path:
    res = priority.build(name)
    lon, lat = res["lon"], res["lat"]
    susc, cons, prio = res["susceptibility"], res["consequence"], res["priority"]
    f = np.load(priority.OUT_DIR / f"features_{name.lower()}.npz")

    # current vegetation STATE from the latest Sentinel image (NDMI = moisture;
    # low = dry). Shown only over vegetation, masked off water/urban.
    panel = np.load(priority.OUT_DIR / f"veg_panel_{name.lower()}.npz")
    latest_vy = max(int(k.split("_")[1]) for k in panel.files if k.startswith("ndmi_"))
    ndmi_cell = np.nan_to_num(panel[f"ndmi_{latest_vy}"], nan=0.3)
    sentinel_date = None   # derived below from the newest image actually used
    tempo_bombeiros = priority.response_minutes(f["dist_bombeiros_m"], name)

    # official PMDFCI 2021 hazard surface, for side-by-side comparison
    try:
        from .official_plan import sample_cells

        oficial_cell = sample_cells(name)
    except Exception:
        oficial_cell = None

    # monthly dryness history (map slider + trend chart), if built
    hist_npz = priority.OUT_DIR / f"dryness_history_{name.lower()}.npz"
    hist_meta = priority.OUT_DIR / f"dryness_history_{name.lower()}.json"
    months, month_cells = [], []
    if hist_npz.exists() and hist_meta.exists():
        h = np.load(hist_npz)
        month_images, month_anom = {}, []
        for m in json.loads(hist_meta.read_text())["months"]:
            if m["key"] not in h.files:
                continue
            months.append(m["label"])
            month_images[m["label"]] = m.get("imagens", [])
            for im in m.get("imagens", []):
                if not sentinel_date or im["data"] > sentinel_date:
                    sentinel_date = im["data"]
            month_cells.append(np.nan_to_num(h[m["key"]], nan=0.3))
            # anomaly vs the same calendar month in past years
            try:
                from .anomaly import baseline_for_month

                ym = m["key"].split("_")[1]
                base, _yrs = baseline_for_month(name, int(ym[5:7]))
                month_anom.append(np.nan_to_num(h[m["key"]] - base, nan=0.0))
            except Exception:
                month_anom.append(None)
    else:
        month_images, month_anom = {}, []

    mid_lat = float(lat.mean())
    dlat = SQUARE_M / 111_320.0
    dlon = SQUARE_M / (111_320.0 * np.cos(np.radians(mid_lat)))
    ix = np.floor((lon - lon.min()) / dlon).astype(int)
    iy = np.floor((lat - lat.min()) / dlat).astype(int)
    key = ix * 100000 + iy

    features = []
    agg = []
    for k in np.unique(key):
        m = key == k
        if m.sum() < 6:
            continue
        x0 = lon.min() + ix[m][0] * dlon
        y0 = lat.min() + iy[m][0] * dlat
        fuel_mode = int(np.bincount(f["fuel_code"][m].astype(int)).argmax())
        # NDMI only over vegetation (not water=9 / urban=1)
        is_veg = fuel_mode not in (1, 9)
        ndmi_val = round(float(ndmi_cell[m].mean()), 3) if is_veg else None
        props = {
            "priority": round(float(prio[m].mean()), 3),
            "susceptibility": round(float(susc[m].mean()), 3),
            "consequence": round(float(cons[m].mean()), 2),
            "fuel": _fuel_label(fuel_mode),
            "ndmi": ndmi_val,
            "slope": round(float(f["slope"][m].mean())),
            "dist_casas": round(float(f["dist_building_m"][m].mean())),
            # share of the square's AREA that burned at least once since 2009
            "ardeu_antes_pct": round(float((f["n_years_burned"][m] > 0).mean()) * 100),
            # how many TIMES it burned (mean fire-years per cell, 2009-2025)
            "vezes_ardeu": round(float(f["n_years_burned"][m].mean()), 1),
            "area_ha": round(float(m.sum()) * 29 * 29 / 1e4, 1),
            "houses_500m": int(f["houses_500m"][m].max()),
            "houses_250m": int(f["houses_250m"][m].max()),
            "agua_m": round(float(f["dist_water_m"][m].mean())),
            "estrada_m": round(float(f["dist_road_m"][m].mean())),
            "bombeiros_min": round(float(tempo_bombeiros[m].mean())),
        }
        for i, cells in enumerate(month_cells):
            props[f"ndmi_m{i}"] = round(float(cells[m].mean()), 3) if is_veg else None
            an = month_anom[i] if i < len(month_anom) else None
            props[f"anom_m{i}"] = round(float(an[m].mean()), 3) if (is_veg and an is not None) else None
        if oficial_cell is not None:
            vals = oficial_cell[m]
            vals = vals[~np.isnan(vals)]
            props["oficial"] = round(float(vals.mean()), 1) if len(vals) else None
        poly = [[[x0, y0], [x0 + dlon, y0], [x0 + dlon, y0 + dlat], [x0, y0 + dlat], [x0, y0]]]
        features.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": poly}, "properties": props})
        agg.append(props["priority"])

    # percentile ranks across squares: both priority and susceptibility are shown
    # RELATIVE to the municipality (single-year burn probability is low everywhere,
    # so absolute colouring collapses — percentile keeps the map readable).
    agg_arr = np.array(agg)
    pct = agg_arr.argsort().argsort() / (len(agg_arr) - 1) * 100.0
    susc_arr = np.array([ft["properties"]["susceptibility"] for ft in features])
    susc_pct = susc_arr.argsort().argsort() / (len(susc_arr) - 1) * 100.0
    for feat, p, sp in zip(features, pct, susc_pct):
        feat["properties"]["pct"] = round(float(p), 1)
        feat["properties"]["susc_pct"] = round(float(sp), 1)

    # percentile of the official hazard, so both surfaces are compared as ranks
    of_vals = np.array([ft["properties"].get("oficial") for ft in features], dtype=float)
    of_ok = ~np.isnan(of_vals)
    if of_ok.sum() > 1:
        ranks = np.full(len(of_vals), np.nan)
        ranks[of_ok] = of_vals[of_ok].argsort().argsort() / (of_ok.sum() - 1) * 100.0
        for feat, r in zip(features, ranks):
            feat["properties"]["oficial_pct"] = None if np.isnan(r) else round(float(r), 1)

    # rank: mark the top squares so the UI can highlight "act here first"
    order = np.argsort(-agg_arr)
    for rank, idx in enumerate(order[:15], 1):
        features[int(idx)]["properties"]["rank"] = rank

    # municipality-wide dryness index (0 = humid, 100 = very dry) over vegetation
    def _dry_stats(key):
        vals = np.array([ft["properties"][key] for ft in features if ft["properties"].get(key) is not None])
        if not len(vals):
            return None, None
        return (round(float(np.clip((0.4 - vals.mean()) / 0.4, 0, 1) * 100)),
                round(float((vals < 0.15).mean()) * 100))

    dryness_index, pct_dry = _dry_stats("ndmi")
    seca_series = []
    for i, label in enumerate(months):
        idx, pdry = _dry_stats(f"ndmi_m{i}")
        an = np.array([ft["properties"].get(f"anom_m{i}") for ft in features], dtype=float)
        an = an[~np.isnan(an)]
        seca_series.append({
            "mes": label, "idx": idx, "pct_dry": pdry,
            "imagens": month_images.get(label, []),
            # negative anomaly = drier than the same month in past years
            "anom": round(float(an.mean()), 3) if len(an) else None,
            "pct_acima_normal": round(float((an < 0).mean()) * 100) if len(an) else None,
        })

    fc = {
        "type": "FeatureCollection",
        "properties": {"municipality": name, "square_m": SQUARE_M, "sentinel_date": sentinel_date,
                       "dryness_index": dryness_index, "pct_dry": pct_dry,
                       "dryness_series": seca_series,
                       "sources": _sources(name, sentinel_date),
                       "model": _model_meta(name)},
        "features": features,
    }
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    out = WEB_DIR / f"{name.lower().replace('ã', 'a')}_priority.geojson"
    out.write_text(json.dumps(fc, ensure_ascii=False))
    print(f"{len(features)} squares exported -> {out}")
    return out


def export_context(name: str = "Baião") -> None:
    """Export context layers (buildings, roads, water) from the OSM caches."""
    import shapely
    from shapely.geometry import shape as _shape  # noqa: F401

    from .boundary import municipality_polygon

    poly, _ = municipality_polygon(name)
    slug = name.lower().replace("ã", "a")
    cache = Path(__file__).resolve().parent.parent / "data" / "cache"
    WEB_DIR.mkdir(parents=True, exist_ok=True)

    # buildings -> points (clipped to the municipality); MS ML footprints w/ OSM fallback
    from .buildings import get_buildings

    _, bbox = municipality_polygon(name)
    pts, _source = get_buildings(name, bbox)
    lons = np.array([p[0] for p in pts]); lats = np.array([p[1] for p in pts])
    inside = shapely.contains_xy(poly, lons, lats)
    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(lo), float(la)]}, "properties": {}}
        for (lo, la), ok in zip(pts, inside) if ok
    ]
    (WEB_DIR / f"{slug}_buildings.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"casas: {len(feats)} pontos")

    from shapely.geometry import LineString, Point as ShpPoint, Polygon as ShpPolygon, mapping

    def _clipped(geom):
        """Clip a shapely geometry to the municipality; yield geojson features."""
        inter = geom.intersection(poly)
        if inter.is_empty:
            return
        parts = getattr(inter, "geoms", [inter])
        for part in parts:
            if part.is_empty or part.geom_type not in ("Point", "LineString", "Polygon"):
                continue
            yield {"type": "Feature", "geometry": mapping(part), "properties": {}}

    def _ways_to_features(raw, closed_as_polygon=False):
        out = []
        for el in raw.get("elements", []):
            if el["type"] == "node":
                p = ShpPoint(el["lon"], el["lat"])
                if poly.contains(p):
                    out.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [el["lon"], el["lat"]]}, "properties": {}})
            elif "geometry" in el:
                coords = [(g["lon"], g["lat"]) for g in el["geometry"]]
                if closed_as_polygon and len(coords) > 3 and coords[0] == coords[-1]:
                    try:
                        out.extend(_clipped(ShpPolygon(coords)))
                    except Exception:
                        continue
                elif len(coords) > 1:
                    out.extend(_clipped(LineString(coords)))
        return out

    roads = _ways_to_features(json.loads((cache / f"roads_{name.lower()}.json").read_text()))
    (WEB_DIR / f"{slug}_roads.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": roads}))
    print(f"estradas: {len(roads)} vias")

    water = _ways_to_features(json.loads((cache / f"water_{name.lower()}.json").read_text()), closed_as_polygon=True)
    from .pmdfci import rpa_points, rpa_points_full

    for p in rpa_points(name):
        water.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
            "properties": {"nome": p["nome"], "volume_m3": p["volume_m3"], "oficial": True, "classe": "M"},
        })
    for p in rpa_points_full(name):
        water.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
            "properties": {"nome": p["nome"], "volume_m3": p["volume_m3"], "oficial": True, "classe": p["classe"] or "T"},
        })
    (WEB_DIR / f"{slug}_water.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": water}, ensure_ascii=False))
    print(f"água: {len(water)} elementos (inclui RPA oficial)")


def export_comparison(name: str = "Baião") -> None:
    """Copy the plan-vs-model head-to-head results for the app to display."""
    src = priority.OUT_DIR / f"comparison_{name.lower()}.json"
    if not src.exists():
        print("comparação ainda não calculada (corre official_plan.head_to_head)")
        return
    slug = name.lower().replace("ã", "a")
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    (WEB_DIR / f"{slug}_comparison.json").write_text(src.read_text())
    print("comparação plano vs modelo exportada")


def export_fires(name: str = "Baião") -> None:
    """Export ICNF burnt-area perimeters per year, clipped to the municipality —
    the ground truth to overlay on the 'onde arde' view (model vs reality)."""
    import shapely
    from shapely.geometry import mapping, shape

    from . import icnf_labels
    from .boundary import municipality_polygon

    poly, bbox = municipality_polygon(name)
    slug = name.lower().replace("ã", "a")
    feats = []
    for layer_id, year in icnf_labels.list_year_layers():
        for fr in icnf_labels.fetch_year(layer_id, bbox):
            g = fr.get("geometry")
            if not g:
                continue
            geom = shape(g)
            if not geom.intersects(poly):
                continue
            clip = geom.intersection(poly).simplify(0.0002)  # ~20 m, lighter payload
            if clip.is_empty:
                continue
            feats.append({
                "type": "Feature",
                "geometry": mapping(clip),
                "properties": {"ano": year, "ha": round(float(fr["properties"].get("AreaHaPoly") or 0))},
            })
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    (WEB_DIR / f"{slug}_fires.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"fogos: {len(feats)} perímetros ({len({f['properties']['ano'] for f in feats})} anos)")


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "Baião"
    export(target)
    export_context(target)
    export_fires(target)
