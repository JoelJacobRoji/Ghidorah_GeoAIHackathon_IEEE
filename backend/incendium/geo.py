"""Regions, state polygons and the risk grid.

The grid is a regular latitude/longitude grid aligned to multiples of
``grid_deg`` (default 0.25 degrees, ~28 km), matching the native resolution of
the weather models we sample. A cell is kept when at least ``min_cell_overlap``
of its area lies inside the region polygon. Cell lookup for a point is plain
arithmetic, so no spatial database is required.

Area fractions are computed in degrees. At Indian latitudes (6-37 N) the
distortion inside a single 0.25-degree cell is negligible for this purpose.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache

from shapely import STRtree
from shapely.geometry import Point, box, mapping, shape
from shapely.geometry.base import BaseGeometry

from .config import DATA_DIR, load_region_config


@dataclass(frozen=True)
class Cell:
    id: str
    region: str
    lat: float
    lon: float
    west: float
    south: float
    east: float
    north: float
    overlap: float

    def polygon(self) -> BaseGeometry:
        return box(self.west, self.south, self.east, self.north)


@lru_cache(maxsize=1)
def india_states() -> list[tuple[str, str, BaseGeometry]]:
    """(name, iso_3166_2, geometry) for Indian states/UTs (Natural Earth 1:10m admin-1, public domain)."""
    with open(DATA_DIR / "india_states.geojson", encoding="utf-8") as fh:
        fc = json.load(fh)
    return [(f["properties"]["name"], f["properties"]["iso"], shape(f["geometry"])) for f in fc["features"]]


@lru_cache(maxsize=1)
def _state_index() -> tuple[STRtree, list[str]]:
    states = india_states()
    return STRtree([g for _, _, g in states]), [n for n, _, _ in states]


def state_for_point(lon: float, lat: float) -> str | None:
    tree, names = _state_index()
    pt = Point(lon, lat)
    for idx in tree.query(pt, predicate="intersects"):
        return names[int(idx)]
    return None


def region_def(region_id: str) -> dict:
    for r in load_region_config()["regions"]:
        if r["id"] == region_id:
            return r
    raise KeyError(region_id)


def region_ids() -> list[str]:
    return [r["id"] for r in load_region_config()["regions"]]


@lru_cache(maxsize=16)
def region_geometry(region_id: str) -> BaseGeometry:
    code = region_def(region_id)["state_code"]
    for _, iso_code, geom in india_states():
        if iso_code == code:
            return geom
    raise KeyError(f"No boundary for {region_id} ({code})")


def region_outline_geojson(region_id: str, tolerance: float = 0.01) -> dict:
    geom = region_geometry(region_id).simplify(tolerance, preserve_topology=True)
    return mapping(geom)


def cell_id(region_id: str, lat: float, lon: float) -> str:
    return f"{region_id}:{lat:.3f},{lon:.3f}"


@lru_cache(maxsize=16)
def build_grid(region_id: str) -> tuple[Cell, ...]:
    r = region_def(region_id)
    step = float(r["grid_deg"])
    min_overlap = float(r.get("min_cell_overlap", 0.2))
    geom = region_geometry(region_id)
    west, south, east, north = geom.bounds
    i0, i1 = math.floor(west / step), math.ceil(east / step)
    j0, j1 = math.floor(south / step), math.ceil(north / step)
    cells: list[Cell] = []
    cell_area = step * step
    for j in range(j0, j1):
        for i in range(i0, i1):
            w, s = round(i * step, 6), round(j * step, 6)
            e, n = round(w + step, 6), round(s + step, 6)
            sq = box(w, s, e, n)
            if not sq.intersects(geom):
                continue
            frac = sq.intersection(geom).area / cell_area
            if frac < min_overlap:
                continue
            lat, lon = round(s + step / 2, 6), round(w + step / 2, 6)
            cells.append(Cell(cell_id(region_id, lat, lon), region_id, lat, lon, w, s, e, n, round(frac, 4)))
    return tuple(cells)


def cell_for_point(region_id: str, lon: float, lat: float) -> str | None:
    r = region_def(region_id)
    step = float(r["grid_deg"])
    w = math.floor(lon / step) * step
    s = math.floor(lat / step) * step
    cid = cell_id(region_id, round(s + step / 2, 6), round(w + step / 2, 6))
    return cid if cid in _grid_ids(region_id) else None


@lru_cache(maxsize=16)
def _grid_ids(region_id: str) -> frozenset[str]:
    return frozenset(c.id for c in build_grid(region_id))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def validate_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    w, s, e, n = bbox
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError("bbox must be west,south,east,north with west<east and south<north within WGS84 bounds")
    return w, s, e, n
