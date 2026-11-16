"""Fire-season design conditions per grid cell, for firebreak planning.

Firebreaks are built before the fire season, so they are planned for a bad
fire-season day, not for today's weather. For each 0.25° cell this derives:

  ffmc_p90, bui_p90   90th percentiles of daily FFMC and BUI over the validated
                      Feb-May seasons (cached cell-day tables from `validate`)
  ws_p90_kmh          90th percentile of the daily maximum 10 m wind speed
  wind_from_deg       speed-weighted vector mean of afternoon (12:00-17:00 local)
                      ERA5 10 m wind direction over the same seasons
  steadiness          |vector mean| / mean speed (1 = always from one direction)

Inputs: Open-Meteo archive API (ERA5), the same source as the validation runs.
Output: incendium/data/firebreak_design.json (shipped with the code).
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import timedelta

import numpy as np

from .config import BACKEND_DIR
from .db import Database, iso, utcnow
from .geo import build_grid, region_def
from .providers import openmeteo
from .providers.http import make_client

log = logging.getLogger(__name__)

DESIGN_FILE = BACKEND_DIR / "incendium" / "data" / "firebreak_design.json"
SEASONS = (("2025-02-01", "2025-05-31"), ("2026-02-01", "2026-05-31"))
WIND_VARS = ("wind_speed_10m", "wind_direction_10m")
BATCH = 10
PER_MINUTE_BUDGET = 400.0
DAILY_BUDGET = 9000.0


def load_design() -> dict:
    if DESIGN_FILE.exists():
        return json.loads(DESIGN_FILE.read_text(encoding="utf-8"))
    return {}


def cell_design(region: str, cell_id: str) -> dict | None:
    return (load_design().get(region) or {}).get("cells", {}).get(cell_id)


def _dryness(region: str) -> dict[str, dict]:
    import pandas as pd

    frames = []
    for start, end in SEASONS:
        p = BACKEND_DIR / "instance" / "validation" / "cache" / f"{region}_{start}_{end}" / "cell_days.csv"
        if p.exists():
            frames.append(pd.read_csv(p, usecols=["cell", "ffmc", "bui", "ws_max"]))
    if not frames:
        raise RuntimeError("no validated cell-day tables; run `python -m incendium validate` for the Feb-May seasons first")
    df = pd.concat(frames).dropna()
    out = {}
    for cid, g in df.groupby("cell"):
        out[cid] = {
            "ffmc_p90": round(float(np.percentile(g["ffmc"], 90)), 1),
            "bui_p90": round(float(np.percentile(g["bui"], 90)), 1),
            "ws_p90_kmh": round(float(np.percentile(g["ws_max"], 90)), 1),
            "cell_days": int(len(g)),
        }
    return out


def _afternoon_wind(times: list[str], ws: list, wd: list) -> dict | None:
    sel = [(s, d) for t, s, d in zip(times, ws, wd) if s is not None and d is not None and 12 <= int(t[11:13]) <= 17]
    if len(sel) < 50:
        return None
    s = np.array([x[0] for x in sel], float)
    d = np.radians([x[1] for x in sel])
    u, v = (s * np.sin(d)).mean(), (s * np.cos(d)).mean()
    return {
        "wind_from_deg": round((math.degrees(math.atan2(u, v)) + 360.0) % 360.0, 1),
        "steadiness": round(float(math.hypot(u, v) / s.mean()), 2) if s.mean() > 0 else 0.0,
        "wind_hours": len(sel),
    }


def build(db: Database, region: str) -> dict:
    tz = region_def(region).get("timezone", "UTC")
    cells = list(build_grid(region))
    dry = _dryness(region)
    raw: dict[str, dict[str, list]] = {}
    days = sum((np.datetime64(e) - np.datetime64(s)).astype(int) + 1 for s, e in SEASONS) // len(SEASONS)
    window: list[tuple[float, float]] = []
    with make_client(timeout=120) as client:
        for bi in range(0, len(cells), BATCH):
            batch = cells[bi : bi + BATCH]
            cost = openmeteo.estimated_cost(len(batch), int(days), len(WIND_VARS)) * len(SEASONS)
            if db.usage_since("open-meteo", utcnow() - timedelta(days=1)) + cost > DAILY_BUDGET:
                raise RuntimeError(f"Open-Meteo 24 h budget reached after {bi}/{len(cells)} cells; rerun later")
            now = time.monotonic()
            window = [(t, c) for t, c in window if now - t < 60]
            if window and sum(c for _, c in window) + cost > PER_MINUTE_BUDGET:
                time.sleep(max(0.0, 61 - (now - window[0][0])))
                window = []
            for start, end in SEASONS:
                series = openmeteo.fetch_archive(client, [(c.lat, c.lon) for c in batch], start, end, tz, hourly=WIND_VARS)
                for c, s in zip(batch, series):
                    r = raw.setdefault(c.id, {"t": [], "s": [], "d": []})
                    r["t"] += s.time_local
                    r["s"] += s.values["wind_speed_10m"]
                    r["d"] += s.values["wind_direction_10m"]
            window.append((time.monotonic(), cost))
            db.record_usage("open-meteo", cost)
            log.info("fire-season wind: %d/%d cells", min(bi + BATCH, len(cells)), len(cells))
    out_cells = {}
    for c in cells:
        w = _afternoon_wind(raw[c.id]["t"], raw[c.id]["s"], raw[c.id]["d"]) if c.id in raw else None
        if w and c.id in dry:
            out_cells[c.id] = {**dry[c.id], **w}
    entry = {
        "seasons": [list(s) for s in SEASONS],
        "method": __doc__.split("Inputs:")[0].strip() if __doc__ else "",
        "source": "Open-Meteo archive API (ERA5); FFMC/BUI from the validation cell-day tables",
        "generated_at": iso(utcnow()),
        "cells": out_cells,
    }
    design = load_design()
    design[region] = entry
    DESIGN_FILE.write_text(json.dumps(design, indent=1), encoding="utf-8")
    return {"region": region, "cells": len(out_cells), "of": len(cells)}
