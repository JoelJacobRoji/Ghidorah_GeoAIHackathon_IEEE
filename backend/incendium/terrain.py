"""Terrain from Mapterhorn Terrarium tiles (elevation, slope, aspect).

Tiles: https://tiles.mapterhorn.com/{z}/{x}/{y}.webp, 512 px, Terrarium encoding
  elevation_m = R * 256 + G + B / 256 - 32768
Attribution: © Mapterhorn (https://mapterhorn.com/attribution, underlying open DEMs).

Tiles are cached on disk under instance/cache/terrain/ and fetched politely
(sequential, small pause), so each tile is downloaded once.
"""
from __future__ import annotations

import io
import logging
import math
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from .config import BACKEND_DIR
from .db import Database, iso, utcnow
from .providers.http import ProviderError, make_client, request_with_retry

log = logging.getLogger(__name__)

TILE_URL = "https://tiles.mapterhorn.com/{z}/{x}/{y}.webp"
TILE_SIZE = 512
PROVIDER = "Mapterhorn"
ATTRIBUTION = "© Mapterhorn (https://mapterhorn.com/attribution)"
CACHE_DIR = BACKEND_DIR / "instance" / "cache" / "terrain"
M_PER_DEG_LAT = 110_540.0
M_PER_DEG_LON_EQ = 111_320.0


def lonlat_to_px(lon: np.ndarray | float, lat: np.ndarray | float, z: int):
    """Global pixel coordinates in Web Mercator at zoom z (TILE_SIZE px tiles)."""
    n = (2 ** z) * TILE_SIZE
    lat_r = np.radians(np.clip(lat, -85.05112878, 85.05112878))
    x = (np.asarray(lon) + 180.0) / 360.0 * n
    y = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def decode_terrarium(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb.astype(np.float32)
    return rgb[..., 0] * 256.0 + rgb[..., 1] + rgb[..., 2] / 256.0 - 32768.0


_pause = {"t": 0.0}


def _tile_path(z: int, x: int, y: int) -> Path:
    return CACHE_DIR / str(z) / str(x) / f"{y}.webp"


@lru_cache(maxsize=48)
def tile_elevation(z: int, x: int, y: int) -> np.ndarray:
    """Elevation array (TILE_SIZE × TILE_SIZE, metres) for one tile, from disk cache or network."""
    from PIL import Image

    p = _tile_path(z, x, y)
    if not p.exists():
        wait = 0.15 - (time.monotonic() - _pause["t"])
        if wait > 0:
            time.sleep(wait)
        with make_client(timeout=60) as client:
            resp = request_with_retry(client, PROVIDER, "GET", TILE_URL.format(z=z, x=x, y=y))
        _pause["t"] = time.monotonic()
        if resp.status_code == 404:
            # No DEM coverage (open sea): treat as sea level.
            return np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float32)
        if resp.status_code != 200:
            raise ProviderError(PROVIDER, f"HTTP {resp.status_code}", resp.status_code)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(resp.content)
        tmp.replace(p)
    with Image.open(io.BytesIO(p.read_bytes())) as im:
        rgb = np.asarray(im.convert("RGB"))
    return decode_terrarium(rgb)


@dataclass
class Grid:
    """A regular lon/lat grid of cell centres (row 0 = north)."""

    west: float
    south: float
    east: float
    north: float
    nx: int
    ny: int

    @property
    def dlon(self) -> float:
        return (self.east - self.west) / self.nx

    @property
    def dlat(self) -> float:
        return (self.north - self.south) / self.ny

    def centres(self) -> tuple[np.ndarray, np.ndarray]:
        lons = self.west + (np.arange(self.nx) + 0.5) * self.dlon
        lats = self.north - (np.arange(self.ny) + 0.5) * self.dlat
        return np.meshgrid(lons, lats)

    @property
    def dx_m(self) -> float:
        return self.dlon * M_PER_DEG_LON_EQ * math.cos(math.radians((self.north + self.south) / 2))

    @property
    def dy_m(self) -> float:
        return self.dlat * M_PER_DEG_LAT

    def index(self, lon: float, lat: float) -> tuple[int, int]:
        c = int((lon - self.west) / self.dlon)
        r = int((self.north - lat) / self.dlat)
        return min(max(r, 0), self.ny - 1), min(max(c, 0), self.nx - 1)


def grid_around(lon: float, lat: float, radius_m: float, res_m: float) -> Grid:
    dlat = radius_m / M_PER_DEG_LAT
    dlon = radius_m / (M_PER_DEG_LON_EQ * math.cos(math.radians(lat)))
    n = max(3, int(round(2 * radius_m / res_m)))
    return Grid(lon - dlon, lat - dlat, lon + dlon, lat + dlat, n, n)


def sample_dem(grid: Grid, z: int) -> np.ndarray:
    """Bilinear-sampled elevation (metres) at the grid centres."""
    lons, lats = grid.centres()
    px, py = lonlat_to_px(lons, lats, z)
    px -= 0.5
    py -= 0.5
    x0 = np.floor(px).astype(np.int64)
    y0 = np.floor(py).astype(np.int64)
    fx = (px - x0).astype(np.float32)
    fy = (py - y0).astype(np.float32)

    def gather(xx: np.ndarray, yy: np.ndarray) -> np.ndarray:
        out = np.empty(xx.shape, dtype=np.float32)
        tx, ty = xx // TILE_SIZE, yy // TILE_SIZE
        for key in set(zip(tx.ravel().tolist(), ty.ravel().tolist())):
            m = (tx == key[0]) & (ty == key[1])
            tile = tile_elevation(z, int(key[0]), int(key[1]))
            out[m] = tile[yy[m] - key[1] * TILE_SIZE, xx[m] - key[0] * TILE_SIZE]
        return out

    e00 = gather(x0, y0)
    e10 = gather(x0 + 1, y0)
    e01 = gather(x0, y0 + 1)
    e11 = gather(x0 + 1, y0 + 1)
    return (e00 * (1 - fx) * (1 - fy) + e10 * fx * (1 - fy) + e01 * (1 - fx) * fy + e11 * fx * fy).astype(np.float32)


def slope_aspect(elev: np.ndarray, dx_m: float, dy_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Slope (degrees) and downslope aspect (degrees clockwise from north)."""
    # Row 0 is north, so +row is southward.
    dz_dy_south, dz_dx = np.gradient(elev.astype(np.float64), dy_m, dx_m)
    dz_dnorth = -dz_dy_south
    slope = np.degrees(np.arctan(np.hypot(dz_dx, dz_dnorth)))
    aspect = (np.degrees(np.arctan2(-dz_dx, -dz_dnorth)) + 360.0) % 360.0
    return slope.astype(np.float32), aspect.astype(np.float32)


# ---------------------------------------------------------------------------- per-cell statistics
CELL_ZOOM = 10
CELL_SAMPLES = 96  # per side; ~290 m spacing for a 0.25° cell


def compute_cell_terrain(db: Database, region: str, force: bool = False) -> dict:
    cells = db.query("SELECT * FROM cells WHERE region=?", (region,))
    have = {r["cell_id"] for r in db.query("SELECT cell_id FROM cell_terrain")}
    todo = [c for c in cells if force or c["id"] not in have]
    run = db.start_run("terrain", f"cells:{region}")
    rows = []
    try:
        for c in todo:
            g = Grid(c["west"], c["south"], c["east"], c["north"], CELL_SAMPLES, CELL_SAMPLES)
            elev = sample_dem(g, CELL_ZOOM)
            slope, _ = slope_aspect(elev, g.dx_m, g.dy_m)
            rows.append(
                (c["id"], float(elev.mean()), float(elev.min()), float(elev.max()), float(slope.mean()),
                 float(np.percentile(slope, 90)), float(elev.std()), CELL_ZOOM, round((g.dx_m + g.dy_m) / 2), iso(utcnow()))
            )
    except ProviderError as exc:
        db.finish_run(run, "error" if not rows else "partial", len(rows), str(exc))
        raise
    finally:
        if rows:
            with db.connect() as con:
                con.executemany(
                    """INSERT OR REPLACE INTO cell_terrain(cell_id, elev_mean, elev_min, elev_max, slope_mean, slope_p90, relief_std,
                       zoom, sample_m, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
    db.finish_run(run, "ok", len(rows), details={"cells": len(cells), "computed": len(rows)})
    return {"status": "ok", "computed": len(rows), "cells": len(cells)}


def cell_terrain_map(db: Database) -> dict[str, dict]:
    return {r["cell_id"]: dict(r) for r in db.query("SELECT * FROM cell_terrain")}
