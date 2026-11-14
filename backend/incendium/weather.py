"""Derive daily fire-weather inputs from hourly model series.

FWI convention: weather at local noon, and rain accumulated over the 24 hours
ending at noon. Open-Meteo hourly ``precipitation`` is the sum over the
*preceding* hour, so the 24 h window ending at 12:00 on day D is the values
time-stamped D-1 13:00 ... D 12:00 inclusive.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta


def _parse_local(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def derive_daily(time_local: list[str], values: dict[str, list[float | None]], now_local: datetime) -> list[dict]:
    idx = {_parse_local(t): i for i, t in enumerate(time_local)}

    def val(var: str, t: datetime) -> float | None:
        i = idx.get(t)
        if i is None:
            return None
        return values[var][i]

    by_day: dict[date, list[datetime]] = defaultdict(list)
    for t in idx:
        by_day[t.date()].append(t)

    out: list[dict] = []
    for d in sorted(by_day):
        noon = datetime(d.year, d.month, d.day, 12, 0)
        if noon not in idx:
            continue
        window = [noon - timedelta(hours=h) for h in range(23, -1, -1)]  # D-1 13:00 .. D 12:00
        rain_vals = [val("precipitation", t) for t in window]
        rain24 = None if any(v is None for v in rain_vals) else round(sum(rain_vals), 2)  # type: ignore[arg-type]

        hours = sorted(by_day[d])

        def agg(var: str, fn):
            xs = [values[var][idx[t]] for t in hours if values[var][idx[t]] is not None]
            return round(fn(xs), 2) if xs else None

        out.append(
            {
                "date": d.isoformat(),
                "t_noon": val("temperature_2m", noon),
                "rh_noon": val("relative_humidity_2m", noon),
                "ws_noon": val("wind_speed_10m", noon),
                "wd_noon": val("wind_direction_10m", noon),
                "rain24_noon": rain24,
                "t_max": agg("temperature_2m", max),
                "rh_min": agg("relative_humidity_2m", min),
                "ws_max": agg("wind_speed_10m", max),
                "gust_max": agg("wind_gusts_10m", max),
                "vpd_max": agg("vapour_pressure_deficit", max),
                "precip_day": agg("precipitation", sum),
                "hours": len(hours),
                "is_forecast": int(noon > now_local),
            }
        )
    return out
