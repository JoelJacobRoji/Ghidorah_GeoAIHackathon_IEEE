"""Module 4: fire spread scenario (illustrative, not a forecast).

Method
------
1. Fine fuel moisture: hourly FFMC (Van Wagner 1977 hourly equations, as in the
   cffdrs `hffmc` routine), started from the previous day's daily FFMC and
   driven by the cell's hourly model weather (Open-Meteo).
2. Head rate of spread per fuel class from the Canadian FBP System
   (Forestry Canada 1992, ST-X-3), using surrogate fuel types:
     grass  -> O-1a matted grass  ROS = 190 (1 - e^(-0.0310 ISI))^1.40 × CF(curing)
     forest -> D-1 leafless aspen ROS = 30 (1 - e^(-0.0232 ISI))^1.6 × BE(BUI)
     shrub  -> mean of the two
   ISI is computed hourly from the hourly FFMC and 10 m wind (FWI System eq. 24-26).
3. Wind shapes spread as an ellipse: length-to-breadth LB = 1 + 8.729 (1 - e^(-0.030 WS))^2.155
   (FBP eq. 79); ROS(θ) = ROS_head (1 - ε) / (1 - ε cos θ), ε = √(1 - 1/LB²).
4. Uphill spread is accelerated with the FBP slope factor SF = exp(3.533 (tan φ)^1.2)
   (tan φ capped at 0.7). Downhill spread is NOT slowed (conservative).
5. Arrival times are computed with a time-dependent shortest-path (Dijkstra) over a
   16-neighbour grid, so wind and moisture change hour by hour.

Limitations: surrogate Canadian fuel types (not calibrated for Indian fuels), no
spotting, no suppression, roads/fire lines are not barriers, crown fire is not
modelled, and model weather at ~10-25 km is applied uniformly to the window.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from shapely.geometry import box, mapping
from shapely.ops import unary_union

from . import fwi as fwi_mod
from .fuel import FUEL_NAMES, fetch_fuel, fuel_summary
from .terrain import Grid, grid_around, sample_dem

MAX_HOURS = 12
SHAPELY_SIMPLIFY_FRAC = 0.6


# ---------------------------------------------------------------------------- fire behaviour equations
def hourly_ffmc(temp: float, rh: float, wind: float, rain: float, ffmc_prev: float) -> float:
    """One-hour FFMC step (Van Wagner 1977; cffdrs hffmc)."""
    rh = min(max(rh, 0.0), 100.0)
    mo = 147.27723 * (101.0 - ffmc_prev) / (59.5 + ffmc_prev)
    if rain > 0:
        mo = mo + 42.5 * rain * math.exp(-100.0 / (251.0 - mo)) * (1.0 - math.exp(-6.93 / rain))
        if mo > 150:
            mo += 0.0015 * (mo - 150.0) ** 2 * math.sqrt(rain)
        mo = min(mo, 250.0)
    ed = 0.942 * rh ** 0.679 + 11.0 * math.exp((rh - 100.0) / 10.0) + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh))
    ew = 0.618 * rh ** 0.753 + 10.0 * math.exp((rh - 100.0) / 10.0) + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh))
    if mo > ed:
        ko = 0.424 * (1.0 - (rh / 100.0) ** 1.7) + 0.0694 * math.sqrt(wind) * (1.0 - (rh / 100.0) ** 8)
        k = ko * 0.0579 * math.exp(0.0365 * temp)
        m = ed + (mo - ed) * 10.0 ** (-k)
    elif mo < ew:
        k1 = 0.424 * (1.0 - ((100.0 - rh) / 100.0) ** 1.7) + 0.0694 * math.sqrt(wind) * (1.0 - ((100.0 - rh) / 100.0) ** 8)
        k = k1 * 0.0579 * math.exp(0.0365 * temp)
        m = ew - (ew - mo) * 10.0 ** (-k)
    else:
        m = mo
    return max(0.0, min(101.0, 59.5 * (250.0 - m) / (147.27723 + m)))


def curing_factor(curing_pct: float) -> float:
    c = curing_pct
    return 0.005 * (math.exp(0.061 * c) - 1.0) if c < 58.8 else 0.176 + 0.02 * (c - 58.8)


def ros_o1a(isi: float, curing_pct: float) -> float:
    return 190.0 * (1.0 - math.exp(-0.0310 * isi)) ** 1.40 * curing_factor(curing_pct)


def ros_d1(isi: float, bui: float | None) -> float:
    ros = 30.0 * (1.0 - math.exp(-0.0232 * isi)) ** 1.6
    if bui and bui > 0:
        ros *= math.exp(50.0 * math.log(0.9) * (1.0 / bui - 1.0 / 32.0))
    return ros


def head_ros_by_fuel(isi: float, bui: float | None, curing_pct: float) -> np.ndarray:
    """Head ROS (m/min) indexed by fuel class 0..3."""
    g = ros_o1a(isi, curing_pct)
    f = ros_d1(isi, bui)
    return np.array([0.0, g, 0.5 * (g + f), f], dtype=np.float64)


def length_to_breadth(ws_kmh: float) -> float:
    return 1.0 + 8.729 * (1.0 - math.exp(-0.030 * ws_kmh)) ** 2.155


def ellipse_factor(theta_rad: np.ndarray | float, lb: float):
    eps = math.sqrt(max(0.0, 1.0 - 1.0 / (lb * lb)))
    return (1.0 - eps) / (1.0 - eps * np.cos(theta_rad))


def slope_factor(tan_phi: float) -> float:
    if tan_phi <= 0:
        return 1.0
    return math.exp(3.533 * min(tan_phi, 0.7) ** 1.2)


# ---------------------------------------------------------------------------- shortest-path spread
# 16-neighbour stencil (dr, dc)
STENCIL = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1),
           (-2, -1), (-2, 1), (2, -1), (2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2)]


@dataclass
class HourState:
    head_ros: np.ndarray       # by fuel class, m/min
    spread_dir_deg: float      # direction fire is pushed TOWARDS (clockwise from north)
    lb: float
    isi: float
    ffmc: float
    ws: float
    wd: float
    temp: float
    rh: float
    time_local: str


def arrival_times(fuel: np.ndarray, elev: np.ndarray, dx: float, dy: float, start: tuple[int, int],
                  hours: list[HourState], max_minutes: float) -> np.ndarray:
    """Minutes from ignition to each cell (inf if not reached within max_minutes)."""
    ny, nx = fuel.shape
    t = np.full((ny, nx), np.inf)
    if fuel[start] == 0:
        return t
    # Per-edge geometry: distance and compass bearing of each stencil step.
    geo = []
    for dr, dc in STENCIL:
        dist = math.hypot(dr * dy, dc * dx)
        bearing = math.degrees(math.atan2(dc * dx, -dr * dy)) % 360.0  # -dr: row up = north
        sr, sc = (dr > 0) - (dr < 0), (dc > 0) - (dc < 0)
        if abs(dr) == 2:      # knight move: path crosses (r+sr, c) and (r+sr, c+dc)
            via, need_all = ((sr, 0), (sr, dc)), True
        elif abs(dc) == 2:    # knight move: path crosses (r, c+sc) and (r+dr, c+sc)
            via, need_all = ((0, sc), (dr, sc)), True
        elif dr and dc:       # diagonal: blocked only if both side cells are non-fuel (no corner cutting)
            via, need_all = ((dr, 0), (0, dc)), False
        else:
            via, need_all = (), True
        geo.append((dr, dc, dist, bearing, via, need_all))
    # Per-hour directional factor for every stencil step.
    efac = []
    for h in hours:
        efac.append([float(ellipse_factor(math.radians(g[3] - h.spread_dir_deg), h.lb)) for g in geo])
    nh = len(hours)
    t[start] = 0.0
    heap = [(0.0, start[0], start[1])]
    fuel_i = fuel.astype(np.int64)
    while heap:
        tu, r, c = heapq.heappop(heap)
        if tu > t[r, c] or tu > max_minutes:
            continue
        hi = min(int(tu // 60), nh - 1)
        hrs = hours[hi].head_ros
        ru = hrs[fuel_i[r, c]]
        eu = elev[r, c]
        ef = efac[hi]
        for k, (dr, dc, dist, _, via, need_all) in enumerate(geo):
            rr, cc = r + dr, c + dc
            if rr < 0 or cc < 0 or rr >= ny or cc >= nx:
                continue
            fv = fuel_i[rr, cc]
            if fv == 0:
                continue
            if via:
                ok_via = [fuel_i[r + a, c + b] > 0 for a, b in via]
                if (need_all and not all(ok_via)) or (not need_all and not any(ok_via)):
                    continue
            ros = 0.5 * (ru + hrs[fv]) * ef[k] * slope_factor((elev[rr, cc] - eu) / dist)
            if ros <= 1e-6:
                continue
            tv = tu + dist / ros
            if tv < t[rr, cc] and tv <= max_minutes:
                t[rr, cc] = tv
                heapq.heappush(heap, (tv, rr, cc))
    return t


def mask_to_polygon(mask: np.ndarray, grid: Grid):
    """Union of row runs of True cells as lon/lat polygons."""
    boxes = []
    for r in range(mask.shape[0]):
        row = mask[r]
        if not row.any():
            continue
        north = grid.north - r * grid.dlat
        south = north - grid.dlat
        idx = np.flatnonzero(np.diff(np.concatenate([[0], row.astype(np.int8), [0]])))
        for a, b in zip(idx[::2], idx[1::2]):
            boxes.append(box(grid.west + a * grid.dlon, south, grid.west + b * grid.dlon, north))
    if not boxes:
        return None
    return unary_union(boxes).simplify(min(grid.dlon, grid.dlat) * SHAPELY_SIMPLIFY_FRAC, preserve_topology=True)


# ---------------------------------------------------------------------------- scenario driver
@dataclass
class ScenarioInputs:
    lon: float
    lat: float
    start_local: datetime
    hours: int
    curing_pct: float
    weather: list[dict]            # hourly rows from start-of-previous-noon .. end
    ffmc_prev_day: float           # daily FFMC of the day before the start date
    bui: float | None
    cell_id: str


def build_hours(inp: ScenarioInputs) -> tuple[list[HourState], float]:
    """Hourly states for the simulated hours; FFMC is stepped hourly from the previous noon."""
    start_key = inp.start_local.strftime("%Y-%m-%dT%H:%M")
    f = inp.ffmc_prev_day
    states: list[HourState] = []
    for row in inp.weather:
        vals = (row.get("t2m"), row.get("rh2m"), row.get("ws10"), row.get("precip"))
        if any(v is None for v in vals):
            continue
        f = hourly_ffmc(vals[0], vals[1], vals[2], vals[3] or 0.0, f)
        if row["time_local"] >= start_key and len(states) < inp.hours:
            isi = fwi_mod.isi(vals[2], f)
            states.append(HourState(
                head_ros=head_ros_by_fuel(isi, inp.bui, inp.curing_pct),
                spread_dir_deg=((row.get("wd10") or 0.0) + 180.0) % 360.0,
                lb=length_to_breadth(vals[2]), isi=isi, ffmc=f, ws=vals[2], wd=row.get("wd10") or 0.0,
                temp=vals[0], rh=vals[1], time_local=row["time_local"],
            ))
    if not states:
        raise ValueError("no hourly weather available for the requested start time")
    max_head = max(float(s.head_ros.max()) for s in states)
    return states, max_head


def run_scenario(inp: ScenarioInputs, settlements_fn=None, stations_fn=None) -> dict:
    states, max_head = build_hours(inp)
    duration_min = 60.0 * len(states)
    # Window: generous radius for the fastest head spread, bounded for responsiveness.
    radius = min(20_000.0, max(2_500.0, 1.15 * max_head * duration_min))
    res = max(40.0, 2 * radius / 220)
    grid = grid_around(inp.lon, inp.lat, radius, res)
    fuel_data = fetch_fuel(grid)
    fuel = fuel_data["fuel"]
    elev = sample_dem(grid, 12 if res < 60 else 11)
    start = grid.index(inp.lon, inp.lat)
    ignition_fuel = int(fuel[start])
    if ignition_fuel == 0:
        # Move to the nearest burnable cell within ~3 pixels (the click may land on a road or stream).
        rr, cc = np.nonzero(fuel[max(0, start[0] - 3):start[0] + 4, max(0, start[1] - 3):start[1] + 4])
        if rr.size:
            k = int(np.argmin((rr - min(3, start[0])) ** 2 + (cc - min(3, start[1])) ** 2))
            start = (max(0, start[0] - 3) + int(rr[k]), max(0, start[1] - 3) + int(cc[k]))
            ignition_fuel = int(fuel[start])
    t = arrival_times(fuel, elev, grid.dx_m, grid.dy_m, start, states, duration_min)
    cell_area_ha = grid.dx_m * grid.dy_m / 10_000.0

    features = []
    hourly = []
    for h in range(1, len(states) + 1):
        m = t <= 60.0 * h
        area = float(m.sum()) * cell_area_ha
        hourly.append({"hour": h, "time_local": states[h - 1].time_local, "area_ha": round(area, 1)})
        poly = mask_to_polygon(m, grid)
        if poly is not None and not poly.is_empty:
            features.append({"type": "Feature", "geometry": mapping(poly),
                             "properties": {"hour": h, "area_ha": round(area, 1), "time_local": states[h - 1].time_local}})
    reached = np.isfinite(t)
    final_poly = mask_to_polygon(reached, grid) if reached.any() else None

    settlements = []
    if settlements_fn and final_poly is not None:
        for sp in settlements_fn((grid.west, grid.south, grid.east, grid.north)):
            r, c = grid.index(sp["lon"], sp["lat"])
            r0, r1, c0, c1 = max(0, r - 3), r + 4, max(0, c - 3), c + 4
            near = t[r0:r1, c0:c1]
            if np.isfinite(near).any():
                settlements.append({**sp, "arrival_hours": round(float(np.nanmin(near[np.isfinite(near)])) / 60.0, 2)})
        settlements.sort(key=lambda x: x["arrival_hours"])

    head_by_hour = [{
        "time_local": s.time_local, "ffmc": round(s.ffmc, 1), "isi": round(s.isi, 1), "wind_kmh": round(s.ws, 1),
        "wind_from_deg": round(s.wd), "temp_c": round(s.temp, 1), "rh_pct": round(s.rh),
        "head_ros_m_min": {FUEL_NAMES[k]: round(float(s.head_ros[k]), 2) for k in (1, 2, 3)}, "lb": round(s.lb, 2),
    } for s in states]
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "ignition": {"lon": inp.lon, "lat": inp.lat, "fuel": FUEL_NAMES[ignition_fuel],
                         "snapped_to": [round(grid.west + (start[1] + 0.5) * grid.dlon, 5), round(grid.north - (start[0] + 0.5) * grid.dlat, 5)]},
            "start_local": inp.start_local.strftime("%Y-%m-%dT%H:%M"), "hours": len(states), "cell_id": inp.cell_id,
            "grid": {"resolution_m": round(res), "radius_m": round(radius), "cells": int(fuel.size),
                     "bbox": [grid.west, grid.south, grid.east, grid.north]},
            "final_area_ha": hourly[-1]["area_ha"] if hourly else 0.0,
            "hourly": hourly, "weather_by_hour": head_by_hour,
            "inputs": {"curing_pct": inp.curing_pct, "ffmc_prev_day": round(inp.ffmc_prev_day, 1),
                       "bui": None if inp.bui is None else round(inp.bui, 1)},
            "fuel": {"summary": fuel_summary(fuel, fuel_data["obs"]), "period": fuel_data["period"],
                     "retrieved_at": fuel_data["retrieved_at"], "source": "Copernicus Sentinel-2 L2A median NDVI (Process API)"},
            "terrain": {"source": "Mapterhorn Terrarium DEM", "elev_min": round(float(elev.min())), "elev_max": round(float(elev.max()))},
            "settlements_reached": settlements[:25],
            "fire_stations": stations_fn(inp.lat, inp.lon) if stations_fn else [],
            "assumptions": [
                "Illustrative scenario, not a forecast: it shows where fire COULD spread from this point under model weather, if nothing stopped it.",
                "Fuel types are Canadian FBP surrogates (O-1a grass, D-1 leafless deciduous) mapped from Sentinel-2 NDVI; they are not calibrated for Indian fuels.",
                f"Grass curing assumed {inp.curing_pct:.0f} %.",
                "No spotting, no crown fire, no suppression; roads and fire lines are not treated as barriers; downhill spread is not slowed.",
                "Weather is hourly model output for the 0.25° cell, applied uniformly across the window.",
            ],
        },
    }


# ---------------------------------------------------------------------------- inputs from the database
class ScenarioError(ValueError):
    """Inputs for a scenario are unavailable (reported to the user as-is)."""


def inputs_from_db(db, regions: list[str], lon: float, lat: float, start_local: datetime | None, hours: int,
                   curing_pct: float) -> ScenarioInputs:
    from zoneinfo import ZoneInfo

    from .geo import cell_for_point, region_def

    cell_id, region = None, None
    for r in regions:
        cell_id = cell_for_point(r, lon, lat)
        if cell_id:
            region = r
            break
    if not cell_id:
        raise ScenarioError("Spread scenarios need hourly weather and FWI, which are computed only inside the risk-grid regions.")
    tz = ZoneInfo(region_def(region).get("timezone", "UTC"))
    if start_local is None:
        start_local = datetime.now(tz).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    start_local = start_local.replace(minute=0, second=0, microsecond=0)
    prev_day = (start_local.date() - timedelta(days=1)).isoformat()
    first = f"{prev_day}T13:00"
    last = (start_local + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")
    rows = [dict(r) for r in db.query(
        "SELECT time_local, t2m, rh2m, ws10, wd10, precip FROM weather_hourly WHERE cell_id=? AND time_local>=? AND time_local<? ORDER BY time_local",
        (cell_id, first, last))]
    start_key = start_local.strftime("%Y-%m-%dT%H:%M")
    if not any(r["time_local"] >= start_key for r in rows):
        raise ScenarioError("No hourly model weather is stored for that start time (available: about the past 2 days to 4 days ahead).")
    prev = db.one("SELECT ffmc FROM fwi_daily WHERE cell_id=? AND date=? AND status='ok'", (cell_id, prev_day))
    today = db.one("SELECT bui FROM fwi_daily WHERE cell_id=? AND date=? AND status='ok'", (cell_id, start_local.date().isoformat()))
    if not prev or prev["ffmc"] is None:
        raise ScenarioError("The previous day's FFMC is not available for this cell, so fuel moisture cannot be initialised.")
    return ScenarioInputs(lon, lat, start_local, max(1, min(hours, MAX_HOURS)), curing_pct, rows, float(prev["ffmc"]),
                          float(today["bui"]) if today and today["bui"] is not None else None, cell_id)
