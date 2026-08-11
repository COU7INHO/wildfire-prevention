"""Audit Sentinel-2 imagery availability for a municipality.

Source: Copernicus Data Space Ecosystem (CDSE) — free ESA account, CC-BY-like
open licence (clean for commercial use, unlike Earth Engine terms).

This audit answers: how often does Sentinel-2 usably see the municipality?
We query the CDSE OData catalogue for L2A products (surface reflectance,
atmosphere-corrected) over the bbox in a recent window and report the pass
cadence and cloud-cover distribution. Cloud cover is the real constraint in
northern Portugal: revisit is ~5 days, but usable (low-cloud) scenes are fewer.

Auth: credentials read from .env (CDSE_USERNAME / CDSE_PASSWORD), exchanged for
a token at the CDSE identity server. Catalogue search itself is public; the
token matters for downloads (next phase: NDVI/NDMI computation).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .boundary import municipality_polygon

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "out"
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
ODATA_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"


def _load_env() -> dict[str, str]:
    env = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


# Cached token. CDSE locks the account when too many sessions are opened, so we
# reuse one token until shortly before it expires instead of asking per download.
_TOKEN: dict = {"value": None, "expires_at": 0.0}


def get_token(force: bool = False) -> str:
    now = time.time()
    if not force and _TOKEN["value"] and now < _TOKEN["expires_at"]:
        return _TOKEN["value"]

    env = _load_env()
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": "cdse-public",
            "grant_type": "password",
            "username": env["CDSE_USERNAME"],
            "password": env["CDSE_PASSWORD"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    _TOKEN["value"] = payload["access_token"]
    # refresh 2 min before the stated expiry (usually 600 s)
    _TOKEN["expires_at"] = now + max(60, int(payload.get("expires_in", 600)) - 120)
    return _TOKEN["value"]


@dataclass
class SceneInfo:
    date: str
    cloud_pct: float
    name: str


@dataclass
class SentinelAudit:
    municipality: str
    window_days: int
    n_scenes: int
    n_usable: int  # cloud cover <= 30%
    median_gap_days: float | None
    usable_median_gap_days: float | None
    scenes: list[SceneInfo] = field(default_factory=list)


def audit_municipality(name: str, window_days: int = 120) -> SentinelAudit:
    _, (minx, miny, maxx, maxy) = municipality_polygon(name)
    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    aoi = f"SRID=4326;POLYGON(({minx} {miny},{maxx} {miny},{maxx} {maxy},{minx} {maxy},{minx} {miny}))"
    flt = (
        f"Collection/Name eq 'SENTINEL-2' "
        f"and contains(Name,'L2A') "
        f"and OData.CSC.Intersects(area=geography'{aoi}') "
        f"and ContentDate/Start gt {since}"
    )
    resp = requests.get(
        ODATA_URL,
        params={
            "$filter": flt,
            "$orderby": "ContentDate/Start asc",
            "$top": "200",
            "$expand": "Attributes",
        },
        timeout=90,
    )
    resp.raise_for_status()
    products = resp.json().get("value", [])

    scenes: list[SceneInfo] = []
    for p in products:
        cloud = None
        for a in p.get("Attributes", []):
            if a.get("Name") == "cloudCover":
                cloud = float(a.get("Value"))
        if cloud is None:
            continue
        scenes.append(
            SceneInfo(
                date=p["ContentDate"]["Start"][:10],
                cloud_pct=round(cloud, 1),
                name=p["Name"],
            )
        )

    dates = sorted({s.date for s in scenes})
    usable_dates = sorted({s.date for s in scenes if s.cloud_pct <= 30})

    return SentinelAudit(
        municipality=name,
        window_days=window_days,
        n_scenes=len(scenes),
        n_usable=sum(1 for s in scenes if s.cloud_pct <= 30),
        median_gap_days=_median_gap(dates),
        usable_median_gap_days=_median_gap(usable_dates),
        scenes=scenes,
    )


def _median_gap(dates: list[str]) -> float | None:
    if len(dates) < 2:
        return None
    ds = [datetime.strptime(d, "%Y-%m-%d") for d in dates]
    gaps = sorted((b - a).days for a, b in zip(ds, ds[1:]))
    mid = len(gaps) // 2
    return float(gaps[mid]) if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2.0


def print_report(a: SentinelAudit) -> None:
    print(f"\n=== Sentinel-2 availability audit: {a.municipality} (last {a.window_days} days) ===\n")
    print(f"L2A scenes covering the area: {a.n_scenes}")
    print(f"Usable scenes (cloud <= 30%): {a.n_usable}")
    if a.median_gap_days is not None:
        print(f"Median gap between passes: {a.median_gap_days:.0f} days")
    if a.usable_median_gap_days is not None:
        print(f"Median gap between USABLE passes: {a.usable_median_gap_days:.0f} days")
    print("\nMost recent 12 scenes:")
    for s in a.scenes[-12:]:
        flag = "OK " if s.cloud_pct <= 30 else "cld"
        print(f"  {s.date}  {s.cloud_pct:>5.1f}% cloud  [{flag}]")


def save_report(a: SentinelAudit) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"sentinel_audit_{a.municipality.lower()}.json"
    payload = {
        "municipality": a.municipality,
        "window_days": a.window_days,
        "n_scenes": a.n_scenes,
        "n_usable": a.n_usable,
        "median_gap_days": a.median_gap_days,
        "usable_median_gap_days": a.usable_median_gap_days,
        "scenes": [vars(s) for s in a.scenes],
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return out


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "Baião"
    print("Testing CDSE authentication...")
    get_token()
    print("Auth OK.")
    result = audit_municipality(name)
    print_report(result)
    path = save_report(result)
    print(f"\nSaved: {path}")
