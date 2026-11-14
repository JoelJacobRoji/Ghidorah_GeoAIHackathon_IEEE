"""Canadian Forest Fire Weather Index (FWI) System.

Implemented from the published equations:
  Van Wagner, C.E. (1987). Development and structure of the Canadian Forest
  Fire Weather Index System. Canadian Forestry Service, Forestry Technical
  Report 35.
  Van Wagner, C.E. & Pickett, T.L. (1985). Equations and FORTRAN program for
  the Canadian Forest Fire Weather Index System. Forestry Technical Report 33.

Latitude-dependent day-length factors follow the Global Fire WEather Database
(GFWED; Field et al. 2015) convention, so that the DMC and DC behave sensibly
at tropical latitudes. GFWED is the FWI source the Forest Survey of India
reports using. The test suite cross-checks this module against
`xclim.compute.fire.cffwis_indices`, which uses the same convention.

Daily inputs (all at local solar noon, LST):
  temp  - air temperature, deg C
  rh    - relative humidity, %
  wind  - 10 m wind speed, km/h
  rain  - precipitation accumulated over the previous 24 h, mm
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

FFMC_START = 85.0
DMC_START = 6.0
DC_START = 15.0

# Effective day length (hours) for DMC by month, per latitude band (GFWED).
_EL_N_TEMPERATE = (6.5, 7.5, 9.0, 12.8, 13.9, 13.9, 12.4, 10.9, 9.4, 8.0, 7.0, 6.0)  # lat >= 30
_EL_N_TROPIC = (7.9, 8.4, 8.9, 9.5, 9.9, 10.2, 10.1, 9.7, 9.1, 8.6, 8.1, 7.8)  # 15 <= lat < 30
_EL_EQUATOR = 9.0  # -15 <= lat < 15
_EL_S_TROPIC = (10.1, 9.6, 9.1, 8.5, 8.1, 7.8, 7.9, 8.3, 8.9, 9.4, 9.9, 10.2)  # -30 <= lat < -15
_EL_S_TEMPERATE = (11.5, 10.5, 9.2, 7.9, 6.8, 6.2, 6.5, 7.4, 8.7, 10.0, 11.2, 11.8)  # lat < -30

# Day-length adjustment for DC by month (GFWED).
_FL_N = (-1.6, -1.6, -1.6, 0.9, 3.8, 5.8, 6.4, 5.0, 2.4, 0.4, -1.6, -1.6)  # lat >= 15
_FL_S = (6.4, 5.0, 2.4, 0.4, -1.6, -1.6, -1.6, -1.6, -1.6, 0.9, 3.8, 5.8)  # lat < -15
_FL_EQ = 1.39  # -15 <= lat < 15


def dmc_day_length(lat: float, month: int) -> float:
    m = month - 1
    if lat >= 30:
        return _EL_N_TEMPERATE[m]
    if lat >= 15:
        return _EL_N_TROPIC[m]
    if lat >= -15:
        return _EL_EQUATOR
    if lat >= -30:
        return _EL_S_TROPIC[m]
    return _EL_S_TEMPERATE[m]


def dc_day_length(lat: float, month: int) -> float:
    if lat >= 15:
        return _FL_N[month - 1]
    if lat < -15:
        return _FL_S[month - 1]
    return _FL_EQ


def ffmc(temp: float, rh: float, wind: float, rain: float, ffmc_prev: float) -> float:
    rh = min(max(rh, 0.0), 100.0)
    wind = max(wind, 0.0)
    rain = max(rain, 0.0)
    mo = 147.2 * (101.0 - ffmc_prev) / (59.5 + ffmc_prev)
    if rain > 0.5:
        rf = rain - 0.5
        if mo > 150.0:
            mo = (
                mo
                + 42.5 * rf * math.exp(-100.0 / (251.0 - mo)) * (1.0 - math.exp(-6.93 / rf))
                + 0.0015 * (mo - 150.0) ** 2 * math.sqrt(rf)
            )
        else:
            mo = mo + 42.5 * rf * math.exp(-100.0 / (251.0 - mo)) * (1.0 - math.exp(-6.93 / rf))
        mo = min(mo, 250.0)
    ed = 0.942 * rh**0.679 + 11.0 * math.exp((rh - 100.0) / 10.0) + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh))
    if mo > ed:
        ko = 0.424 * (1.0 - (rh / 100.0) ** 1.7) + 0.0694 * math.sqrt(wind) * (1.0 - (rh / 100.0) ** 8)
        kd = ko * 0.581 * math.exp(0.0365 * temp)
        m = ed + (mo - ed) * 10.0 ** (-kd)
    else:
        ew = 0.618 * rh**0.753 + 10.0 * math.exp((rh - 100.0) / 10.0) + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh))
        if mo < ew:
            k1 = 0.424 * (1.0 - ((100.0 - rh) / 100.0) ** 1.7) + 0.0694 * math.sqrt(wind) * (1.0 - ((100.0 - rh) / 100.0) ** 8)
            kw = k1 * 0.581 * math.exp(0.0365 * temp)
            m = ew - (ew - mo) * 10.0 ** (-kw)
        else:
            m = mo
    value = 59.5 * (250.0 - m) / (147.2 + m)
    return min(max(value, 0.0), 101.0)


def dmc(temp: float, rh: float, rain: float, dmc_prev: float, lat: float, month: int) -> float:
    rh = min(max(rh, 0.0), 100.0)
    rain = max(rain, 0.0)
    t = max(temp, -1.1)
    rk = 1.894 * (t + 1.1) * (100.0 - rh) * dmc_day_length(lat, month) * 1e-4
    if rain > 1.5:
        re = 0.92 * rain - 1.27
        mo = 20.0 + math.exp(5.6348 - dmc_prev / 43.43)
        if dmc_prev <= 33.0:
            b = 100.0 / (0.5 + 0.3 * dmc_prev)
        elif dmc_prev <= 65.0:
            b = 14.0 - 1.3 * math.log(dmc_prev)
        else:
            b = 6.2 * math.log(dmc_prev) - 17.2
        mr = mo + 1000.0 * re / (48.77 + b * re)
        pr = 244.72 - 43.43 * math.log(mr - 20.0)
        pr = max(pr, 0.0)
    else:
        pr = dmc_prev
    return max(pr + rk, 0.0)


def dc(temp: float, rain: float, dc_prev: float, lat: float, month: int) -> float:
    rain = max(rain, 0.0)
    t = max(temp, -2.8)
    pe = (0.36 * (t + 2.8) + dc_day_length(lat, month)) / 2.0
    pe = max(pe, 0.0)
    if rain > 2.8:
        rd = 0.83 * rain - 1.27
        qo = 800.0 * math.exp(-dc_prev / 400.0)
        qr = qo + 3.937 * rd
        dr = 400.0 * math.log(800.0 / qr)
        dr = max(dr, 0.0)
        return dr + pe
    return dc_prev + pe


def isi(wind: float, ffmc_today: float) -> float:
    m = 147.2 * (101.0 - ffmc_today) / (59.5 + ffmc_today)
    f_w = math.exp(0.05039 * max(wind, 0.0))
    f_f = 91.9 * math.exp(-0.1386 * m) * (1.0 + m**5.31 / 4.93e7)
    return 0.208 * f_w * f_f


def bui(dmc_today: float, dc_today: float) -> float:
    if dmc_today == 0.0 and dc_today == 0.0:
        return 0.0
    if dmc_today <= 0.4 * dc_today:
        value = 0.8 * dmc_today * dc_today / (dmc_today + 0.4 * dc_today)
    else:
        value = dmc_today - (1.0 - 0.8 * dc_today / (dmc_today + 0.4 * dc_today)) * (0.92 + (0.0114 * dmc_today) ** 1.7)
    return max(value, 0.0)


def fwi(isi_today: float, bui_today: float) -> float:
    if bui_today <= 80.0:
        f_d = 0.626 * bui_today**0.809 + 2.0
    else:
        f_d = 1000.0 / (25.0 + 108.64 * math.exp(-0.023 * bui_today))
    b = 0.1 * isi_today * f_d
    if b > 1.0:
        return math.exp(2.72 * (0.434 * math.log(b)) ** 0.647)
    return b


def dsr(fwi_today: float) -> float:
    return 0.0272 * fwi_today**1.77


@dataclass(frozen=True)
class DailyInput:
    date: str  # YYYY-MM-DD (local)
    temp: float | None
    rh: float | None
    wind: float | None
    rain: float | None
    is_forecast: bool = False


@dataclass(frozen=True)
class DailyFWI:
    date: str
    ffmc: float | None
    dmc: float | None
    dc: float | None
    isi: float | None
    bui: float | None
    fwi: float | None
    dsr: float | None
    is_forecast: bool
    spinup: bool
    status: str  # ok | missing_inputs


def run_series(
    inputs: Sequence[DailyInput],
    lat: float,
    spinup_days: int = 45,
    start: tuple[float, float, float] = (FFMC_START, DMC_START, DC_START),
) -> list[DailyFWI]:
    """Run the FWI system over a *contiguous* daily series.

    If any input for a day is missing, that day and every later day are marked
    ``missing_inputs`` (the moisture codes cannot be carried forward honestly).
    The first ``spinup_days`` results are flagged as spin-up.
    """
    out: list[DailyFWI] = []
    f_prev, p_prev, d_prev = start
    broken = False
    for idx, d in enumerate(inputs):
        month = int(d.date[5:7])
        spin = idx < spinup_days
        if broken or None in (d.temp, d.rh, d.wind, d.rain):
            broken = True
            out.append(DailyFWI(d.date, None, None, None, None, None, None, None, d.is_forecast, spin, "missing_inputs"))
            continue
        f = ffmc(d.temp, d.rh, d.wind, d.rain, f_prev)  # type: ignore[arg-type]
        p = dmc(d.temp, d.rh, d.rain, p_prev, lat, month)  # type: ignore[arg-type]
        c = dc(d.temp, d.rain, d_prev, lat, month)  # type: ignore[arg-type]
        i = isi(d.wind, f)  # type: ignore[arg-type]
        b = bui(p, c)
        w = fwi(i, b)
        out.append(DailyFWI(d.date, f, p, c, i, b, w, dsr(w), d.is_forecast, spin, "ok"))
        f_prev, p_prev, d_prev = f, p, c
    return out


# Fire danger classes used by the European Forest Fire Information System
# (EFFIS). They are calibrated for Europe, NOT for India; Incendium labels them
# "prototype" everywhere they are displayed.
FWI_CLASSES: tuple[tuple[str, float, float], ...] = (
    ("Low", 0.0, 11.2),
    ("Moderate", 11.2, 21.3),
    ("High", 21.3, 38.0),
    ("Very High", 38.0, 50.0),
    ("Extreme", 50.0, 70.0),
    ("Very Extreme", 70.0, math.inf),
)


ClassScheme = tuple[tuple[str, float, float], ...]


def fwi_class(value: float | None, classes: ClassScheme = FWI_CLASSES) -> str | None:
    if value is None:
        return None
    for name, lo, hi in classes:
        if lo <= value < hi:
            return name
    return classes[-1][0]


# ---------------------------------------------------------------------------- regional calibration
# Pre-registered method (fixed before any 2025 data was examined):
#   * Class boundaries are percentiles of the pooled distribution of daily FWI
#     over every cell-day of one reference fire season (Feb-May) in the region.
#   * Percentiles 25 / 50 / 75 / 90 / 97 separate the six classes, so in the
#     reference season the classes cover 25 / 25 / 25 / 15 / 7 / 3 % of cell-days.
#   * Only FWI values are used. Detection data are NOT used to set thresholds;
#     they are used afterwards, on a different season, to test the classes.
CALIBRATION_PERCENTILES: tuple[float, ...] = (25.0, 50.0, 75.0, 90.0, 97.0)
CLASS_NAMES: tuple[str, ...] = tuple(n for n, _, _ in FWI_CLASSES)
CALIBRATION_FILE = Path(__file__).resolve().parent / "data" / "fwi_calibration.json"


def scheme_from_thresholds(thresholds: Iterable[float]) -> ClassScheme:
    th = [float(x) for x in thresholds]
    if len(th) != len(CLASS_NAMES) - 1 or any(b <= a for a, b in zip(th, th[1:])) or th[0] <= 0:
        raise ValueError(f"need {len(CLASS_NAMES) - 1} strictly increasing positive thresholds, got {th}")
    bounds = [0.0, *th, math.inf]
    return tuple((n, bounds[i], bounds[i + 1]) for i, n in enumerate(CLASS_NAMES))


def calibrate_thresholds(values: Iterable[float], percentiles: tuple[float, ...] = CALIBRATION_PERCENTILES) -> list[float]:
    """Percentile thresholds from FWI values (linear interpolation, NaN/None ignored)."""
    import numpy as np

    arr = np.array([v for v in values if v is not None], dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size < 1000:
        raise ValueError(f"too few FWI values to calibrate ({arr.size})")
    return [round(float(x), 2) for x in np.percentile(arr, percentiles)]


def load_calibrations(path: Path | None = None) -> dict:
    p = path or CALIBRATION_FILE
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def class_scheme(region: str | None, path: Path | None = None) -> tuple[ClassScheme, dict | None]:
    """Classes for a region: its calibration if one exists, else the EFFIS prototype."""
    cal = load_calibrations(path).get(region or "") if region else None
    if cal:
        return scheme_from_thresholds(cal["thresholds"]), cal
    return FWI_CLASSES, None


def contiguous(dates: Iterable[str]) -> bool:
    from datetime import date

    ds = [date.fromisoformat(x) for x in dates]
    return all((b - a).days == 1 for a, b in zip(ds, ds[1:]))
