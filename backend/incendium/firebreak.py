"""Module 3: firebreak / fuel-break line recommendation (decision support).

Purpose: most wildfires here are started by people, near roads and settlements.
The tool proposes lines that separate that human ignition zone from the deeper
forest behind it, so that a fire started near a road or village is held at the
forest edge instead of running into the forest interior. Lines are proposed only
where (a) forest adjoins the human zone, (b) a sizeable forest (at least 5 km²)
lies behind it and (c) the area is high-risk: satellites detected forest fires
within 2 km of the edge in recent fire seasons.

Steps (all parameters fixed and documented, not fitted):
1. Design fire day: firebreaks are built before the fire season, so the cell's
   fire-season conditions are used, not today's weather: 90th-percentile FFMC,
   BUI and daily maximum wind over the validated Feb-May seasons, and the
   prevailing afternoon wind direction over the same seasons
   (see firebreak_design.py; Open-Meteo ERA5).
2. Fuel (100 m grid) from fire-season Sentinel-2 imagery (median NDVI of
   1 Feb - 30 Apr of the latest season, when crops are harvested and deciduous
   forest is leaf-off; classes as in fuel.py) and fire intensity per fuel class from the Canadian FBP System, as head-fire intensity I = 300 × fuel
   consumed (kg/m²) × rate of spread (m/min), in kW/m:
     grass  O-1a, 90 % cured, fuel load 0.35 kg/m²
     forest C-7 (open forest with a grass/litter understorey), surface fire
     shrub  mean of the two
   multiplied by the FBP slope factor. Hazard = I / 10 000 kW/m, capped at 1,
   so the scale is the same in every window.
3. Human ignition zone = everything within 500 m of a motorable road
   (OpenStreetMap trunk … service; tracks and paths excluded) or of a
   settlement's approximate built-up edge (OSM place point with a radius of
   city 2.5 km, town 1.2 km, suburb 0.8 km, village 0.4 km, hamlet 0.2 km).
4. Candidate line = the outer edge of the ignition zone. A 100 m step of that
   edge is kept only where it lies in forest or shrub, the forest behind it
   (outside the ignition zone) is a connected patch of at least 5 km², and the
   mean fire intensity in the first kilometre behind it is at least 2000 kW/m
   (beyond direct attack with hand tools). Kept steps are joined into continuous
   lines; lines shorter than 1 km are dropped.
   High-risk test: a line is kept only if at least 3 VIIRS (NOAA-20) detections
   on forest or shrub pixels fell within 2 km of it during the Feb-May 2025 and
   2026 fire seasons. Evergreen and plantation belts that rarely burn drop out.
5. Each line is scored:
     hazard        mean hazard in the first kilometre of forest behind it
     fires         past forest-fire detections within 2 km (0.5 + 0.5 × min(1, n / 30))
     wind          share of the line where the fire-season wind blows from the
                   human zone into the forest, weighted by how steady that wind
                   is: 0.4 + 0.6 × (steadiness × share + (1 − steadiness) × 0.5)
     pressure      nearby people: settlements within 3 km and motorable roads
                   within 1 km (0.5 + 0.5 × saturating index)
     forest        size of the forest protected (0.6 + 0.4 × min(1, km² / 10))
     terrain       1 − slope / 35°, clipped to [0.1, 1] (buildability)
   score = hazard × fires × wind × pressure × forest × terrain. Up to 15 lines are shown.
6. Where an existing track, path or stream runs along a line (within 150 m for
   at least 40 % of its length) it is named as a possible anchor.
"""
from __future__ import annotations

import base64
import io
import math

import numpy as np
import shapely
from scipy import ndimage
from shapely.geometry import LineString, Point, box, mapping
from shapely.ops import transform, unary_union

from .fuel import FUEL_NAMES, fetch_fuel, fire_season_period, fuel_summary
from .places import SETTLEMENT_WEIGHT
from .spread import curing_factor, slope_factor
from .terrain import M_PER_DEG_LAT, M_PER_DEG_LON_EQ, grid_around, sample_dem, slope_aspect

RES_M = 100.0
MARGIN_M = 1500.0
IGNITION_BUFFER_M = 500.0
SETTLEMENT_RADIUS_M = {"city": 2500.0, "town": 1200.0, "suburb": 800.0, "village": 400.0, "hamlet": 200.0}
HUMAN_ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "service"}
ANCHOR_CLASSES = {"track", "path", "river", "stream", "canal"}
STEP_M = 100.0
BEHIND_M = (150.0, 350.0, 550.0, 750.0, 950.0)
MIN_DEEP_FOREST_KM2 = 5.0
MIN_INTENSITY_KW_M = 2000.0
INTENSITY_CAP_KW_M = 10000.0
MIN_LINE_M = 1000.0
FIRE_RADIUS_M = 2000.0
MIN_PAST_FIRES = 3
HISTORY_YEARS = (2025, 2026)
ANCHOR_DIST_M = 150.0
GRASS_CURING_PCT = 90.0
GRASS_FUEL_LOAD = 0.35
MAX_LINES = 15
INTENSITY_CLASSES = ((500.0, "low"), (2000.0, "moderate"), (4000.0, "high"), (math.inf, "very high"))


class FirebreakError(ValueError):
    pass


# ---------------------------------------------------------------------------- fire behaviour
def ros_c7(isi: float, bui: float) -> float:
    """FBP C-7 head rate of spread (m/min), with the BUI effect (q 0.85, BUI0 106)."""
    ros = 45.0 * (1.0 - math.exp(-0.0305 * isi)) ** 2.0
    if bui > 0:
        ros *= math.exp(50.0 * math.log(0.85) * (1.0 / bui - 1.0 / 106.0))
    return ros


def sfc_c7(ffmc: float, bui: float) -> float:
    """FBP C-7 surface fuel consumption (kg/m²): forest floor + woody fuel."""
    ffc = 2.0 * (1.0 - math.exp(-0.104 * (ffmc - 70.0))) if ffmc > 70 else 0.0
    wfc = 1.5 * (1.0 - math.exp(-0.0201 * bui))
    return ffc + wfc


def ros_o1a(isi: float, curing_pct: float) -> float:
    return 190.0 * (1.0 - math.exp(-0.0310 * isi)) ** 1.40 * curing_factor(curing_pct)


def intensity_by_fuel(isi: float, ffmc: float, bui: float) -> np.ndarray:
    """Head-fire intensity (kW/m) on flat ground, indexed by fuel class 0..3."""
    grass = 300.0 * GRASS_FUEL_LOAD * ros_o1a(isi, GRASS_CURING_PCT)
    forest = 300.0 * sfc_c7(ffmc, bui) * ros_c7(isi, bui)
    return np.array([0.0, grass, 0.5 * (grass + forest), forest], dtype=np.float64)


def intensity_class(i: float) -> str:
    return next(name for limit, name in INTENSITY_CLASSES if i < limit)


def past_fires(region: str, bbox: tuple[float, float, float, float]) -> tuple[list[tuple[float, float]], list[int]]:
    """VIIRS NOAA-20 fire-season detections (lon, lat) inside bbox from the cached history files."""
    import json

    from .ml import _history_path

    w, s, e, n = bbox
    pts, years = [], []
    for y in HISTORY_YEARS:
        p = _history_path(region, y)
        if not p.exists():
            continue
        years.append(y)
        for d in json.loads(p.read_text(encoding="utf-8"))["detections"]:
            if w <= d["lon"] <= e and s <= d["lat"] <= n and d["acq"][5:7] in ("02", "03", "04", "05"):
                pts.append((d["lon"], d["lat"]))
    return pts, years


def design_conditions(region: str, cell_id: str) -> dict:
    from . import fwi as fwi_mod
    from .firebreak_design import cell_design

    d = cell_design(region, cell_id)
    if not d:
        raise FirebreakError("No fire-season design conditions for this cell; run `python -m incendium firebreak-design`.")
    isi = fwi_mod.isi(d["ws_p90_kmh"], d["ffmc_p90"])
    return {**d, "isi": round(isi, 1)}


# ---------------------------------------------------------------------------- raster helpers
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


class _Frame:
    """Local metric frame (x east, y north, metres) centred on the analysis point."""

    def __init__(self, grid, lon0: float, lat0: float):
        self.grid, self.lon0, self.lat0 = grid, lon0, lat0
        self.kx = M_PER_DEG_LON_EQ * math.cos(math.radians(lat0))

    def to_xy(self, lon, lat):
        return (np.asarray(lon) - self.lon0) * self.kx, (np.asarray(lat) - self.lat0) * M_PER_DEG_LAT

    def to_lonlat(self, x, y):
        return self.lon0 + np.asarray(x) / self.kx, self.lat0 + np.asarray(y) / M_PER_DEG_LAT

    def rc(self, x, y):
        lon, lat = self.to_lonlat(x, y)
        g = self.grid
        c = np.floor((lon - g.west) / g.dlon).astype(int)
        r = np.floor((g.north - lat) / g.dlat).astype(int)
        ok = (r >= 0) & (c >= 0) & (r < g.ny) & (c < g.nx)
        return r, c, ok

    def sample(self, arr: np.ndarray, x, y, fill=np.nan) -> np.ndarray:
        x, y = np.asarray(x, float), np.asarray(y, float)
        r, c, ok = self.rc(x, y)
        out = np.full(x.shape, fill, dtype=float)
        out[ok] = arr[r[ok], c[ok]]
        return out

    def geom_to_lonlat(self, geom):
        return transform(lambda x, y, z=None: tuple(self.to_lonlat(x, y)), geom)


def _runs(keep: np.ndarray, max_gap: int = 1) -> list[tuple[int, int]]:
    """Index ranges [a, b] of kept points, bridging gaps of up to max_gap points."""
    idx = np.flatnonzero(keep)
    if not len(idx):
        return []
    runs, a, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > max_gap + 1:
            runs.append((a, prev))
            a = i
        prev = i
    runs.append((a, prev))
    return runs


# ---------------------------------------------------------------------------- main
def recommend(db, regions: list[str], lon: float, lat: float, radius_km: float, lines_fn, settlements_fn) -> dict:
    from .geo import cell_for_point, region_def

    cell_id = region = None
    for r in regions:
        cell_id = cell_for_point(r, lon, lat)
        if cell_id:
            region = r
            break
    if not cell_id or not region:
        names = " and ".join(region_def(r).get("name", r) for r in regions)
        raise FirebreakError(f"This point is outside {names}. Firebreak planning works only there, where fire-season weather and fuel are computed; click a point inside {names}.")
    design = design_conditions(region, cell_id)
    i_fuel = intensity_by_fuel(design["isi"], design["ffmc_p90"], design["bui_p90"])

    radius_m = min(12.0, max(3.0, radius_km)) * 1000.0
    grid = grid_around(lon, lat, radius_m + MARGIN_M, RES_M)
    fr = _Frame(grid, lon, lat)
    fuel_data = fetch_fuel(grid, period=fire_season_period())
    fuel = fuel_data["fuel"].astype(int)
    elev = sample_dem(grid, 11)
    slope, _ = slope_aspect(elev, grid.dx_m, grid.dy_m)
    sf = np.vectorize(slope_factor)(np.tan(np.radians(slope)))
    intensity = i_fuel[fuel] * sf
    hz = np.clip(intensity / INTENSITY_CAP_KW_M, 0.0, 1.0)

    # ---- human ignition zone (vector, local metres)
    bbox = (grid.west, grid.south, grid.east, grid.north)
    lines, lines_retrieved = lines_fn(bbox)
    sett = settlements_fn((grid.west - 0.03, grid.south - 0.03, grid.east + 0.03, grid.north + 0.03))

    def xy_line(ln):
        x, y = fr.to_xy([p[0] for p in ln["coords"]], [p[1] for p in ln["coords"]])
        return LineString(np.column_stack([x, y]))

    roads = [(ln, xy_line(ln)) for ln in lines if ln["kind"] == "road" and ln["class"] in HUMAN_ROADS]
    anchors = [(ln, xy_line(ln)) for ln in lines if ln["class"] in ANCHOR_CLASSES]
    sett_xy = [(s, Point(*fr.to_xy(s["lon"], s["lat"]))) for s in sett]
    parts = [g.buffer(IGNITION_BUFFER_M, quad_segs=4) for _, g in roads]
    parts += [p.buffer(SETTLEMENT_RADIUS_M.get(s["subtype"] or "", 200.0) + IGNITION_BUFFER_M, quad_segs=6) for s, p in sett_xy]
    window = box(*fr.to_xy(grid.west, grid.south), *fr.to_xy(grid.east, grid.north))
    human = unary_union(parts).simplify(20.0).intersection(window) if parts else shapely.Polygon()
    shapely.prepare(human)

    lons, lats = grid.centres()
    gx, gy = fr.to_xy(lons, lats)
    in_human = shapely.contains_xy(human, gx, gy) if not human.is_empty else np.zeros(gx.shape, bool)

    # ---- forest behind the ignition zone
    forest = np.isin(fuel, (2, 3))
    forest = ndimage.binary_opening(ndimage.binary_closing(forest, iterations=1), iterations=1)
    labels, n_lab = ndimage.label(forest & ~in_human, structure=np.ones((3, 3)))
    px_km2 = grid.dx_m * grid.dy_m / 1e6
    lab_area = np.concatenate([[0.0], ndimage.sum(np.ones_like(labels), labels, index=np.arange(1, n_lab + 1)) * px_km2]) if n_lab else np.zeros(1)
    deep = lab_area >= MIN_DEEP_FOREST_KM2

    fire_ll, fire_years = past_fires(region, (grid.west, grid.south, grid.east, grid.north))
    if fire_ll:
        fx, fy = fr.to_xy([p[0] for p in fire_ll], [p[1] for p in fire_ll])
        on_forest = fr.sample(forest.astype(float), fx, fy, 0.0) > 0
        fire_pts = shapely.points(np.column_stack([fx[on_forest], fy[on_forest]]))
    else:
        fire_pts = shapely.points(np.zeros((0, 2)))
    fire_tree = shapely.STRtree(fire_pts)

    empty_reason = None
    boundary_km = 0.0
    corridors: list[dict] = []
    road_union = unary_union([g for _, g in roads]) if roads else None
    aoi = Point(0, 0).buffer(radius_m, quad_segs=32)
    down = None
    if human.is_empty:
        empty_reason = "There are no motorable roads or mapped settlements in this area, so there is no human ignition zone to separate from the forest."
    else:
        to_rad = math.radians((design["wind_from_deg"] + 180.0) % 360.0)
        down = np.array([math.sin(to_rad), math.cos(to_rad)])
        edge = human.boundary.intersection(aoi)
        edge_lines = [g for g in getattr(edge, "geoms", [edge]) if isinstance(g, LineString) and g.length >= MIN_LINE_M]
        for el in edge_lines:
            boundary_km += el.length / 1000.0
            d = np.arange(0.0, el.length + 1e-6, STEP_M)
            pts = shapely.get_coordinates(shapely.line_interpolate_point(el, d))
            if len(pts) < 3:
                continue
            tan = np.gradient(pts, axis=0)
            tan /= np.maximum(np.hypot(tan[:, 0], tan[:, 1]), 1e-9)[:, None]
            nrm = np.column_stack([tan[:, 1], -tan[:, 0]])
            probe = pts + 150.0 * nrm
            flip = shapely.contains_xy(human, probe[:, 0], probe[:, 1])
            nrm[flip] *= -1  # normal now points away from the human zone, into the forest side
            # forest at the line itself (3 × 3 neighbourhood)
            at_line = np.mean([fr.sample(forest.astype(float), pts[:, 0] + ox, pts[:, 1] + oy, 0.0)
                               for ox in (-RES_M, 0, RES_M) for oy in (-RES_M, 0, RES_M)], axis=0)
            behind = np.stack([pts + b * nrm for b in BEHIND_M], axis=1)  # (n, k, 2)
            lab_b = fr.sample(labels, behind[..., 0], behind[..., 1], 0.0).astype(int)
            deep_b = deep[lab_b].any(axis=1)
            i_b = np.nanmean(fr.sample(intensity, behind[..., 0], behind[..., 1]), axis=1)
            h_b = np.nanmean(fr.sample(hz, behind[..., 0], behind[..., 1]), axis=1)
            keep = (at_line >= 0.4) & deep_b & (np.nan_to_num(i_b) >= MIN_INTENSITY_KW_M)
            for a, b in _runs(keep):
                if (b - a) * STEP_M < MIN_LINE_M:
                    continue
                sl = slice(a, b + 1)
                geom = LineString(pts[sl])
                n_fires = len(fire_tree.query(geom, predicate="dwithin", distance=FIRE_RADIUS_M)) if len(fire_pts) else 0
                if fire_years and n_fires < MIN_PAST_FIRES:
                    continue
                corridors.append({
                    "geom": geom, "pts": pts[sl], "nrm": nrm[sl],
                    "intensity": float(np.nanmean(i_b[sl])), "hazard": float(np.nanmean(h_b[sl])), "fires": n_fires,
                    "labels": {int(x) for x in lab_b[sl].ravel() if x and deep[int(x)]},
                })
        if not corridors:
            empty_reason = (
                "No forest edge here meets all the conditions: forest or shrub next to the 500 m road/settlement zone, "
                f"at least {MIN_DEEP_FOREST_KM2:g} km² of forest behind it, fire-season intensity of at least {MIN_INTENSITY_KW_M:.0f} kW/m"
                + (f", and at least {MIN_PAST_FIRES} satellite forest-fire detections within {FIRE_RADIUS_M / 1000:g} km in the "
                   f"{' and '.join(map(str, fire_years))} fire seasons." if fire_years else ".")
            )

    # ---- score
    sett_pts = np.array([[p.x, p.y] for _, p in sett_xy]) if sett_xy else np.zeros((0, 2))
    sett_w = np.array([SETTLEMENT_WEIGHT.get(s["subtype"] or "", 0.5) for s, _ in sett_xy])
    feats = []
    for c in corridors:
        g = c["geom"]
        wind_share = float(np.mean((c["nrm"] @ down) > 0.2)) if down is not None else 0.0
        steady = float(design["steadiness"])
        wind_eff = steady * wind_share + (1.0 - steady) * 0.5
        if len(sett_pts):
            dist = shapely.distance(g, shapely.points(sett_pts))
            near = dist <= 3000.0
            sett_idx = float(np.sum(sett_w[near] * np.exp(-dist[near] / 1500.0)))
            names = [sett_xy[j][0] for j in np.flatnonzero(near)[np.argsort(dist[near])][:4]]
        else:
            sett_idx, names = 0.0, []
        road_km = road_union.intersection(g.buffer(1000.0)).length / 1000.0 if road_union is not None else 0.0
        pressure = 1.0 - math.exp(-(sett_idx + road_km / 4.0) / 2.0)
        forest_km2 = float(sum(lab_area[x] for x in c["labels"]))
        seg_slope = float(np.nanmean(fr.sample(slope, c["pts"][:, 0], c["pts"][:, 1])))
        terrain = float(np.clip(1.0 - seg_slope / 35.0, 0.1, 1.0))
        score = c["hazard"] * (0.5 + 0.5 * min(1.0, c["fires"] / 30.0)) * (0.4 + 0.6 * wind_eff) * (0.5 + 0.5 * pressure) * (0.6 + 0.4 * min(1.0, forest_km2 / 10.0)) * terrain
        # existing linear feature along the line?
        anchor = None
        best = 0.0
        for ln, ag in anchors:
            if ag.distance(g) > ANCHOR_DIST_M:
                continue
            share = float(np.mean(shapely.distance(ag, shapely.points(c["pts"])) <= ANCHOR_DIST_M))
            if share >= 0.4 and share > best:
                best, anchor = share, {"osm_id": ln["id"], "class": ln["class"], "name": ln["name"], "share": round(share, 2)}
        near_roads = sorted({ln["name"] for ln, rg in roads if ln["name"] and rg.distance(g) <= IGNITION_BUFFER_M + 50})[:3]
        ll = fr.geom_to_lonlat(g.simplify(15.0))
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[round(x, 6), round(y, 6)] for x, y in ll.coords]},
            "properties": {
                "score": round(score, 4),
                "length_m": round(g.length),
                "forest_intensity_kw_m": round(c["intensity"]),
                "intensity_class": intensity_class(c["intensity"]),
                "hazard": round(c["hazard"], 3),
                "wind_into_forest": round(wind_share, 2),
                "past_fires": c["fires"],
                "human_pressure": round(pressure, 2),
                "forest_protected_km2": round(forest_km2, 1),
                "terrain": round(terrain, 2),
                "mean_slope_deg": round(seg_slope, 1),
                "settlements_near": [{"name": s["name"], "subtype": s["subtype"]} for s in names],
                "roads_near": near_roads,
                "anchor": anchor,
            },
        })
    feats.sort(key=lambda f: -f["properties"]["score"])
    feats = feats[:MAX_LINES]
    for rank, f in enumerate(feats, 1):
        f["properties"]["rank"] = rank

    human_ll = fr.geom_to_lonlat(human.intersection(aoi).simplify(40.0)) if not human.is_empty else None
    return {
        "type": "FeatureCollection",
        "features": feats,
        "meta": {
            "center": [lon, lat], "radius_km": radius_m / 1000.0, "cell_id": cell_id,
            "design": {
                "seasons": "Feb–May 2025 and 2026",
                "wind_from_deg": design["wind_from_deg"], "steadiness": design["steadiness"], "ws_p90_kmh": design["ws_p90_kmh"],
                "ffmc_p90": design["ffmc_p90"], "bui_p90": design["bui_p90"], "isi": design["isi"],
                "intensity_kw_m": {FUEL_NAMES[k]: round(float(i_fuel[k])) for k in (1, 2, 3)},
            },
            "human_zone": mapping(human_ll) if human_ll is not None and not human_ll.is_empty else None,
            "hazard_image": {"url": hazard_png(hz), "bounds": [grid.west, grid.south, grid.east, grid.north]},
            "edge_km_examined": round(boundary_km, 1),
            "line_km_proposed": round(sum(f["properties"]["length_m"] for f in feats) / 1000.0, 1),
            "settlements_considered": len(sett), "roads_considered": len(roads),
            "empty_reason": empty_reason if not feats else None,
            "fire_history": {"years": fire_years, "detections_in_window": len(fire_ll), "on_forest": len(fire_pts),
                             "source": "NASA FIRMS VIIRS NOAA-20 standard processing, Feb–May"},
            "parameters": {"ignition_buffer_m": IGNITION_BUFFER_M, "min_forest_km2": MIN_DEEP_FOREST_KM2, "min_past_fires": MIN_PAST_FIRES, "fire_radius_m": FIRE_RADIUS_M,
                           "min_intensity_kw_m": MIN_INTENSITY_KW_M, "min_line_m": MIN_LINE_M},
            "fuel": {"summary": fuel_summary(fuel, fuel_data["obs"]), "period": fuel_data["period"], "retrieved_at": fuel_data["retrieved_at"]},
            "osm_retrieved_at": lines_retrieved,
            "method": __doc__.split("Steps")[1].strip() if __doc__ else "",
            "assumptions": [
                "Proposed lines for field assessment only. Land ownership, ecology, protected-area rules and existing fire lines are not considered.",
                "The human ignition zone is a fixed 500 m around motorable roads and approximate settlement extents from OpenStreetMap points; isolated houses, farms and unmapped roads are missed.",
                "Past fire detections mark where fires have burned, not every place they could; areas with no recent detections get no line.",
                "Fuel is a fire-season Sentinel-2 NDVI proxy mapped to Canadian FBP surrogate fuel types (not calibrated for Indian forests); crown fire and spotting are not modelled.",
                "Design weather is one 0.25° cell's fire-season 90th percentiles and a single prevailing wind direction for the whole area.",
            ],
        },
    }
