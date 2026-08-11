"""Download Sentinel-2 bands and build an NDVI/NDMI composite for a municipality.

Temporal hygiene: we composite SUMMER 2023 (before the 2024-2025 target), so the
vegetation state is a legitimate pre-label feature.

Approach (light-weight — no full ~1 GB products):
  - pick the clearest L2A scenes over Baiao's MGRS tile (T29TNF), summer 2023
  - download only 3 bands at 20 m via the OData Nodes API: B04 (red), B8A (NIR),
    B11 (SWIR)
  - per scene compute NDVI=(NIR-RED)/(NIR+RED) and NDMI=(NIR-SWIR)/(NIR+SWIR)
  - median across scenes -> a clean, cloud-robust composite
  - sample each municipality cell (reprojecting cell lon/lat into the tile's UTM)

Reflectance offset: processing baseline N0510 stores DN with a -1000 offset, so we
use ref = DN - 1000 (DN == 0 is nodata -> NaN). The /10000 scale cancels in ratios.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.warp import transform as warp_transform

from . import sentinel
from .boundary import municipality_polygon

TILE = "T29TNF"           # MGRS tile covering Baião
BANDS = {"B04": "red", "B08": "nir", "B11": "swir"}  # note: 20 m NIR file is B8A
BAND_FILE_KEYS = ["B04", "B8A", "B11"]
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "sentinel"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"
ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1"


def clear_scenes(max_scenes: int = 4, year: int = 2023, max_cloud: float = 15.0,
                 window: tuple[str, str] | None = None):
    """Clearest L2A scenes over the tile. Default window = the year's summer
    (Jun-Sep); pass window=('YYYY-MM-DD','YYYY-MM-DD') for an arbitrary period."""
    token = sentinel.get_token()
    t0, t1 = window if window else (f"{year}-06-01", f"{year}-09-30")
    flt = (
        f"Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') "
        f"and contains(Name,'{TILE}') "
        f"and ContentDate/Start gt {t0}T00:00:00.000Z "
        f"and ContentDate/Start lt {t1}T23:59:59.000Z"
    )
    r = requests.get(
        sentinel.ODATA_URL,
        params={"$filter": flt, "$top": "50", "$expand": "Attributes"},
        timeout=90,
    )
    r.raise_for_status()
    scenes = []
    for p in r.json()["value"]:
        cc = next((float(a["Value"]) for a in p.get("Attributes", []) if a["Name"] == "cloudCover"), None)
        if cc is not None and cc <= max_cloud:
            scenes.append((cc, p["Id"], p["Name"], p["ContentDate"]["Start"][:10]))
    scenes.sort()
    return token, scenes[:max_scenes]


def _download_bands(pid: str, pname: str, token: str) -> dict[str, Path]:
    h = {"Authorization": f"Bearer {token}"}
    base = f"{ODATA}/Products({pid})/Nodes({pname})"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def names(url):
        return [n["Name"] for n in requests.get(url + "/Nodes", headers=h, timeout=60).json().get("result", [])]

    granule = names(f"{base}/Nodes(GRANULE)")[0]
    r20 = f"{base}/Nodes(GRANULE)/Nodes({granule})/Nodes(IMG_DATA)/Nodes(R20m)"
    files = names(r20)

    out = {}
    for key in BAND_FILE_KEYS:
        fname = next(f for f in files if f"_{key}_" in f)
        cache = CACHE_DIR / fname
        if not cache.exists():
            url = f"{r20}/Nodes({fname})/$value"
            with _get_following_redirects(url, h) as resp:
                with open(cache, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
        out[key] = cache
    return out


def _get_following_redirects(url: str, headers: dict):
    """Follow CDSE cross-host download redirects, re-attaching auth on each hop
    (requests drops the Authorization header across hosts, causing 401)."""
    resp = requests.get(url, headers=headers, timeout=600, stream=True, allow_redirects=False)
    hops = 0
    while resp.status_code in (301, 302, 303, 307, 308) and hops < 5:
        loc = resp.headers["Location"]
        resp.close()
        resp = requests.get(loc, headers=headers, timeout=600, stream=True, allow_redirects=False)
        hops += 1
    resp.raise_for_status()
    return resp


def _indices_for_cells(bands: dict[str, Path], lon, lat):
    """Return (ndvi, ndmi) sampled at each cell (lon/lat) from one scene."""
    with rasterio.open(bands["B04"]) as src:
        xs, ys = warp_transform("EPSG:4326", src.crs, list(lon), list(lat))
        t = src.transform  # north-up UTM affine, no rotation
        cols = np.clip(((np.asarray(xs) - t.c) / t.a).astype(int), 0, src.width - 1)
        rows = np.clip(((np.asarray(ys) - t.f) / t.e).astype(int), 0, src.height - 1)
        red = src.read(1)[rows, cols].astype(np.float64)
    with rasterio.open(bands["B8A"]) as src:
        nir = src.read(1)[rows, cols].astype(np.float64)
    with rasterio.open(bands["B11"]) as src:
        swir = src.read(1)[rows, cols].astype(np.float64)

    for arr in (red, nir, swir):
        arr[arr == 0] = np.nan
    red, nir, swir = red - 1000, nir - 1000, swir - 1000  # N0510 offset

    ndvi = (nir - red) / (nir + red)
    ndmi = (nir - swir) / (nir + swir)
    return ndvi, ndmi


def composite_for_year(name: str, year: int, max_scenes: int = 4,
                       window: tuple[str, str] | None = None):
    """Return (ndvi, ndmi) median composite for a period, sampled at the cells.
    Default period = the year's summer; pass window for e.g. a single month."""
    municipality_polygon(name)  # ensure boundary cached
    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    lon, lat = f["lon"], f["lat"]

    _, scenes = clear_scenes(max_scenes=max_scenes, year=year, window=window, max_cloud=20.0)
    if not scenes:
        raise RuntimeError(f"no usable scenes for {window or year}")
    print(f"[{window or year}] {len(scenes)} clearest scenes over {TILE}:")
    ndvis, ndmis = [], []
    for cc, pid, pname, date in scenes:
        print(f"  {date}  {cc:.1f}% cloud  -> downloading bands...")
        token = sentinel.get_token()  # cached; refreshed only when near expiry
        bands = _download_bands(pid, pname, token)
        ndvi, ndmi = _indices_for_cells(bands, lon, lat)
        ndvis.append(ndvi)
        ndmis.append(ndmi)

    return np.nanmedian(np.vstack(ndvis), axis=0), np.nanmedian(np.vstack(ndmis), axis=0)


def build_composite(name: str = "Baião", max_scenes: int = 4):
    f = np.load(OUT_DIR / f"features_{name.lower()}.npz")
    ndvi_med, ndmi_med = composite_for_year(name, 2023, max_scenes)

    # save alongside the feature grid (new columns)
    data = {k: f[k] for k in f.files}
    data["ndvi"] = ndvi_med.astype(np.float32)
    data["ndmi"] = ndmi_med.astype(np.float32)
    out = OUT_DIR / f"features_{name.lower()}.npz"
    np.savez_compressed(out, **data)

    print(f"\nNDVI: median {np.nanmedian(ndvi_med):.3f}  (p10 {np.nanpercentile(ndvi_med,10):.3f} / p90 {np.nanpercentile(ndvi_med,90):.3f})")
    print(f"NDMI: median {np.nanmedian(ndmi_med):.3f}  (p10 {np.nanpercentile(ndmi_med,10):.3f} / p90 {np.nanpercentile(ndmi_med,90):.3f})")
    print(f"cells with valid NDVI: {(~np.isnan(ndvi_med)).mean()*100:.1f}%")
    print(f"Saved NDVI/NDMI into {out}")
    return out


if __name__ == "__main__":
    import sys

    build_composite(sys.argv[1] if len(sys.argv) > 1 else "Baião")
