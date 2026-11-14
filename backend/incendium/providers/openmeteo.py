"""Open-Meteo forecast API client (free, non-commercial tier, no key).

All values are numerical weather-model output (Open-Meteo "best match"
blend of ICON / GFS / ECMWF IFS and regional models), NOT station
observations. Past days come from the same model archive.

Free tier limits: 600 calls/min, 5 000/h, 10 000/day. A request is counted as
more than one call when it spans > 2 weeks or > 10 variables; how multiple
locations in one request are counted is not documented, so Incendium budgets
conservatively as if every location were a separate call.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import httpx

from .http import ProviderError, request_with_retry

BASE = "https://api.open-meteo.com/v1/forecast"
PROVIDER = "Open-Meteo"

HOURLY_VARS = (
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "precipitation",
    "vapour_pressure_deficit",
)

EXPECTED_UNITS = {
    "temperature_2m": "°C",
    "relative_humidity_2m": "%",
    "wind_speed_10m": "km/h",
    "wind_gusts_10m": "km/h",
    "wind_direction_10m": "°",
    "precipitation": "mm",
    "vapour_pressure_deficit": "kPa",
}


@dataclass(frozen=True)
class LocationSeries:
    lat_requested: float
    lon_requested: float
    model_lat: float
    model_lon: float
    elevation: float | None
    timezone: str
    utc_offset_seconds: int
    time_local: list[str]  # "YYYY-MM-DDTHH:MM" local time
    values: dict[str, list[float | None]]


def estimated_cost(n_locations: int, days: int, n_vars: int = len(HOURLY_VARS)) -> float:
    return n_locations * max(1.0, days / 14.0) * max(1.0, n_vars / 10.0)


ARCHIVE_BASE = "https://archive-api.open-meteo.com/v1/archive"


def _check_points(points: list[tuple[float, float]]) -> None:
    for lat, lon in points:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"invalid coordinate {lat},{lon}")


def _common_params(points: list[tuple[float, float]], tz: str) -> dict:
    return {
        "latitude": ",".join(f"{lat:.4f}" for lat, _ in points),
        "longitude": ",".join(f"{lon:.4f}" for _, lon in points),
        "hourly": ",".join(HOURLY_VARS),
        "timezone": tz,
        "wind_speed_unit": "kmh",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
        "timeformat": "iso8601",
    }


def fetch_archive(
    client: httpx.Client,
    points: list[tuple[float, float]],
    start: str,
    end: str,
    tz: str,
    model: str = "era5",
) -> list[LocationSeries]:
    """Historical reanalysis (ERA5 by default, 0.25°, ~5-day delay) for validation."""
    if not points:
        return []
    _check_points(points)
    params = {**_common_params(points, tz), "start_date": start, "end_date": end, "models": model}
    resp = request_with_retry(client, PROVIDER, "GET", ARCHIVE_BASE, params=params)
    return _parse(resp, points, tz)


def fetch_hourly(
    client: httpx.Client,
    points: list[tuple[float, float]],
    past_days: int,
    forecast_days: int,
    tz: str,
) -> list[LocationSeries]:
    if not points:
        return []
    if not (0 <= past_days <= 92 and 1 <= forecast_days <= 16):
        raise ValueError("past_days must be 0..92 and forecast_days 1..16")
    _check_points(points)
    params = {**_common_params(points, tz), "past_days": past_days, "forecast_days": forecast_days}
    resp = request_with_retry(client, PROVIDER, "GET", BASE, params=params)
    return _parse(resp, points, tz)


def _parse(resp: httpx.Response, points: list[tuple[float, float]], tz: str) -> list[LocationSeries]:
    if resp.status_code != 200:
        reason = ""
        try:
            reason = resp.json().get("reason", "")
        except ValueError:
            pass
        raise ProviderError(PROVIDER, f"HTTP {resp.status_code} {reason}".strip(), resp.status_code)
    payload = resp.json()
    items = payload if isinstance(payload, list) else [payload]
    if len(items) != len(points):
        raise ProviderError(PROVIDER, f"expected {len(points)} locations, got {len(items)}")
    out: list[LocationSeries] = []
    for (lat, lon), item in zip(points, items):
        hourly = item.get("hourly") or {}
        units = item.get("hourly_units") or {}
        for var, unit in EXPECTED_UNITS.items():
            if units.get(var) not in (None, unit):
                raise ProviderError(PROVIDER, f"unexpected unit for {var}: {units.get(var)}")
        times = hourly.get("time")
        if not isinstance(times, list) or not times:
            raise ProviderError(PROVIDER, "response has no hourly time axis")
        values: dict[str, list[float | None]] = {}
        for var in HOURLY_VARS:
            series = hourly.get(var)
            if not isinstance(series, list) or len(series) != len(times):
                values[var] = [None] * len(times)
            else:
                values[var] = [None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v) for v in series]
        out.append(
            LocationSeries(
                lat_requested=lat,
                lon_requested=lon,
                model_lat=float(item.get("latitude", lat)),
                model_lon=float(item.get("longitude", lon)),
                elevation=item.get("elevation"),
                timezone=item.get("timezone", tz),
                utc_offset_seconds=int(item.get("utc_offset_seconds", 0)),
                time_local=list(times),
                values=values,
            )
        )
    return out
