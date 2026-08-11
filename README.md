<p align="center">
  <img src="assets/icon.png" alt="" width="160" height="160">
</p>

<h1 align="center">wildfire-prevention</h1>

<p align="center">
  Where fuel management protects the most, from open and official data, updated automatically.
</p>

---

A decision-support tool for municipalities. It is not a fire-spread simulator,
and it does not replace the statutory municipal fire defence plan (PMDFCI). It
is the decision layer on top of it: susceptibility × exposure × suppression
difficulty, ranked and explained in plain language.

**Pilot municipality: Baião**, classified T4 by the ICNF — the worst tier, with
both a high number of ignitions and a large burned area.

The interface is in Portuguese, because its users are Portuguese municipal
technicians. Everything else is in English.

---

## Getting started

```bash
make setup     # once: uv sync, npm install, libomp
make app       # http://localhost:5175
make estado    # what is built, and how recent it is
```

Rebuilding everything from scratch takes hours and downloads ~10 GB of satellite
imagery:

```bash
make dados
```

`make help` lists every target. Dependencies are managed with
[uv](https://docs.astral.sh/uv/), not pip.

---

## What the application shows

| View | Answers | Refresh |
|---|---|---|
| **Where to act** | where prevention protects the most (susceptibility × exposure × difficulty) | yearly |
| **Where it burns** | structural propensity, with real burned areas overlaid by year | yearly |
| **Vegetation** | land cover, including species (eucalyptus / pine / broadleaf) | ~5 years |
| **Dryness** | current vegetation state and the **anomaly** against the same month across 2015-2025 | weekly |
| **2021 Plan** | side-by-side comparison with the official PMDFCI hazard map | — |

Context layers available in every view: buildings, roads, official water points,
and the current year's ignitions.

---

## Data sources

| Data | Source | Typical age |
|---|---|---|
| Burned areas | ICNF (ArcGIS REST, one layer per year) | 2009-2025 |
| Current-year ignitions | Civil Protection via `api.fogos.pt` | near real time |
| Terrain (slope, aspect) | AWS Terrain Tiles, ~29 m | stable |
| Land cover | COS 2015 / 2018 / 2023 (DGT) | multi-year |
| Vegetation (NDVI/NDMI) | Sentinel-2 L2A via Copernicus (CDSE) | ~5 days |
| Buildings | Microsoft Global ML Building Footprints | 18,914 in Baião |
| Water points | **the municipality's own PMDFCI** (RPA, 2G+3G) | 120 points |
| Fire stations, roads | OpenStreetMap | continuous |
| Official hazard map | **PMDFCI 2021-2030**, 10 m raster | fixed until 2030 |

Sentinel access needs Copernicus credentials in `.env`
(`CDSE_USERNAME` / `CDSE_PASSWORD`). See `.env.example`; the real file is
git-ignored. Every other step runs without them.

---

## The model

**LightGBM** over a panel of 2.1 M rows (210,998 cells × 10 years). Each row uses
vegetation, fuel and fire history from **before** the year it predicts, so no
label information can leak backwards into the features.

Hyperparameters were tuned on a validation split held apart from the test set
(training ≤2019 · validation 2020-2021 · test 2022-2025, touched exactly once).
Strong regularisation (`min_child_samples=2000`) turned out to be essential:
without it the model memorises individual cells instead of learning patterns.

### Honest validation

| Test | AUC |
|---|---|
| Unseen years (2023 / 2024 / 2025) | **0.804** |
| Unseen years **and unseen terrain** (half the municipality) | **0.763** |
| Fair comparison against the official hazard map (2022-2025) | 0.681 vs 0.700 |

**The gap against the official map is not statistically significant**
(95% CI [−0.020, +0.069], spatial block bootstrap). Within the margin of error,
the two are equivalent.

Accuracy is therefore not the differentiator. The difference that matters is that
the official map is **frozen until 2030**, while this model **retrains**.

### The model is an identifiable artifact

`make retreinar` saves the model to `data/out/modelo_<municipality>.txt` and
records, in a companion `.json`, **when it was trained, over which years, on how
many rows, and with which variables**. The weekly update scores with that saved
model rather than training a fresh one each time.

This exists for a concrete reason: a published map informs decisions about public
spending. Months later, it must still be possible to answer *which* model
produced the map a decision was based on. `make estado` always reports the one in
use.

---

## Automatic updates

```bash
make atualizar    # ignitions + burned areas + dryness + export  (~3 min)
make cron         # prints the crontab line for a weekly schedule
make retreinar    # retrain the model (after each fire season)
```

Every step tolerates failure: one source being down does not stop the others, and
the application keeps serving the last good data.

---

## Deployment

```bash
make build     # production interface -> webapp/dist/
make nginx     # prints the server configuration
make cron      # prints the crontab line
```

**Watch out for this one**, it is the easy mistake to make: `dist/` only receives
the data at build time. If nginx serves data from inside `dist/`, the cron job
will update the files and **the site will keep showing the old ones, silently**.

That is why `/data/` is served directly from `webapp/public/data/`, which is where
the cron job writes. Data then updates without rebuilding the interface, and the
interface rebuilds without touching the data.

The server needs:

| | |
|---|---|
| `.env` | Copernicus credentials |
| `uv` | on the cron `PATH` (the printed line sets it) |
| `data/out` + part of `data/cache` | ~200 MB — the 14 GB of satellite bands are **not** required |

The raw bands are only needed to rebuild historical composites; the cron job
downloads whatever it needs for the current month.

---

## Known limitations

- **One municipality only.** Baião is hardcoded in several places (Sentinel tile
  `T29TNF`, DICO code `1302`, GHSL tile). `make dados MUN=X` does not yet work
  for another municipality.
- **Suppression bias.** The data shows where fire burned, not where firefighters
  stopped it. No wildfire model escapes this.
- **No weather.** This does not forecast a specific day, only structural
  propensity. That is why a "seasonal readiness" view was built and then removed:
  without wind, temperature and FWI the signal was not trustworthy.
- **Dryness is descriptive, not predictive.** It reports what the satellite
  measured, not what is going to burn.
- **~29 m resolution.** Legal fuel-management strips (median 0.09 ha) are too
  narrow to monitor from this imagery.
- **Land cover is from 2023**, predating the large 2024 fire.

---

## Layout

```
wildfire_prevention/
  boundary.py        municipal boundary (OSM)
  features.py        cell grid: terrain + land cover + ICNF fire history
  access.py          buildings, roads, water, fire stations -> grid columns
  veg_panel.py       per-year vegetation panel (Sentinel)
  monthly_archive.py monthly composites 2015-2025 (basis for the anomaly)
  seca_history.py    recent dryness months (sliding window)
  anomalia.py        dryness against the same month in previous years
  panel_model.py     model, tuned hyperparameters, susceptibility
  tune.py            hyperparameter search with a separate validation split
  priority.py        priority = susceptibility x exposure x difficulty
  plano_oficial.py   PMDFCI hazard raster + comparison with uncertainty
  export_web.py      the GeoJSON the application reads
  atualizar.py       what the cron job runs
  estado.py          make estado
webapp/              React + MapLibre (user interface in Portuguese)
data/cache/          downloaded, ~10 GB, outside git
data/out/            intermediate products
```

---

## Working principle

Measure before asserting. Several promising conclusions collapsed once they were
tested, and they are recorded here rather than hidden: a biased comparison
against the official plan, a "readiness" model that answered the wrong question
well, an economic valuation with 43% of the polygons unvalued.

If a number in this README looks too good, the test that produced it is in the
code.
