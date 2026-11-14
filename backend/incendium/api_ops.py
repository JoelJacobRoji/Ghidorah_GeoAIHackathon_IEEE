"""Operations endpoints: spread scenarios, firebreak planning, alerts, places and the ML report."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import alerts as alerts_mod
from . import ml, places
from werkzeug.exceptions import HTTPException

from .api import BadRequest, _args, _bad, _db, _err, _http
from .db import iso, utcnow
from .providers.http import ProviderError

bp = Blueprint("ops", __name__, url_prefix="/api")
_heavy = threading.Semaphore(1)  # one spread/firebreak computation at a time
bp.register_error_handler(BadRequest, _bad)
bp.register_error_handler(HTTPException, _http)
bp.register_error_handler(Exception, _err)


def _settings():
    return current_app.extensions["incendium_settings"]


def _body(model):
    try:
        return model(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        raise BadRequest([{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]) from None


def _busy():
    return jsonify({"error": "busy", "message": "Another scenario is being computed. Try again in a few seconds."}), 429


def _upstream(exc: ProviderError):
    return jsonify({"error": "upstream_unavailable", "message": f"A data provider is unavailable: {exc}"}), 503


# --------------------------------------------------------------------------- Module 4: spread scenario
class SpreadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    start_local: datetime | None = None
    hours: int = Field(default=6, ge=1, le=12)
    curing_pct: float = Field(default=85, ge=40, le=100)


@bp.post("/simulate/spread")
def simulate_spread():
    from . import spread

    b = _body(SpreadBody)
    if not _heavy.acquire(blocking=False):
        return _busy()
    try:
        db = _db()
        inp = spread.inputs_from_db(db, list(_settings().risk_regions), b.lon, b.lat,
                                    b.start_local.replace(tzinfo=None) if b.start_local else None, b.hours, b.curing_pct)
        res = spread.run_scenario(inp, lambda bb: places.in_bbox(db, "settlement", bb),
                                  lambda la, lo: places.nearest(db, "fire_station", la, lo, 3, 80.0))
        res["meta"]["generated_at"] = iso(utcnow())
        return jsonify(res)
    except spread.ScenarioError as exc:
        return jsonify({"error": "unavailable", "message": str(exc)}), 422
    except ProviderError as exc:
        return _upstream(exc)
    finally:
        _heavy.release()


# --------------------------------------------------------------------------- Module 3: firebreaks
class FirebreakBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    radius_km: float = Field(default=6, ge=3, le=12)


@bp.post("/firebreaks")
def firebreaks():
    from . import firebreak
    from .providers import osm

    b = _body(FirebreakBody)
    if not _heavy.acquire(blocking=False):
        return _busy()
    try:
        db = _db()
        res = firebreak.recommend(db, list(_settings().risk_regions), b.lon, b.lat, b.radius_km,
                                  osm.fetch_lines, lambda bb: places.in_bbox(db, "settlement", bb))
        res["meta"]["generated_at"] = iso(utcnow())
        return jsonify(res)
    except firebreak.FirebreakError as exc:
        return jsonify({"error": "unavailable", "message": str(exc)}), 422
    except ProviderError as exc:
        return _upstream(exc)
    finally:
        _heavy.release()


# --------------------------------------------------------------------------- Module 5: alerts
class AlertsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: int = Field(default=48, ge=1, le=720)
    min_priority: str = Field(default="low", pattern="^(low|medium|high)$")
    kind: str | None = Field(default=None, pattern="^(detection|outlook)$")
    include_acknowledged: bool = True
    limit: int = Field(default=500, ge=1, le=2000)


def _alert_json(r) -> dict:
    return {"id": r["id"], "created_at": r["created_at"], "kind": r["kind"], "priority": r["priority"], "title": r["title"],
            "message": r["message"], "lat": r["lat"], "lon": r["lon"], "state": r["state"], "cell_id": r["cell_id"],
            "acknowledged_at": r["acknowledged_at"], "notified_at": r["notified_at"], "notify_status": r["notify_status"],
            **json.loads(r["payload"])}


@bp.get("/alerts")
def list_alerts():
    q = _args(AlertsQuery)
    pr = [p for p, v in alerts_mod.PRIORITY_ORDER.items() if v >= alerts_mod.PRIORITY_ORDER[q.min_priority]]
    sql = f"SELECT * FROM alerts WHERE created_at>=? AND priority IN ({','.join('?' * len(pr))})"
    params: list = [iso(utcnow() - timedelta(hours=q.hours)), *pr]
    if q.kind:
        sql += " AND kind=?"
        params.append(q.kind)
    if not q.include_acknowledged:
        sql += " AND acknowledged_at IS NULL"
    order = " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, acknowledged_at IS NOT NULL, created_at DESC LIMIT ?"
    rows = _db().query(sql + order, (*params, q.limit))
    summary = _db().query(
        "SELECT priority, COUNT(*) n, SUM(acknowledged_at IS NULL) open FROM alerts WHERE created_at>=? GROUP BY priority",
        (iso(utcnow() - timedelta(hours=q.hours)),))
    return jsonify({
        "alerts": [_alert_json(r) for r in rows],
        "summary": {r["priority"]: {"total": r["n"], "open": r["open"]} for r in summary},
        "channels": alerts_mod.channel_config(),
        "rules": alerts_mod.__doc__.split("Detection rules")[1].strip() if alerts_mod.__doc__ else "",
        "disclaimer": alerts_mod.DISCLAIMER,
        "last_evaluation": _last_eval(),
    })


def _last_eval():
    r = _db().one("SELECT finished_at, status FROM provider_runs WHERE provider='alerts' ORDER BY id DESC LIMIT 1")
    return dict(r) if r else None


@bp.post("/alerts/<alert_id>/ack")
def ack_alert(alert_id: str):
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "forbidden", "message": "Acknowledging alerts is only available locally."}), 403
    db = _db()
    if not db.one("SELECT id FROM alerts WHERE id=?", (alert_id,)):
        return jsonify({"error": "not_found", "message": "Unknown alert."}), 404
    with db.connect() as con:
        con.execute("UPDATE alerts SET acknowledged_at=COALESCE(acknowledged_at, ?) WHERE id=?", (iso(utcnow()), alert_id))
    return jsonify({"status": "ok", "id": alert_id})


# --------------------------------------------------------------------------- places
class BboxQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bbox: str

    def parsed(self) -> tuple[float, float, float, float]:
        from .geo import validate_bbox

        parts = [float(x) for x in self.bbox.split(",")]
        if len(parts) != 4:
            raise ValueError("bbox must be w,s,e,n")
        validate_bbox((parts[0], parts[1], parts[2], parts[3]))
        w, s, e, n = parts
        if (e - w) * (n - s) > 400:
            raise ValueError("bbox too large")
        return w, s, e, n


@bp.get("/places/fire-stations")
def fire_stations():
    q = _args(BboxQuery)
    try:
        bb = q.parsed()
    except ValueError as exc:
        raise BadRequest([{"field": "bbox", "message": str(exc)}]) from None
    items = places.in_bbox(_db(), "fire_station", bb)
    return jsonify({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [x["lon"], x["lat"]]}, "properties": x} for x in items],
        "meta": {"count": len(items), "attribution": "© OpenStreetMap contributors (ODbL)", **places.counts(_db())}})


# --------------------------------------------------------------------------- Module 1: ML report
@bp.get("/ml/report")
def ml_report():
    r = ml.load_report()
    if r is None:
        return jsonify({"error": "not_found", "message": "No model has been trained yet. Run `python -m incendium ml-train`."}), 404
    return jsonify(r)
