"""Cached OpenStreetMap points (fire stations, settlements) and nearest-feature queries."""
from __future__ import annotations

import json
import logging
import math

import numpy as np

from .db import Database, iso, utcnow
from .providers import osm
from .providers.http import ProviderError

log = logging.getLogger(__name__)

# Region id -> ISO 3166-2 scope used for village-level settlements.
REGION_SCOPE = {"karnataka": "IN-KA", "uttarakhand": "IN-UT", "odisha": "IN-OR"}
SETTLEMENT_WEIGHT = {"city": 5.0, "town": 3.0, "suburb": 2.0, "village": 1.0, "hamlet": 0.5}


def refresh(db: Database, regions: list[str]) -> dict:
    """Fire stations and towns for India; villages and hamlets for the risk regions."""
    run = db.start_run("osm", "places")
    retrieved = iso(utcnow())
    batches: list[tuple[str, list[dict]]] = []
    errors = []
    jobs = [("IN", lambda: osm.fetch_fire_stations("IN")), ("IN", lambda: osm.fetch_settlements("IN", ("city", "town")))]
    for r in regions:
        scope = REGION_SCOPE.get(r)
        if scope:
            jobs.append((scope, lambda scope=scope: osm.fetch_settlements(scope, ("suburb", "village", "hamlet"))))
    for scope, job in jobs:
        try:
            batches.append((scope, job()))
        except ProviderError as exc:
            errors.append(str(exc))
            log.warning("osm: %s", exc)
    n = 0
    with db.connect() as con:
        for scope, items in batches:
            con.executemany(
                """INSERT OR REPLACE INTO osm_features(id, kind, subtype, name, lat, lon, tags, scope, retrieved_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                [(x["id"], x["kind"], x["subtype"], x["name"], x["lat"], x["lon"], json.dumps(x["tags"]), scope, retrieved) for x in items],
            )
            n += len(items)
    status = "ok" if not errors else ("partial" if batches else "error")
    db.finish_run(run, status, n, "; ".join(errors)[:500] or None, {"batches": len(batches)})
    return {"status": status, "features": n, "errors": errors}


def haversine_km_vec(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    p1, p2 = math.radians(lat), np.radians(lats)
    dphi = p2 - p1
    dl = np.radians(lons) - math.radians(lon)
    a = np.sin(dphi / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 6371.0088 * 2 * np.arcsin(np.sqrt(a))


def _row(r, d: float | None = None) -> dict:
    tags = json.loads(r["tags"] or "{}")
    out = {"id": r["id"], "kind": r["kind"], "subtype": r["subtype"], "name": r["name"], "lat": r["lat"], "lon": r["lon"],
           "phone": tags.get("phone") or tags.get("contact:phone"), "operator": tags.get("operator"),
           "population": tags.get("population"), "osm_url": f"https://www.openstreetmap.org/{r['id']}"}
    if d is not None:
        out["distance_km"] = round(float(d), 2)
    return out


def nearest(db: Database, kind: str, lat: float, lon: float, k: int = 3, max_km: float = 80.0,
            subtypes: tuple[str, ...] | None = None) -> list[dict]:
    pad = max_km / 100.0 + 0.2
    pad_lon = pad / max(math.cos(math.radians(lat)), 0.2)
    sql = "SELECT * FROM osm_features WHERE kind=? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
    params: list = [kind, lat - pad, lat + pad, lon - pad_lon, lon + pad_lon]
    if subtypes:
        sql += f" AND subtype IN ({','.join('?' * len(subtypes))})"
        params += list(subtypes)
    rows = db.query(sql, tuple(params))
    if not rows:
        return []
    d = haversine_km_vec(lat, lon, np.array([r["lat"] for r in rows]), np.array([r["lon"] for r in rows]))
    order = np.argsort(d)
    return [_row(rows[i], d[i]) for i in order[:k] if d[i] <= max_km]


def in_bbox(db: Database, kind: str, bbox: tuple[float, float, float, float]) -> list[dict]:
    w, s, e, n = bbox
    rows = db.query("SELECT * FROM osm_features WHERE kind=? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?", (kind, s, n, w, e))
    return [_row(r) for r in rows]


def counts(db: Database) -> dict:
    rows = db.query("SELECT kind, subtype, COUNT(*) n, MAX(retrieved_at) t FROM osm_features GROUP BY kind, subtype")
    return {"by_kind": [dict(r) for r in rows]}
