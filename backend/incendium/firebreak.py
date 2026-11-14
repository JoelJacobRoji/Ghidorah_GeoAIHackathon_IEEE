"""Module 3: firebreak / fuel-break corridor recommendation (decision support).

Ranks existing linear features in an area of interest as CANDIDATE anchors for
fuel breaks, for field assessment. It does not design firebreaks.

Steps (all parameters fixed and documented, not fitted):
1. Hazard (0-1) on a 100 m grid = FBP head rate of spread of the local fuel class
   (see spread.py) under "dry conditions" for the cell (highest daily FFMC of the
   last 7 days, 90th-percentile afternoon wind speed of the last 14 days and the
   forecast), multiplied by the FBP slope factor of the local slope, divided by the
   window maximum.
2. Prevailing fire-season wind = speed-weighted vector mean of afternoon
   (12:00-17:00) model winds over the same period.
3. Candidates = OpenStreetMap roads/tracks/paths and rivers/streams/canals, cut into
   ~400 m segments.
4. For each segment:
     upwind hazard   mean hazard in a 1.5 km × 0.6 km box on the upwind side
     assets          Σ settlement weight × e^(-d / 2 km) for settlements within 6 km
                     on the downwind side (±60° of the downwind direction)
                     (weights: city 5, town 3, suburb 2, village 1, hamlet 0.5)
     orientation     |sin(segment bearing − wind bearing)|  (across the wind is best)
     access          1.0 roads and tracks, 0.7 paths, e^(-d / 1 km) for waterways (d to nearest road)
     terrain         1 − slope / 35°, clipped to [0.1, 1]
   score = upwind hazard × √(assets / max assets) × (0.4 + 0.6 × orientation) × access × terrain
5. The top segments are merged along the same OSM way into corridors.
"""
from __future__ import annotations

import base64
import io
import math
from datetime import timedelta

import numpy as np

from .fuel import FUEL_NAMES, fetch_fuel, fuel_summary
from .places import SETTLEMENT_WEIGHT
from .spread import head_ros_by_fuel, slope_factor
from .terrain import M_PER_DEG_LAT, M_PER_DEG_LON_EQ, grid_around, sample_dem, slope_aspect

SEGMENT_M = 400.0
UPWIND_LEN_M = 1500.0
UPWIND_HALF_W_M = 300.0
ASSET_RADIUS_M = 6000.0
ASSET_DECAY_M = 2000.0
TOP_SEGMENTS = 40
ACCESS = {"path": 0.7}


class FirebreakError(ValueError):
    pass


def prevailing_wind(db, cell_id: str, now_local) -> dict:
    first = (now_local - timedelta(days=14)).strftime("%Y-%m-%dT00:00")
    rows = db.query(
        "SELECT time_local, ws10, wd10 FROM weather_hourly WHERE cell_id=? AND time_local>=? AND ws10 IS NOT NULL AND wd10 IS NOT NULL",
        (cell_id, first))
    aft = [r for r in rows if 12 <= int(r["time_local"][11:13]) <= 17]
    if len(aft) < 12:
        raise FirebreakError("Not enough hourly model wind for this cell to estimate the prevailing wind.")
    ws = np.array([r["ws10"] for r in aft], dtype=float)
    wd = np.radians([r["wd10"] for r in aft])
    # Vector mean of the direction the wind blows FROM, weighted by speed.
    u, v = (ws * np.sin(wd)).mean(), (ws * np.cos(wd)).mean()
    from_deg = (math.degrees(math.atan2(u, v)) + 360.0) % 360.0
    steadiness = float(math.hypot(u, v) / ws.mean()) if ws.mean() > 0 else 0.0
    return {"from_deg": round(from_deg, 1), "p90_kmh": round(float(np.percentile(ws, 90)), 1), "mean_kmh": round(float(ws.mean()), 1),
            "steadiness": round(steadiness, 2), "hours": len(aft), "period_start": first[:10]}


def _segments(lines: list[dict], seg_m: float, lon0: float, lat0: float):
    """Cut lines into ~seg_m pieces in a local metric frame (x east, y north, metres)."""
    kx = M_PER_DEG_LON_EQ * math.cos(math.radians(lat0))
    out = []
    for li, ln in enumerate(lines):
        xy = np.array([((lo - lon0) * kx, (la - lat0) * M_PER_DEG_LAT) for lo, la in ln["coords"]])
        seglen = np.hypot(*np.diff(xy, axis=0).T)
        total = float(seglen.sum())
        if total < 50:
            continue
        cum = np.concatenate([[0.0], np.cumsum(seglen)])
        n = max(1, int(round(total / seg_m)))
        for k in range(n):
            a, b = total * k / n, total * (k + 1) / n
            ts = np.linspace(a, b, 6)
            pts = np.column_stack([np.interp(ts, cum, xy[:, 0]), np.interp(ts, cum, xy[:, 1])])
            out.append({"line": li, "k": k, "pts": pts, "length": b - a})
    return out, kx


def _sample(grid, arr: np.ndarray, x: np.ndarray, y: np.ndarray, lon0: float, lat0: float, kx: float) -> np.ndarray:
    lon = lon0 + x / kx
    lat = lat0 + y / M_PER_DEG_LAT
    c = ((lon - grid.west) / grid.dlon).astype(int)
    r = ((grid.north - lat) / grid.dlat).astype(int)
    ok = (r >= 0) & (c >= 0) & (r < grid.ny) & (c < grid.nx)
    out = np.full(x.shape, np.nan)
    out[ok] = arr[r[ok], c[ok]]
    return out


def hazard_png(h: np.ndarray) -> str:
    """Hazard raster as a transparent PNG (data URL) on the green -> dark-red ramp."""
    from PIL import Image

    stops = np.array([[0x49, 0xa5, 0x50], [0x85, 0x7c, 0x00], [0x97, 0x50, 0x00], [0x95, 0x25, 0x00], [0x7c, 0x02, 0x06], [0x55, 0x05, 0x13]], float)
    v = np.clip(np.nan_to_num(h), 0, 1)
    pos = v * (len(stops) - 1)
    i0 = np.clip(np.floor(pos).astype(int), 0, len(stops) - 2)
    f = (pos - i0)[..., None]
    rgb = stops[i0] * (1 - f) + stops[i0 + 1] * f
    alpha = np.where(v > 0.02, 70 + 120 * v, 0)
    img = Image.fromarray(np.dstack([rgb, alpha]).astype(np.uint8), "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def recommend(db, regions: list[str], lon: float, lat: float, radius_km: float, lines_fn, settlements_fn) -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from . import fwi as fwi_mod
    from .geo import cell_for_point, region_def

    cell_id = region = None
    for r in regions:
        cell_id = cell_for_point(r, lon, lat)
        if cell_id:
            region = r
            break
    if not cell_id:
        raise FirebreakError("Firebreak planning needs model weather and FWI, which are computed only inside the risk-grid regions.")
    now_local = datetime.now(ZoneInfo(region_def(region).get("timezone", "UTC"))).replace(tzinfo=None)
    wind = prevailing_wind(db, cell_id, now_local)
    since = (now_local.date() - timedelta(days=7)).isoformat()
    fw = db.one("SELECT MAX(ffmc) ffmc, MAX(bui) bui FROM fwi_daily WHERE cell_id=? AND date>=? AND status='ok' AND spinup=0", (cell_id, since))
    if not fw or fw["ffmc"] is None:
        raise FirebreakError("No recent FWI values for this cell.")
    isi = fwi_mod.isi(wind["p90_kmh"], fw["ffmc"])
    ros = head_ros_by_fuel(isi, fw["bui"], 85.0)

    radius_m = min(12.0, max(3.0, radius_km)) * 1000.0
    # Analyse a slightly larger window so upwind boxes near the edge still see fuel.
    grid = grid_around(lon, lat, radius_m + UPWIND_LEN_M, 100.0)
    fuel_data = fetch_fuel(grid)
    fuel = fuel_data["fuel"]
    elev = sample_dem(grid, 11)
    slope, _ = slope_aspect(elev, grid.dx_m, grid.dy_m)
    sf = np.vectorize(slope_factor)(np.tan(np.radians(slope)))
    hz = ros[fuel.astype(int)] * sf
    hz = hz / hz.max() if hz.max() > 0 else hz

    bbox = (grid.west, grid.south, grid.east, grid.north)
    lines, lines_retrieved = lines_fn(bbox)
    if not lines:
        raise FirebreakError("No roads, tracks or waterways were found in OpenStreetMap for this area.")
    segs, kx = _segments(lines, SEGMENT_M, lon, lat)
    # keep segments whose midpoint lies inside the requested radius
    segs = [s for s in segs if math.hypot(*s["pts"][len(s["pts"]) // 2]) <= radius_m]
    if not segs:
        raise FirebreakError("No candidate segments inside the area of interest.")

    to_deg = math.radians((wind["from_deg"] + 180.0) % 360.0)  # direction the wind blows TOWARDS
    down = np.array([math.sin(to_deg), math.cos(to_deg)])
    perp = np.array([down[1], -down[0]])
    mids = np.array([s["pts"][len(s["pts"]) // 2] for s in segs])

    # upwind hazard: grid of offsets in the upwind box
    al = np.linspace(100, UPWIND_LEN_M, 8)
    lat_off = np.linspace(-UPWIND_HALF_W_M, UPWIND_HALF_W_M, 5)
    offs = np.array([-a * down + b * perp for a in al for b in lat_off])
    px = mids[:, None, 0] + offs[None, :, 0]
    py = mids[:, None, 1] + offs[None, :, 1]
    upwind = np.nanmean(_sample(grid, hz, px, py, lon, lat, kx), axis=1)

    # assets downwind
    sett = settlements_fn((grid.west - 0.06, grid.south - 0.06, grid.east + 0.06, grid.north + 0.06))
    assets = np.zeros(len(segs))
    exposed: dict[int, set] = {}
    if sett:
        sxy = np.array([((s["lon"] - lon) * kx, (s["lat"] - lat) * M_PER_DEG_LAT) for s in sett])
        w = np.array([SETTLEMENT_WEIGHT.get(s["subtype"] or "", 0.5) for s in sett])
        d = sxy[None, :, :] - mids[:, None, :]
        dist = np.hypot(d[..., 0], d[..., 1])
        cosang = (d @ down) / np.maximum(dist, 1.0)
        ok = (dist <= ASSET_RADIUS_M) & (cosang >= math.cos(math.radians(60)))
        contrib = np.where(ok, w[None, :] * np.exp(-dist / ASSET_DECAY_M), 0.0)
        assets = contrib.sum(axis=1)
        for i in range(len(segs)):
            exposed[i] = set(np.flatnonzero(contrib[i] > 0.05).tolist())

    # orientation, access, terrain
    vec = np.array([s["pts"][-1] - s["pts"][0] for s in segs])
    bearing = np.arctan2(vec[:, 0], vec[:, 1])
    orient = np.abs(np.sin(bearing - to_deg))
    road_mids = mids[[i for i, s in enumerate(segs) if lines[s["line"]]["kind"] == "road"]]
    access = np.ones(len(segs))
    for i, s in enumerate(segs):
        ln = lines[s["line"]]
        if ln["kind"] == "waterway":
            dmin = float(np.min(np.hypot(*(road_mids - mids[i]).T))) if len(road_mids) else 5000.0
            access[i] = math.exp(-dmin / 1000.0)
        else:
            access[i] = ACCESS.get(ln["class"], 1.0)
    seg_slope = np.array([np.nanmean(_sample(grid, slope, s["pts"][:, 0], s["pts"][:, 1], lon, lat, kx)) for s in segs])
    terrain = np.clip(1.0 - seg_slope / 35.0, 0.1, 1.0)
    burnable = np.array([np.nanmean(_sample(grid, (fuel > 0).astype(float), s["pts"][:, 0], s["pts"][:, 1], lon, lat, kx)) for s in segs])

    amax = assets.max() if assets.max() > 0 else 1.0
    score = np.nan_to_num(upwind) * np.sqrt(assets / amax) * (0.4 + 0.6 * orient) * access * terrain
    order = [i for i in np.argsort(-score) if score[i] > 0][:TOP_SEGMENTS]
    if not order:
        raise FirebreakError("No candidate segment has both upwind fuel hazard and downwind settlements in this area.")

    # merge selected consecutive segments of the same way into corridors
    chosen = sorted(order, key=lambda i: (segs[i]["line"], segs[i]["k"]))
    groups: list[list[int]] = []
    for i in chosen:
        if groups and segs[groups[-1][-1]]["line"] == segs[i]["line"] and segs[groups[-1][-1]]["k"] + 1 == segs[i]["k"]:
            groups[-1].append(i)
        else:
            groups.append([i])
    corridors = []
    for g in groups:
        ln = lines[segs[g[0]]["line"]]
        pts = np.vstack([segs[i]["pts"] for i in g])
        coords = [[round(lon + x / kx, 6), round(lat + y / M_PER_DEG_LAT, 6)] for x, y in pts]
        idx = set().union(*(exposed.get(i, set()) for i in g)) if exposed else set()
        top = sorted((sett[j] for j in idx), key=lambda s: -SETTLEMENT_WEIGHT.get(s["subtype"] or "", 0.5))[:5]
        corridors.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "score": round(float(max(score[i] for i in g)), 4),
                "length_m": round(float(sum(segs[i]["length"] for i in g))),
                "anchor": ln["kind"], "osm_class": ln["class"], "name": ln["name"], "osm_id": ln["id"],
                "upwind_hazard": round(float(np.nanmean(upwind[g])), 3),
                "assets": round(float(assets[g].mean()), 2),
                "orientation": round(float(orient[g].mean()), 2),
                "access": round(float(access[g].mean()), 2),
                "terrain": round(float(terrain[g].mean()), 2),
                "mean_slope_deg": round(float(np.nanmean(seg_slope[g])), 1),
                "burnable_share": round(float(np.nanmean(burnable[g])), 2),
                "settlements_protected": [{"name": s["name"], "subtype": s["subtype"]} for s in top],
            },
        })
    corridors.sort(key=lambda f: -f["properties"]["score"])
    for rank, f in enumerate(corridors, 1):
        f["properties"]["rank"] = rank
    corridors = corridors[:15]

    return {
        "type": "FeatureCollection",
        "features": corridors,
        "meta": {
            "center": [lon, lat], "radius_km": radius_m / 1000.0, "cell_id": cell_id,
            "wind": wind, "dry_conditions": {"ffmc_7d_max": round(float(fw["ffmc"]), 1), "bui_7d_max": None if fw["bui"] is None else round(float(fw["bui"]), 1),
                                             "isi": round(isi, 1), "head_ros_m_min": {FUEL_NAMES[k]: round(float(ros[k]), 2) for k in (1, 2, 3)}},
            "hazard_image": {"url": hazard_png(hz), "bounds": [grid.west, grid.south, grid.east, grid.north]},
            "candidates_scored": len(segs), "settlements_considered": len(sett),
            "fuel": {"summary": fuel_summary(fuel, fuel_data["obs"]), "period": fuel_data["period"], "retrieved_at": fuel_data["retrieved_at"]},
            "osm_retrieved_at": lines_retrieved,
            "method": __doc__.split("Steps")[1].strip() if __doc__ else "",
            "assumptions": [
                "Candidate corridors for field assessment only. They are existing roads, tracks and waterways that could anchor fuel breaks; ground conditions, land ownership, ecology and legal constraints are not considered.",
                "Hazard uses Canadian FBP surrogate fuel types mapped from Sentinel-2 NDVI and a single prevailing wind for the whole area.",
                "Settlements come from OpenStreetMap points and may be incomplete; isolated houses and farms are not included.",
                "Ridgelines and existing natural breaks other than mapped waterways are not yet considered.",
            ],
        },
    }
