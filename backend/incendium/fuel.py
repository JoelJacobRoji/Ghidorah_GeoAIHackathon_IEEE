"""Fuel map for a local analysis window from Sentinel-2 L2A (Copernicus Process API).

For each pixel, the median NDVI and NDMI of cloud-free observations over the
last LOOKBACK_DAYS days are computed on the Copernicus side (SCL masking as in
providers/cdse.py), plus the share of observations classified as water.

Fuel classes (a simple, documented proxy, NOT a fuel-type map):
  0 non-fuel  water (≥ 50 % of observations), or NDVI < 0.15 (bare, rock, built-up, burnt)
  1 grass     0.15 ≤ NDVI < 0.35  (grassland, crop residue, very open scrub)
  2 shrub     0.35 ≤ NDVI < 0.55  (scrub, open or leaf-off woodland)
  3 forest    NDVI ≥ 0.55
In the dry season, leaf-off deciduous forest can fall into "shrub".
"""
from __future__ import annotations

import hashlib
import io
import logging
from datetime import timedelta
from pathlib import Path

import numpy as np

from .config import BACKEND_DIR, get_settings
from .db import iso, utcnow
from .providers import cdse
from .providers.http import ProviderError, make_client, request_with_retry
from .terrain import Grid

log = logging.getLogger(__name__)

PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
LOOKBACK_DAYS = 45
CACHE_DIR = BACKEND_DIR / "instance" / "cache" / "fuel"
CACHE_MAX_AGE = timedelta(days=7)
FUEL_NAMES = {0: "non-fuel", 1: "grass", 2: "shrub", 3: "forest"}
NDVI_BREAKS = (0.15, 0.35, 0.55)

EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B08", "B8A", "B11", "SCL", "dataMask"] }],
    output: { bands: 4, sampleType: "FLOAT32" },
    mosaicking: "ORBIT"
  };
}
const EXCLUDED = [0, 1, 3, 6, 8, 9, 10, 11];
function median(a) {
  if (a.length === 0) return NaN;
  a.sort(function (x, y) { return x - y; });
  const m = Math.floor(a.length / 2);
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}
function evaluatePixel(samples) {
  const ndvi = [], ndmi = [];
  let water = 0, seen = 0;
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i];
    if (s.dataMask !== 1) continue;
    seen++;
    if (s.SCL === 6) water++;
    if (EXCLUDED.indexOf(s.SCL) !== -1) continue;
    const v = (s.B08 - s.B04) / (s.B08 + s.B04);
    const m = (s.B8A - s.B11) / (s.B8A + s.B11);
    if (isFinite(v) && isFinite(m)) { ndvi.push(v); ndmi.push(m); }
  }
  return [median(ndvi), median(ndmi), seen ? water / seen : NaN, ndvi.length];
}
"""


def classify(ndvi: np.ndarray, water_share: np.ndarray) -> np.ndarray:
    fuel = np.zeros(ndvi.shape, dtype=np.int8)
    v = np.nan_to_num(ndvi, nan=-1.0)
    fuel[(v >= NDVI_BREAKS[0]) & (v < NDVI_BREAKS[1])] = 1
    fuel[(v >= NDVI_BREAKS[1]) & (v < NDVI_BREAKS[2])] = 2
    fuel[v >= NDVI_BREAKS[2]] = 3
    fuel[np.nan_to_num(water_share, nan=0.0) >= 0.5] = 0
    return fuel


def _key(grid: Grid) -> str:
    return hashlib.sha1(f"{grid.west:.4f},{grid.south:.4f},{grid.east:.4f},{grid.north:.4f},{grid.nx},{grid.ny}".encode()).hexdigest()[:16]


def fetch_fuel(grid: Grid, cache_dir: Path | None = None) -> dict:
    """Returns {"ndvi", "ndmi", "water_share", "obs", "fuel", "retrieved_at", "period"} for the grid."""
    cdir = cache_dir or CACHE_DIR
    path = cdir / f"fuel_{_key(grid)}.npz"
    if path.exists():
        z = np.load(path, allow_pickle=False)
        retrieved = str(z["retrieved_at"])
        from datetime import datetime

        if utcnow() - datetime.fromisoformat(retrieved.replace("Z", "+00:00")) < CACHE_MAX_AGE:
            return {k: z[k] for k in ("ndvi", "ndmi", "water_share", "obs", "fuel")} | {
                "retrieved_at": retrieved, "period": [str(z["period_from"]), str(z["period_to"])], "cached": True}

    s = get_settings()
    if not (s.cdse_client_id and s.cdse_client_secret):
        raise ProviderError(cdse.PROVIDER, "credentials not configured")
    end = utcnow()
    start = end - timedelta(days=LOOKBACK_DAYS)
    body = {
        "input": {
            "bounds": {"bbox": [grid.west, grid.south, grid.east, grid.north],
                       "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},
            "data": [{"type": "sentinel-2-l2a", "dataFilter": {
                "timeRange": {"from": start.strftime("%Y-%m-%dT00:00:00Z"), "to": end.strftime("%Y-%m-%dT23:59:59Z")},
                "maxCloudCoverage": 80}}],
        },
        "output": {"width": grid.nx, "height": grid.ny, "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
        "evalscript": EVALSCRIPT,
    }
    tokens = cdse.TokenProvider(s.cdse_client_id, s.cdse_client_secret)
    with make_client(timeout=180) as client:
        token = tokens.get(client)
        resp = request_with_retry(client, cdse.PROVIDER, "POST", PROCESS_URL, json=body,
                                  headers={"Authorization": f"Bearer {token}", "Accept": "image/tiff"}, attempts=4)
    if resp.status_code != 200:
        raise ProviderError(cdse.PROVIDER, f"process request failed (HTTP {resp.status_code})", resp.status_code)
    import tifffile

    arr = tifffile.imread(io.BytesIO(resp.content)).astype(np.float32)
    if arr.ndim == 3 and arr.shape[0] == 4 and arr.shape[-1] != 4:
        arr = np.moveaxis(arr, 0, -1)
    ndvi, ndmi, water, obs = (arr[..., i] for i in range(4))
    fuel = classify(ndvi, water)
    retrieved = iso(utcnow())
    period = (start.date().isoformat(), end.date().isoformat())
    cdir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, ndvi=ndvi, ndmi=ndmi, water_share=water, obs=obs, fuel=fuel,
                        retrieved_at=np.array(retrieved), period_from=np.array(period[0]), period_to=np.array(period[1]))
    return {"ndvi": ndvi, "ndmi": ndmi, "water_share": water, "obs": obs, "fuel": fuel,
            "retrieved_at": retrieved, "period": list(period), "cached": False}


def fuel_summary(fuel: np.ndarray, obs: np.ndarray | None = None) -> dict:
    n = fuel.size
    out = {FUEL_NAMES[k]: round(float((fuel == k).sum()) / n, 3) for k in FUEL_NAMES}
    if obs is not None:
        out["no_clear_observation"] = round(float((np.nan_to_num(obs) == 0).sum()) / n, 3)
    return out
