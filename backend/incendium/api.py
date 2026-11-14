"""HTTP API (read-only for the public; manual refresh restricted to localhost)."""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from .config import load_region_config
from .db import Database, iso, utcnow
from .geo import haversine_km, region_def, region_outline_geojson
from .ingest import Ingestor
from .methodology import methodology
from .schemas import CellQuery, HotspotQuery, RegionQuery, ValidationQuery, WeatherQuery

bp = Blueprint("api", __name__, url_prefix="/api")

PROVIDERS = {
    "firms": {"label": "NASA FIRMS (VIIRS NOAA-20/21)", "expected_minutes": 30, "stale_after_minutes": 180},
    "open-meteo": {"label": "Open-Meteo weather model", "expected_minutes": 180, "stale_after_minutes": 540},
    "cdse": {"label": "Copernicus Sentinel-2 (NDVI/NDMI)", "expected_minutes": 7 * 1440, "stale_after_minutes": 10 * 1440},
    "risk-engine": {"label": "Incendium risk engine", "expected_minutes": 180, "stale_after_minutes": 540},
}
MANUAL_MIN_INTERVAL = {"firms": timedelta(minutes=5), "weather": timedelta(minutes=30), "vegetation": timedelta(hours=12)}
_manual_last: dict[str, datetime] = {}
_manual_lock = threading.Lock()


def _db() -> Database:
    return current_app.extensions["incendium_db"]


def _ing() -> Ingestor:
    return current_app.extensions["incendium_ingestor"]


def _args(model):
    try:
        return model(**request.args.to_dict())
    except ValidationError as exc:
        errs = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        raise BadRequest(errs) from None


class BadRequest(Exception):
    def __init__(self, errors):
        self.errors = errors


@bp.errorhandler(BadRequest)
def _bad(e: BadRequest):
    return jsonify({"error": "invalid_request", "details": e.errors}), 400


@bp.errorhandler(HTTPException)
def _http(e: HTTPException):
    return jsonify({"error": e.name.lower().replace(" ", "_"), "message": e.description}), e.code


@bp.errorhandler(Exception)
def _err(e: Exception):  # never leak internals
    current_app.logger.exception("Unhandled API error")
    return jsonify({"error": "internal_error", "message": "An internal error occurred."}), 500


def _parse_ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _provider_status() -> list[dict]:
    db = _db()
    now = utcnow()
    out = []
    for pid, meta in PROVIDERS.items():
        last = db.one("SELECT * FROM provider_runs WHERE provider=? AND status!='running' ORDER BY id DESC LIMIT 1", (pid,))
        last_ok = db.one(
            "SELECT * FROM provider_runs WHERE provider=? AND status IN ('ok','partial') ORDER BY id DESC LIMIT 1", (pid,)
        )
        running = db.one("SELECT id FROM provider_runs WHERE provider=? AND status='running' ORDER BY id DESC LIMIT 1", (pid,))
        ok_t = _parse_ts(last_ok["finished_at"]) if last_ok else None
        age_min = (now - ok_t).total_seconds() / 60 if ok_t else None
        if ok_t is None:
            state = "unavailable" if last else "never_run"
        elif age_min is not None and age_min > meta["stale_after_minutes"]:
            state = "stale"
        elif last and last["status"] == "error":
            state = "degraded"
        elif last_ok and last_ok["status"] == "partial":
            state = "partial"
        else:
            state = "fresh"
        out.append(
            {
                "id": pid,
                "label": meta["label"],
                "state": state,
                "running": bool(running),
                "last_success": last_ok["finished_at"] if last_ok else None,
                "last_attempt": last["finished_at"] if last else None,
                "last_status": last["status"] if last else None,
                "last_message": last["message"] if last and last["status"] != "ok" else None,
                "age_minutes": round(age_min, 1) if age_min is not None else None,
                "expected_interval_minutes": meta["expected_minutes"],
            }
        )
    return out


# --------------------------------------------------------------------------- basic
@bp.get("/health")
def health():
    _db().one("SELECT 1")
    return jsonify({"status": "ok", "time": iso(utcnow())})


@bp.get("/providers/status")
def providers_status():
    return jsonify({"providers": _provider_status(), "generated_at": iso(utcnow())})


@bp.get("/regions")
def regions():
    cfg = load_region_config()
    s = current_app.extensions["incendium_settings"]
    out = []
    for r in cfg["regions"]:
        enabled = r["id"] in s.risk_regions
        n = _db().one("SELECT COUNT(*) AS n FROM cells WHERE region=?", (r["id"],))["n"]
        out.append(
            {
                "id": r["id"], "name": r["name"], "state_code": r["state_code"], "grid_deg": r["grid_deg"],
                "center": r["center"], "zoom": r["zoom"], "enabled": enabled, "cells": n,
                "outline": region_outline_geojson(r["id"]),
            }
        )
    return jsonify({"regions": out, "detection_window": cfg["detection_window"]})


@bp.get("/validation")
def validation_report():
    from .validation import load_report

    q = _args(ValidationQuery)
    report = load_report(q.region, q.season)
    if report is None:
        return jsonify({"error": "not_found", "message": "No retrospective comparison has been run for this region. Run `python -m incendium validate`."}), 404
    return jsonify(report)


@bp.get("/methodology")
def get_methodology():
    return jsonify(methodology(current_app.extensions["incendium_settings"].composite_weights, list(current_app.extensions["incendium_settings"].risk_regions)))


# --------------------------------------------------------------------------- hotspots
def _detection_feature(r) -> dict:
    return {
        "type": "Feature",
        "id": r["id"],
        "geometry": {"type": "Point", "coordinates": [r["longitude"], r["latitude"]]},
        "properties": {
            "id": r["id"], "satellite": r["satellite"], "instrument": r["instrument"], "source": r["source"],
            "acq_datetime": r["acq_datetime"], "confidence": r["confidence"], "frp": r["frp"],
            "bright_ti4": r["bright_ti4"], "bright_ti5": r["bright_ti5"], "scan": r["scan"], "track": r["track"],
            "daynight": r["daynight"], "version": r["version"], "state": r["state"], "cell_id": r["cell_id"],
            "retrieved_at": r["retrieved_at"],
        },
    }


@bp.get("/hotspots")
def hotspots():
    q = _args(HotspotQuery)
    until = q.until or utcnow()
    since = q.since or (until - timedelta(hours=q.hours))
    if since >= until:
        raise BadRequest([{"field": "since", "message": "since must be before until"}])
    if (until - since) > timedelta(days=31):
        raise BadRequest([{"field": "since", "message": "time window limited to 31 days"}])
    where = ["acq_datetime >= ?", "acq_datetime <= ?"]
    params: list = [iso(since), iso(until)]
    if (bb := q.bbox_tuple()) is not None:
        where += ["longitude BETWEEN ? AND ?", "latitude BETWEEN ? AND ?"]
        params += [bb[0], bb[2], bb[1], bb[3]]
    if q.min_confidence:
        allowed = {"low": ("low", "nominal", "high"), "nominal": ("nominal", "high"), "high": ("high",)}[q.min_confidence]
        where.append(f"confidence IN ({','.join('?' * len(allowed))})")
        params += list(allowed)
    if q.satellite:
        where.append("satellite = ?")
        params.append(q.satellite)
    if q.state:
        where.append("state = ?")
        params.append(q.state)
    if q.region:
        where.append("cell_id IN (SELECT id FROM cells WHERE region=?)")
        params.append(q.region)
    sql = f"SELECT * FROM detections WHERE {' AND '.join(where)} ORDER BY acq_datetime DESC LIMIT ?"
    rows = _db().query(sql, (*params, q.limit + 1))
    truncated = len(rows) > q.limit
    rows = rows[: q.limit]
    status = next(p for p in _provider_status() if p["id"] == "firms")
    return jsonify(
        {
            "type": "FeatureCollection",
            "features": [_detection_feature(r) for r in rows],
            "meta": {
                "count": len(rows), "truncated": truncated, "since": iso(since), "until": iso(until),
                "provider": "NASA FIRMS", "products": ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"],
                "detection_window": load_region_config()["detection_window"],
                "provider_status": status,
                "semantics": "Satellite-detected thermal anomalies (not confirmed wildfires). Times are UTC acquisition times.",
            },
        }
    )


# --------------------------------------------------------------------------- risk
def _latest_date(region: str) -> str | None:
    tz = region_def(region).get("timezone", "UTC")
    today = datetime.now(ZoneInfo(tz)).date().isoformat()
    row = _db().one(
        """SELECT MAX(f.date) AS d FROM fwi_daily f JOIN cells c ON c.id=f.cell_id
           WHERE c.region=? AND f.is_forecast=0 AND f.status='ok' AND f.spinup=0 AND f.date<=?""",
        (region, today),
    )
    return row["d"] if row and row["d"] else None


def _available_dates(region: str) -> list[dict]:
    rows = _db().query(
        """SELECT r.date AS date, MAX(f.is_forecast) AS fc FROM risk_snapshot r JOIN cells c ON c.id=r.cell_id
           JOIN fwi_daily f ON f.cell_id=r.cell_id AND f.date=r.date WHERE c.region=? GROUP BY r.date ORDER BY r.date""",
        (region,),
    )
    return [{"date": r["date"], "is_forecast": bool(r["fc"])} for r in rows]


@bp.get("/risk-map")
def risk_map():
    q = _args(RegionQuery)
    if q.region not in current_app.extensions["incendium_settings"].risk_regions:
        return jsonify({"error": "region_not_enabled", "message": f"Risk grid for '{q.region}' has not been ingested. Enable it with INCENDIUM_RISK_REGIONS."}), 404
    d = q.date.isoformat() if q.date else _latest_date(q.region)
    if d is None:
        return jsonify({"error": "no_data", "message": "No risk data computed yet for this region."}), 404
    rows = _db().query(
        """SELECT c.*, f.ffmc, f.dmc, f.dc, f.isi, f.bui, f.fwi, f.dsr, f.fwi_class, f.is_forecast, f.status AS fwi_status,
                  f.computed_at, r.payload, w.t_max, w.rh_min, w.ws_max, w.ws_noon, w.wd_noon, w.rain24_noon, w.retrieved_at AS weather_retrieved_at,
                  (SELECT COUNT(*) FROM detections dt WHERE dt.cell_id=c.id AND dt.acq_datetime>=?) AS detections_7d
           FROM cells c
           LEFT JOIN fwi_daily f ON f.cell_id=c.id AND f.date=?
           LEFT JOIN risk_snapshot r ON r.cell_id=c.id AND r.date=?
           LEFT JOIN weather_daily w ON w.cell_id=c.id AND w.date=?
           WHERE c.region=? ORDER BY c.lat, c.lon""",
        (iso(utcnow() - timedelta(days=7)), d, d, d, q.region),
    )
    features = []
    is_fc = False
    for r in rows:
        payload = json.loads(r["payload"]) if r["payload"] else None
        comp = payload["composite"] if payload else None
        is_fc = is_fc or bool(r["is_forecast"])
        features.append(
            {
                "type": "Feature",
                "id": r["id"],
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[r["west"], r["south"]], [r["east"], r["south"]], [r["east"], r["north"]], [r["west"], r["north"]], [r["west"], r["south"]]]],
                },
                "properties": {
                    "cell_id": r["id"], "lat": r["lat"], "lon": r["lon"], "overlap": r["overlap"],
                    "fwi": r["fwi"], "fwi_class": r["fwi_class"], "ffmc": r["ffmc"], "dmc": r["dmc"], "dc": r["dc"],
                    "isi": r["isi"], "bui": r["bui"], "fwi_status": r["fwi_status"] or "missing",
                    "composite": comp["score"] if comp else None,
                    "composite_class": comp["class"] if comp else None,
                    "completeness": comp["completeness"] if comp else 0,
                    "missing": comp["missing"] if comp else ["T", "H", "W", "D", "V"],
                    "ndvi": payload["vegetation"]["ndvi"] if payload and payload.get("vegetation") else None,
                    "ndmi": payload["vegetation"]["ndmi"] if payload and payload.get("vegetation") else None,
                    "t_max": r["t_max"], "rh_min": r["rh_min"], "ws_max": r["ws_max"],
                    "ws_noon": r["ws_noon"], "wd_noon": r["wd_noon"], "rain24": r["rain24_noon"],
                    "detections_7d": r["detections_7d"], "is_forecast": bool(r["is_forecast"]),
                    "ml_probability": (payload.get("ml") or {}).get("probability") if payload else None,
                    "ml_in_season": (payload.get("ml") or {}).get("in_training_season") if payload else None,
                },
            }
        )
    meta_row = _db().one(
        "SELECT MAX(computed_at) AS c FROM fwi_daily f JOIN cells c ON c.id=f.cell_id WHERE c.region=?", (q.region,)
    )
    return jsonify(
        {
            "type": "FeatureCollection",
            "features": features,
            "meta": {
                "region": q.region, "date": d, "is_forecast": is_fc, "available_dates": _available_dates(q.region),
                "computed_at": meta_row["c"] if meta_row else None, "grid_deg": region_def(q.region)["grid_deg"],
                "providers": _provider_status(),
                "semantics": "FWI from model weather at local noon; composite is experimental. Neither is an ignition probability.",
            },
        }
    )


@bp.get("/risk-details")
def risk_details():
    q = _args(CellQuery)
    db = _db()
    cell = db.one("SELECT * FROM cells WHERE id=?", (q.cell_id,))
    if not cell:
        return jsonify({"error": "not_found", "message": "Unknown cell."}), 404
    region = cell["region"]
    d = q.date.isoformat() if q.date else _latest_date(region)
    fwi_rows = [dict(r) for r in db.query("SELECT * FROM fwi_daily WHERE cell_id=? AND spinup=0 ORDER BY date", (q.cell_id,))]
    wx_rows = [dict(r) for r in db.query("SELECT * FROM weather_daily WHERE cell_id=? ORDER BY date", (q.cell_id,))]
    veg_rows = [dict(r) for r in db.query("SELECT * FROM veg_stats WHERE cell_id=? ORDER BY interval_from", (q.cell_id,))]
    snap = db.one("SELECT payload, computed_at FROM risk_snapshot WHERE cell_id=? AND date=?", (q.cell_id, d)) if d else None
    payload = json.loads(snap["payload"]) if snap else None
    today_fwi = next((r for r in fwi_rows if r["date"] == d), None)
    today_wx = next((r for r in wx_rows if r["date"] == d), None)
    cutoff = iso(utcnow() - timedelta(days=7))
    pad = 0.35
    near = db.query(
        """SELECT * FROM detections WHERE acq_datetime>=? AND latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
           ORDER BY acq_datetime DESC LIMIT 2000""",
        (cutoff, cell["lat"] - pad, cell["lat"] + pad, cell["lon"] - pad, cell["lon"] + pad),
    )
    nearby = []
    for r in near:
        dist = haversine_km(cell["lat"], cell["lon"], r["latitude"], r["longitude"])
        if dist <= 30:
            f = _detection_feature(r)["properties"]
            f.update({"latitude": r["latitude"], "longitude": r["longitude"], "distance_km": round(dist, 1)})
            nearby.append(f)
    hourly = db.one("SELECT model_lat, model_lon, model_elev, retrieved_at FROM weather_hourly WHERE cell_id=? ORDER BY time_local DESC LIMIT 1", (q.cell_id,))
    return jsonify(
        {
            "cell": dict(cell),
            "date": d,
            "fwi": today_fwi,
            "weather": today_wx,
            "composite": payload["composite"] if payload else None,
            "percentiles": payload["percentiles"] if payload else None,
            "vegetation": payload["vegetation"] if payload else None,
            "explanation": payload["explanation"] if payload else ["No risk assessment available for this date."],
            "weights": payload["weights"] if payload else current_app.extensions["incendium_settings"].composite_weights,
            "fwi_series": [
                {k: r[k] for k in ("date", "ffmc", "dmc", "dc", "isi", "bui", "fwi", "fwi_class", "is_forecast", "status")}
                for r in fwi_rows[-40:]
            ],
            "weather_series": [
                {k: r[k] for k in ("date", "t_noon", "rh_noon", "ws_noon", "rain24_noon", "t_max", "rh_min", "ws_max", "gust_max", "vpd_max", "precip_day", "is_forecast")}
                for r in wx_rows[-40:]
            ],
            "vegetation_series": veg_rows,
            "nearby_detections": nearby,
            "provenance": {
                "weather": {
                    "provider": "Open-Meteo (model)",
                    "model_grid_point": {"lat": hourly["model_lat"], "lon": hourly["model_lon"], "elevation_m": hourly["model_elev"]} if hourly else None,
                    "retrieved_at": today_wx["retrieved_at"] if today_wx else None,
                },
                "fwi": {"computed_at": today_fwi["computed_at"] if today_fwi else None},
                "vegetation": {"provider": "Copernicus Sentinel-2 L2A", "retrieved_at": payload["vegetation"]["retrieved_at"] if payload and payload.get("vegetation") else None},
                "detections": {"provider": "NASA FIRMS", "radius_km": 30, "window_days": 7},
                "risk_computed_at": snap["computed_at"] if snap else None,
            },
        }
    )


@bp.get("/region-summary")
def region_summary():
    q = _args(RegionQuery)
    if q.region not in current_app.extensions["incendium_settings"].risk_regions:
        return jsonify({"error": "region_not_enabled", "message": "Region not ingested."}), 404
    db = _db()
    d = q.date.isoformat() if q.date else _latest_date(q.region)
    if d is None:
        return jsonify({"error": "no_data", "message": "No risk data computed yet."}), 404
    rows = db.query(
        """SELECT c.id, c.lat, c.lon, f.fwi, f.fwi_class, f.ffmc, f.isi, f.bui, f.dc, f.is_forecast, r.payload,
                  w.t_max, w.rh_min, w.ws_max, w.rain24_noon
           FROM cells c LEFT JOIN fwi_daily f ON f.cell_id=c.id AND f.date=?
           LEFT JOIN risk_snapshot r ON r.cell_id=c.id AND r.date=?
           LEFT JOIN weather_daily w ON w.cell_id=c.id AND w.date=?
           WHERE c.region=?""",
        (d, d, d, q.region),
    )
    fwi_counts: dict[str, int] = {}
    comp_counts: dict[str, int] = {"Insufficient data": 0}
    fwis, comps, factor_sums = [], [], {}
    for r in rows:
        if r["fwi_class"]:
            fwi_counts[r["fwi_class"]] = fwi_counts.get(r["fwi_class"], 0) + 1
            fwis.append(r["fwi"])
        p = json.loads(r["payload"]) if r["payload"] else None
        c = p["composite"] if p else None
        if c and c["score"] is not None:
            comp_counts[c["class"]] = comp_counts.get(c["class"], 0) + 1
            comps.append(c["score"])
            for f in c["factors"]:
                factor_sums.setdefault(f["key"], []).append(f["contribution"])
        else:
            comp_counts["Insufficient data"] += 1
    top = sorted((dict(r) for r in rows if r["fwi"] is not None), key=lambda r: -r["fwi"])[:8]
    for t in top:
        t.pop("payload", None)

    def stats(xs):
        xs = [x for x in xs if x is not None]
        if not xs:
            return None
        xs = sorted(xs)
        return {"min": round(xs[0], 2), "median": round(xs[len(xs) // 2], 2), "mean": round(sum(xs) / len(xs), 2), "max": round(xs[-1], 2), "n": len(xs)}

    det = db.one(
        """SELECT SUM(acq_datetime>=?) AS d1, COUNT(*) AS d7 FROM detections
           WHERE cell_id IN (SELECT id FROM cells WHERE region=?) AND acq_datetime>=?""",
        (iso(utcnow() - timedelta(hours=24)), q.region, iso(utcnow() - timedelta(days=7))),
    )
    return jsonify(
        {
            "region": q.region, "date": d, "cells": len(rows),
            "is_forecast": any(r["is_forecast"] for r in rows),
            "fwi": {"class_counts": fwi_counts, "stats": stats(fwis)},
            "composite": {
                "class_counts": comp_counts, "stats": stats(comps),
                "mean_contributions": {k: round(sum(v) / len(v), 1) for k, v in factor_sums.items() if v},
            },
            "weather": {
                "t_max": stats([r["t_max"] for r in rows]), "rh_min": stats([r["rh_min"] for r in rows]),
                "ws_max": stats([r["ws_max"] for r in rows]), "rain24": stats([r["rain24_noon"] for r in rows]),
            },
            "detections": {"last_24h": det["d1"] or 0, "last_7d": det["d7"] or 0},
            "top_cells": top,
            "available_dates": _available_dates(q.region),
            "providers": _provider_status(),
        }
    )


@bp.get("/weather")
def weather():
    q = _args(WeatherQuery)
    tz = region_def(q.region).get("timezone", "UTC")
    target = (datetime.now(ZoneInfo(tz)) + timedelta(hours=q.hour_offset)).strftime("%Y-%m-%dT%H:%M")
    rows = _db().query(
        """SELECT c.id AS cell_id, c.lat, c.lon, wh.* FROM cells c JOIN weather_hourly wh ON wh.cell_id=c.id
           WHERE c.region=? AND wh.time_local=(SELECT MAX(time_local) FROM weather_hourly WHERE cell_id=c.id AND time_local<=?)""",
        (q.region, target),
    )
    now_local = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%dT%H:%M")
    return jsonify(
        {
            "region": q.region,
            "timezone": tz,
            "target_time_local": target,
            "cells": [
                {
                    "cell_id": r["cell_id"], "lat": r["lat"], "lon": r["lon"], "time_local": r["time_local"],
                    "t2m": r["t2m"], "rh2m": r["rh2m"], "ws10": r["ws10"], "wd10": r["wd10"], "gust10": r["gust10"],
                    "precip": r["precip"], "vpd": r["vpd"], "is_forecast": r["time_local"] > now_local,
                    "retrieved_at": r["retrieved_at"],
                }
                for r in rows
            ],
            "units": {"t2m": "°C", "rh2m": "%", "ws10": "km/h", "wd10": "° (direction wind blows FROM)", "gust10": "km/h", "precip": "mm/h", "vpd": "kPa"},
            "semantics": "Numerical weather model values (Open-Meteo best match) sampled at cell centres; not station observations.",
            "provider_status": next(p for p in _provider_status() if p["id"] == "open-meteo"),
        }
    )


@bp.get("/vegetation")
def vegetation():
    q = _args(RegionQuery)
    rows = _db().query(
        """SELECT v.* FROM veg_stats v JOIN cells c ON c.id=v.cell_id WHERE c.region=? ORDER BY v.interval_from""",
        (q.region,),
    )
    latest: dict[str, dict] = {}
    for r in rows:
        if r["valid_fraction"] is None or r["valid_fraction"] < 0.10 or r["ndvi_mean"] is None:
            continue
        latest[r["cell_id"]] = dict(r)
    return jsonify(
        {
            "region": q.region,
            "cells": list(latest.values()),
            "semantics": "Mean NDVI (greenness) and NDMI (vegetation water-content proxy) from cloud-masked Sentinel-2 L2A, most recent 10-day interval with ≥10% valid pixels.",
            "provider_status": next(p for p in _provider_status() if p["id"] == "cdse"),
        }
    )


@bp.get("/summary")
def summary():
    db = _db()
    now = utcnow()
    d24, d7 = iso(now - timedelta(hours=24)), iso(now - timedelta(days=7))
    tot = db.one(
        "SELECT SUM(acq_datetime>=?) AS h24, SUM(acq_datetime>=?) AS d7, MAX(acq_datetime) AS latest, MIN(acq_datetime) AS earliest FROM detections",
        (d24, d7),
    )
    by_state = db.query(
        "SELECT COALESCE(state,'Outside Indian state polygons') AS state, COUNT(*) AS n FROM detections WHERE acq_datetime>=? GROUP BY 1 ORDER BY n DESC LIMIT 12",
        (d24,),
    )
    by_sat = db.query("SELECT satellite, COUNT(*) AS n FROM detections WHERE acq_datetime>=? GROUP BY 1", (d24,))
    by_conf = db.query("SELECT confidence, COUNT(*) AS n FROM detections WHERE acq_datetime>=? GROUP BY 1", (d24,))
    daily = db.query(
        "SELECT substr(acq_datetime,1,10) AS day, COUNT(*) AS n FROM detections WHERE acq_datetime>=? GROUP BY 1 ORDER BY 1",
        (iso(now - timedelta(days=14)),),
    )
    return jsonify(
        {
            "detections": {
                "last_24h": tot["h24"] or 0, "last_7d": tot["d7"] or 0, "latest_acquisition": tot["latest"],
                "earliest_stored": tot["earliest"],
                "by_state_24h": [dict(r) for r in by_state], "by_satellite_24h": [dict(r) for r in by_sat],
                "by_confidence_24h": [dict(r) for r in by_conf], "daily_utc": [dict(r) for r in daily],
            },
            "detection_window": load_region_config()["detection_window"],
            "providers": _provider_status(),
            "generated_at": iso(now),
        }
    )


# --------------------------------------------------------------------------- admin (localhost only)
@bp.post("/admin/refresh/<job>")
def manual_refresh(job: str):
    s = current_app.extensions["incendium_settings"]
    if not s.allow_manual_refresh or request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "forbidden", "message": "Manual refresh is only available locally."}), 403
    if job not in MANUAL_MIN_INTERVAL:
        return jsonify({"error": "not_found", "message": "Unknown job."}), 404
    with _manual_lock:
        last = _manual_last.get(job)
        if last and utcnow() - last < MANUAL_MIN_INTERVAL[job]:
            wait = int((MANUAL_MIN_INTERVAL[job] - (utcnow() - last)).total_seconds())
            return jsonify({"error": "too_soon", "message": f"Please wait {wait}s before refreshing {job} again.", "retry_after_s": wait}), 429
        _manual_last[job] = utcnow()
    ing = _ing()

    def run():
        try:
            if job == "firms":
                ing.ingest_firms()
            elif job == "weather":
                for r in s.risk_regions:
                    ing.ingest_weather(r)
            elif job == "vegetation":
                for r in s.risk_regions:
                    ing.ingest_vegetation(r)
        except Exception:  # pragma: no cover - logged for operators
            current_app.logger.exception("manual refresh failed")

    threading.Thread(target=run, daemon=True, name=f"manual-{job}").start()
    return jsonify({"status": "accepted", "job": job}), 202
