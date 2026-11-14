"""NASA FIRMS Area API client (VIIRS 375 m active fire / thermal anomaly product).

Endpoint: /api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north}/{DAY_RANGE}[/{DATE}]
  - DAY_RANGE 1..5
  - CSV only
  - acq_time is UTC (HHMM)
  - VIIRS confidence: l / n / h
Limit: 5000 transactions per 10 minutes per MAP_KEY.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

from ..log import mask
from .http import ProviderError, request_with_retry

log = logging.getLogger(__name__)

BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
PROVIDER = "NASA FIRMS"

REQUIRED_COLUMNS = {"latitude", "longitude", "acq_date", "acq_time", "satellite", "confidence"}
CONFIDENCE_MAP = {"l": "low", "n": "nominal", "h": "high", "low": "low", "nominal": "nominal", "high": "high"}
SATELLITE_NAMES = {"N": "Suomi NPP", "N20": "NOAA-20", "N21": "NOAA-21", "1": "NOAA-20", "Terra": "Terra", "Aqua": "Aqua"}


@dataclass(frozen=True)
class Detection:
    id: str
    source: str
    satellite: str
    instrument: str
    latitude: float
    longitude: float
    acq_datetime: datetime
    confidence: str | None
    frp: float | None
    bright_ti4: float | None
    bright_ti5: float | None
    scan: float | None
    track: float | None
    daynight: str | None
    version: str | None


def _float(v: str | None) -> float | None:
    if v is None or v.strip() == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def detection_id(satellite: str, acq: datetime, lat: float, lon: float) -> str:
    """Observation identity: platform + overpass minute + position rounded to ~11 m.

    Two rows with the same platform, acquisition minute and pixel-centre
    coordinates are the same observation (e.g. returned by overlapping day
    ranges or re-processing), so they collapse to one record.
    """
    return f"{satellite}|{acq.strftime('%Y%m%dT%H%M')}|{lat:.4f}|{lon:.4f}"


def parse_csv(text: str, source: str, bbox: tuple[float, float, float, float]) -> tuple[list[Detection], int]:
    """Parse a FIRMS CSV body. Returns (detections, rejected_row_count)."""
    stripped = text.strip()
    if not stripped:
        return [], 0
    first_line = stripped.splitlines()[0]
    if "latitude" not in first_line.lower():
        # FIRMS returns plain-text errors with HTTP 200 (e.g. "Invalid MAP_KEY.").
        raise ProviderError(PROVIDER, f"unexpected response: {mask(first_line[:160])}")
    reader = csv.DictReader(io.StringIO(stripped))
    cols = {c.strip().lower() for c in (reader.fieldnames or [])}
    missing = REQUIRED_COLUMNS - cols
    if missing:
        raise ProviderError(PROVIDER, f"CSV missing columns: {sorted(missing)}")
    w, s, e, n = bbox
    out: dict[str, Detection] = {}
    rejected = 0
    for row in reader:
        row = {k.strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
        lat, lon = _float(row.get("latitude")), _float(row.get("longitude"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            rejected += 1
            continue
        if not (w - 0.01 <= lon <= e + 0.01 and s - 0.01 <= lat <= n + 0.01):
            rejected += 1
            continue
        try:
            d = date.fromisoformat(row["acq_date"])
            t = (row.get("acq_time") or "").zfill(4)
            hh, mm = int(t[:2]), int(t[2:4])
            acq = datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone.utc)
        except (ValueError, KeyError):
            rejected += 1
            continue
        sat_raw = row.get("satellite") or ""
        satellite = SATELLITE_NAMES.get(sat_raw, sat_raw or "unknown")
        conf_raw = (row.get("confidence") or "").lower()
        det = Detection(
            id=detection_id(satellite, acq, lat, lon),
            source=source,
            satellite=satellite,
            instrument=row.get("instrument") or "VIIRS",
            latitude=lat,
            longitude=lon,
            acq_datetime=acq,
            confidence=CONFIDENCE_MAP.get(conf_raw),
            frp=_float(row.get("frp")),
            bright_ti4=_float(row.get("bright_ti4")),
            bright_ti5=_float(row.get("bright_ti5")),
            scan=_float(row.get("scan")),
            track=_float(row.get("track")),
            daynight=row.get("daynight") or None,
            version=row.get("version") or None,
        )
        out[det.id] = det
    return list(out.values()), rejected


def fetch_area(
    client: httpx.Client,
    map_key: str,
    source: str,
    bbox: tuple[float, float, float, float],
    day_range: int,
    start_date: date | None = None,
) -> tuple[list[Detection], int]:
    if not 1 <= day_range <= 5:
        raise ValueError("FIRMS day_range must be 1..5")
    area = ",".join(f"{v:g}" for v in bbox)
    url = f"{BASE}/{map_key}/{source}/{area}/{day_range}"
    if start_date is not None:
        url += f"/{start_date.isoformat()}"
    resp = request_with_retry(client, PROVIDER, "GET", url)
    if resp.status_code != 200:
        raise ProviderError(PROVIDER, f"HTTP {resp.status_code}: {mask(resp.text[:160])}", resp.status_code)
    return parse_csv(resp.text, source, bbox)
