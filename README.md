<p align="center">
  <img src="assets/icon.png" alt="" width="160" height="160">
</p>

<h1 align="center">wildfire-prevention</h1>

<p align="center">
  Where fuel management protects the most, from open and official data, updated automatically.
</p>

<p align="center">
  <a href="https://firebreak.tiago-coutinho.com"><strong>firebreak.tiago-coutinho.com</strong></a>
  &nbsp;·&nbsp;
  <a href="https://firebreak.tiago-coutinho.com/mapa">Open the map</a>
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
make status    # what is built, and how recent it is
```

Rebuilding everything from scratch takes hours and downloads ~10 GB of satellite
imagery:

```bash
make data
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

`make retrain` saves the model to `data/out/model_<municipality>.txt` and
records, in a companion `.json`, **when it was trained, over which years, on how
many rows, with which variables, and how well it scored** — the holdout AUC from
a temporal split, so a later model can be compared with the one it replaced.

Three rules protect that artifact:

| | |
|---|---|
| **The old model is kept** | superseded models move to `data/out/models/`, stamped with their training date. The last five stay. Rollback is a file copy. |
| **A worse model is not promoted** | if the mean holdout AUC falls more than 0.01, the model in production is left untouched. `make retrain FORCE=1` overrides that deliberately. |
| **Scoring never trains** | a missing or stale artifact raises instead of quietly training one, so a scheduled job cannot publish a map from a model nobody chose. |

This exists for a concrete reason: a published map informs decisions about public
spending. Months later, it must still be possible to answer *which* model
produced the map a decision was based on — and whether it was any good.
`make status` always reports the one in use.

---

## Automatic updates

```bash
make refresh              # ignitions + burned areas + dryness + export  (~4 min)
make cron                 # prints the crontab line for a weekly schedule
make retrain              # retrain the model (after each fire season)
make retrain FORCE=1      # ...promoting it even if the holdout AUC drops
```

Every step tolerates failure: one source being down does not stop the others, and
the application keeps serving the last good data.

The refresh never retrains. Scoring loads the saved model and raises if it is
missing, so a scheduled job can never quietly publish a map from a model nobody
reviewed. Retraining is always a decision someone made.

---

## Deployment

The pilot runs at **[firebreak.tiago-coutinho.com](https://firebreak.tiago-coutinho.com)**,
on a Debian LXC container on a Proxmox host at home. Everything runs there: the
weekly refresh, the satellite downloads and the model scoring. No laptop is
involved, so the map keeps updating whether or not anyone is around.

### How a request arrives

```
internet -> Cloudflare (TLS)
              |  tunnel: an OUTBOUND connection, so no router port is open
        cloudflared        (systemd service in the container)
              |  http://localhost:80
            nginx          (systemd service in the container)
              |
   webapp/dist/            the interface, rebuilt by `make build`
   webapp/public/data/     the GeoJSON, rewritten by the weekly cron
```

There is no application server. The Python runs for about four minutes a week,
writes GeoJSON, and exits; nginx serves the files it left behind. Nothing of
ours is alive between runs.

### The one mistake to avoid

`dist/` only receives a copy of the data **at build time**. If nginx serves
`/data/` from inside `dist/`, the cron will update the real files and the site
will keep showing the old ones, with no error anywhere.

So `/data/` is served straight from `webapp/public/data/`, where the cron
writes. Data updates without rebuilding the interface, and the interface
rebuilds without touching the data.

### What the server needs

| | | |
|---|---|---|
| `.env` | Copernicus credentials, mode 600 | required for the weekly dryness step |
| `uv` | installed in `/usr/local/bin` | the cron `PATH` is minimal and will not find `~/.local/bin` |
| Node 20+ | for `vite build` | Debian 13 ships it; Debian 12 ships an end-of-life Node 18 |
| `libgomp1` | OpenMP runtime | LightGBM needs it to train — this is what replaces `brew install libomp` on macOS |
| `data/out` + `data/cache` | **229 MB**, copied once by rsync | not in git; the 14 GB of raw Sentinel bands are **not** required |

The raw bands only rebuild historical composites. The cron downloads the current
month by itself, which grows the cache by roughly 2-4 GB a year — worth pruning
scenes older than three months.

Reference container: 4 cores, 4 GB RAM, 32 GB disk. The RAM sizes the yearly
`make retrain`, not the weekly refresh, which peaks far below it.

### Provisioning a new one

```bash
apt install -y curl ca-certificates git rsync nginx libgomp1 nodejs npm
curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh

git clone https://github.com/COU7INHO/wildfire-prevention.git /opt/firebreak
cd /opt/firebreak && uv sync && (cd webapp && npm install)

# seed what git does not carry, from a machine that has it
# rsync -rltz --exclude='*.png' data/out/   <host>:/opt/firebreak/data/out/
# rsync -rltz --exclude='sentinel/' data/cache/ <host>:/opt/firebreak/data/cache/

make build      # interface -> webapp/dist/
make nginx      # prints the server block; mind the /data/ alias above
make cron       # prints the crontab line, with the PATH already resolved
make status     # everything green before opening it up
```

`.geojson` is missing from nginx's `mime.types`, so it is served as
`application/octet-stream` and skipped by gzip — the largest file on the site.
The server block maps it explicitly, which cuts the map payload from 7.6 MB to
1.5 MB.

---

## Known limitations

- **One municipality only.** Baião is hardcoded in several places (Sentinel tile
  `T29TNF`, DICO code `1302`, GHSL tile). `make data MUN=X` does not yet work
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
  dryness_history.py    recent dryness months (sliding window)
  anomaly.py        dryness against the same month in previous years
  panel_model.py     model, tuned hyperparameters, susceptibility
  tune.py            hyperparameter search with a separate validation split
  priority.py        priority = susceptibility x exposure x difficulty
  official_plan.py   PMDFCI hazard raster + comparison with uncertainty
  export_web.py      the GeoJSON the application reads
  refresh.py       what the cron job runs
  status.py          make status
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
