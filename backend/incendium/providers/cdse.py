"""Copernicus Data Space Ecosystem: Sentinel Hub Statistical API client.

Computes per-cell NDVI and NDMI statistics from Sentinel-2 L2A surface
reflectance with Scene Classification (SCL) masking. Raster processing runs
on the Copernicus side; only summary statistics are transferred.

  NDVI = (B08 - B04) / (B08 + B04)   -> vegetation greenness / photosynthetic activity
  NDMI = (B8A - B11) / (B8A + B11)   -> canopy / vegetation water content proxy

Pixels are excluded when SCL is 0 (no data), 1 (saturated/defective),
3 (cloud shadow), 6 (water), 8/9 (cloud medium/high probability),
10 (thin cirrus) or 11 (snow/ice).

Free quota (Copernicus General Users): 10 000 requests and 10 000 processing
units per month; 300 per minute.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from .http import ProviderError, request_with_retry

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
STATS_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"
PROVIDER = "Copernicus Data Space"

EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B08", "B8A", "B11", "SCL", "dataMask"] }],
    output: [
      { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "ndmi", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}
const EXCLUDED = [0, 1, 3, 6, 8, 9, 10, 11];
function evaluatePixel(s) {
  const ndvi = (s.B08 - s.B04) / (s.B08 + s.B04);
  const ndmi = (s.B8A - s.B11) / (s.B8A + s.B11);
  const ok = s.dataMask === 1 && EXCLUDED.indexOf(s.SCL) === -1 && isFinite(ndvi) && isFinite(ndmi);
  return { ndvi: [ndvi], ndmi: [ndmi], dataMask: [ok ? 1 : 0] };
}
"""


@dataclass(frozen=True)
class VegInterval:
    interval_from: str
    interval_to: str
    ndvi_mean: float | None
    ndvi_std: float | None
    ndmi_mean: float | None
    ndmi_std: float | None
    valid_fraction: float | None
    sample_count: int | None


class TokenProvider:
    def __init__(self, client_id: str, client_secret: str):
        self._id = client_id
        self._secret = client_secret
        self._token: str | None = None
        self._expires: datetime = datetime.min.replace(tzinfo=timezone.utc)
        self._lock = threading.Lock()

    def get(self, client: httpx.Client) -> str:
        with self._lock:
            if self._token and datetime.now(timezone.utc) < self._expires - timedelta(seconds=60):
                return self._token
            resp = request_with_retry(
                client,
                PROVIDER,
                "POST",
                TOKEN_URL,
                data={"grant_type": "client_credentials", "client_id": self._id, "client_secret": self._secret},
                attempts=3,
            )
            if resp.status_code != 200:
                raise ProviderError(PROVIDER, f"token request failed (HTTP {resp.status_code})", resp.status_code)
            body = resp.json()
            self._token = body["access_token"]
            self._expires = datetime.now(timezone.utc) + timedelta(seconds=int(body.get("expires_in", 300)))
            return self._token


def build_request(polygon: list[list[float]], start: datetime, end: datetime, interval_days: int, res_deg: float) -> dict:
    return {
        "input": {
            "bounds": {
                "geometry": {"type": "Polygon", "coordinates": [polygon]},
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
            },
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {"mosaickingOrder": "leastCC"},
                }
            ],
        },
        "aggregation": {
            "timeRange": {
                "from": start.strftime("%Y-%m-%dT00:00:00Z"),
                "to": end.strftime("%Y-%m-%dT23:59:59Z"),
            },
            "aggregationInterval": {"of": f"P{interval_days}D", "lastIntervalBehavior": "SHORTEN"},
            "evalscript": EVALSCRIPT,
            "resx": res_deg,
            "resy": res_deg,
        },
        "calculations": {"default": {}},
    }


def _band_stats(outputs: dict, name: str) -> dict | None:
    try:
        return outputs[name]["bands"]["B0"]["stats"]
    except (KeyError, TypeError):
        return None


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN -> None


def parse_response(body: dict) -> list[VegInterval]:
    out: list[VegInterval] = []
    for item in body.get("data", []) or []:
        if item.get("error"):
            continue
        interval = item.get("interval") or {}
        outputs = item.get("outputs") or {}
        ndvi, ndmi = _band_stats(outputs, "ndvi"), _band_stats(outputs, "ndmi")
        if ndvi is None or ndmi is None:
            continue
        samples = ndvi.get("sampleCount")
        nodata = ndvi.get("noDataCount")
        valid_fraction = None
        if isinstance(samples, (int, float)) and samples > 0 and isinstance(nodata, (int, float)):
            valid_fraction = max(0.0, min(1.0, (samples - nodata) / samples))
        out.append(
            VegInterval(
                interval_from=str(interval.get("from", ""))[:10],
                interval_to=str(interval.get("to", ""))[:10],
                ndvi_mean=_num(ndvi.get("mean")),
                ndvi_std=_num(ndvi.get("stDev")),
                ndmi_mean=_num(ndmi.get("mean")),
                ndmi_std=_num(ndmi.get("stDev")),
                valid_fraction=valid_fraction,
                sample_count=int(samples) if isinstance(samples, (int, float)) else None,
            )
        )
    return out


def fetch_cell_stats(
    client: httpx.Client,
    tokens: TokenProvider,
    polygon: list[list[float]],
    start: datetime,
    end: datetime,
    interval_days: int = 10,
    res_deg: float = 0.002,
) -> list[VegInterval]:
    body = build_request(polygon, start, end, interval_days, res_deg)
    token = tokens.get(client)
    resp = request_with_retry(
        client, PROVIDER, "POST", STATS_URL, json=body, headers={"Authorization": f"Bearer {token}"}, attempts=4
    )
    if resp.status_code == 401:
        raise ProviderError(PROVIDER, "unauthorised (check CDSE client credentials)", 401)
    if resp.status_code != 200:
        detail = ""
        try:
            detail = str(resp.json().get("error", {}).get("message", ""))[:200]
        except ValueError:
            pass
        raise ProviderError(PROVIDER, f"HTTP {resp.status_code} {detail}".strip(), resp.status_code)
    return parse_response(resp.json())


class RateLimiter:
    """Simple minimum-interval limiter (thread-safe)."""

    def __init__(self, per_minute: int):
        self.interval = 60.0 / per_minute
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)
