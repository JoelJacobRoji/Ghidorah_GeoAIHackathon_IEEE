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
    return jsonify({"error": "busy", "message": "Another spread or firebreak analysis is still running on the server (they run one at a time). Try again in about a minute."}), 429


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
    compact: bool = False


# Compact mode keeps what an alert card shows and drops the bulky payload parts
# (detection id lists, the full settlement record, outlook cell lists).
_COMPACT_DROP = ("detections", "nearest_settlement", "cells", "dates")
_COMPACT_STATION_KEYS = ("id", "name", "lat", "lon", "phone", "distance_km", "osm_url")


def _alert_json(r, compact: bool = False) -> dict:
    payload = json.loads(r["payload"])
    if compact:
        for k in _COMPACT_DROP:
            payload.pop(k, None)
        if payload.get("fire_stations"):
            payload["fire_stations"] = [{k: s.get(k) for k in _COMPACT_STATION_KEYS} for s in payload["fire_stations"]]
    return {"id": r["id"], "created_at": r["created_at"], "kind": r["kind"], "priority": r["priority"], "title": r["title"],
            "message": r["message"], "lat": r["lat"], "lon": r["lon"], "state": r["state"], "cell_id": r["cell_id"],
            "acknowledged_at": r["acknowledged_at"], "notified_at": r["notified_at"], "notify_status": r["notify_status"],
            **payload}


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
    total = _db().one(sql.replace("SELECT *", "SELECT COUNT(*) n", 1), tuple(params))["n"]
    summary = _db().query(
        "SELECT priority, COUNT(*) n, SUM(acknowledged_at IS NULL) open FROM alerts WHERE created_at>=? GROUP BY priority",
        (iso(utcnow() - timedelta(hours=q.hours)),))
    return jsonify({
        "alerts": [_alert_json(r, q.compact) for r in rows],
        "total": total,
        "summary": {r["priority"]: {"total": r["n"], "open": r["open"]} for r in summary},
        "channels": alerts_mod.channel_config(),
        "operator_actions": _operator_key() is not None,
        "rules": alerts_mod.__doc__.split("Detection rules")[1].strip() if alerts_mod.__doc__ else "",
        "disclaimer": alerts_mod.DISCLAIMER,
        "last_evaluation": _last_eval(),
    })


def _last_eval():
    r = _db().one("SELECT finished_at, status FROM provider_runs WHERE provider='alerts' ORDER BY id DESC LIMIT 1")
    return dict(r) if r else None


# --------------------------------------------------------------------------- operator actions
# Acknowledging alerts and sending an alert email by hand change state or contact people, and
# the site has no user accounts. They therefore need the operator key (INCENDIUM_OPERATOR_KEY,
# set only in the server environment) in the X-Operator-Key header. Direct requests from the
# machine itself (local development, no proxy headers) are allowed without a key.
OPERATOR_KEY_MIN_LEN = 12
OPERATOR_MAX_FAILS = 8
OPERATOR_FAIL_WINDOW = timedelta(minutes=15)
MANUAL_EMAIL_PER_ALERT = timedelta(minutes=10)
MANUAL_EMAIL_PER_HOUR = 20
_op_lock = threading.Lock()
_op_fails: dict[str, list[datetime]] = {}
_manual_sends: list[datetime] = []


def _operator_key() -> str | None:
    from .config import get_secret

    k = get_secret("INCENDIUM_OPERATOR_KEY")
    return k if k and len(k) >= OPERATOR_KEY_MIN_LEN else None


def _client_ip() -> str:
    return (request.headers.get("X-Real-IP") or request.remote_addr or "?").strip()


def _operator_denied():
    """None when the request may perform operator actions, otherwise an error response."""
    import hmac

    proxied = bool(request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For"))
    if request.remote_addr in ("127.0.0.1", "::1") and not proxied:
        return None
    ip, now = _client_ip(), utcnow()
    with _op_lock:
        recent = [t for t in _op_fails.get(ip, []) if now - t < OPERATOR_FAIL_WINDOW]
        _op_fails[ip] = recent
        if len(recent) >= OPERATOR_MAX_FAILS:
            return jsonify({"error": "locked", "message": "Too many wrong operator keys. Try again in 15 minutes."}), 429
    key = _operator_key()
    if not key:
        return jsonify({"error": "operator_disabled",
                        "message": "Operator actions are switched off on this server (INCENDIUM_OPERATOR_KEY is not set)."}), 403
    given = request.headers.get("X-Operator-Key", "")
    if not given:
        return jsonify({"error": "operator_key_required", "message": "Enter the operator key to acknowledge alerts or send emails."}), 401
    if not hmac.compare_digest(given.encode("utf-8"), key.encode("utf-8")):
        with _op_lock:
            _op_fails.setdefault(ip, []).append(now)
        current_app.logger.warning("wrong operator key from %s", ip)
        return jsonify({"error": "invalid_operator_key", "message": "That operator key is not correct."}), 401
    return None


@bp.post("/operator/check")
def operator_check():
    denied = _operator_denied()
    return denied if denied else jsonify({"status": "ok"})


@bp.post("/alerts/<alert_id>/ack")
def ack_alert(alert_id: str):
    denied = _operator_denied()
    if denied:
        return denied
    db = _db()
    if not db.one("SELECT id FROM alerts WHERE id=?", (alert_id,)):
        return jsonify({"error": "not_found", "message": "Unknown alert."}), 404
    with db.connect() as con:
        con.execute("UPDATE alerts SET acknowledged_at=COALESCE(acknowledged_at, ?) WHERE id=?", (iso(utcnow()), alert_id))
    return jsonify({"status": "ok", "id": alert_id})


@bp.post("/alerts/<alert_id>/notify")
def notify_alert(alert_id: str):
    """Email one alert now to the configured recipients (operator only, rate limited)."""
    from . import mailer
    from .config import get_alert_settings

    denied = _operator_denied()
    if denied:
        return denied
    db = _db()
    row = db.one("SELECT * FROM alerts WHERE id=?", (alert_id,))
    if not row:
        return jsonify({"error": "not_found", "message": "Unknown alert."}), 404
    cfg = get_alert_settings()
    if not cfg.email_enabled:
        return jsonify({"error": "email_disabled", "message": "Email is switched off on this server (INCENDIUM_EMAIL_ENABLED=0)."}), 409
    if not cfg.can_send:
        return jsonify({"error": "email_not_configured", "message": "Email is not fully configured on this server (sender, password or recipients missing)."}), 409
    now = utcnow()
    if row["notified_at"] and (row["notify_status"] or "").startswith("sent manually"):
        ago = now - datetime.fromisoformat(row["notified_at"].replace("Z", "+00:00"))
        if ago < MANUAL_EMAIL_PER_ALERT:
            mins = max(1, int((MANUAL_EMAIL_PER_ALERT - ago).total_seconds() // 60) + 1)
            return jsonify({"error": "rate_limited", "message": f"This alert was just emailed. You can send it again in {mins} min."}), 429
    with _op_lock:
        _manual_sends[:] = [t for t in _manual_sends if now - t < timedelta(hours=1)]
        if len(_manual_sends) >= MANUAL_EMAIL_PER_HOUR:
            return jsonify({"error": "rate_limited", "message": f"At most {MANUAL_EMAIL_PER_HOUR} manual emails per hour. Try again later."}), 429
        _manual_sends.append(now)
    try:
        res = alerts_mod._send_email([dict(row)])
    except mailer.MailError as exc:
        with db.connect() as con:
            con.execute("UPDATE alerts SET notify_status=? WHERE id=?", (f"manual send failed: {exc}"[:200], alert_id))
        current_app.logger.error("manual alert email failed: %s", exc)
        return jsonify({"error": "send_failed", "message": f"The email could not be sent: {exc}"}), 502
    status = (f"sent manually to {res['recipients']} recipient(s)" if res["status"] == "sent"
              else f"test mode: written to outbox for {res['recipients']} recipient(s)")
    with db.connect() as con:
        con.execute("UPDATE alerts SET notified_at=?, notify_status=? WHERE id=?", (iso(now), status, alert_id))
    current_app.logger.info("alert %s emailed manually (%s)", alert_id, res["status"])
    return jsonify({"status": res["status"], "recipients": res["recipients"], "notify_status": status})


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
