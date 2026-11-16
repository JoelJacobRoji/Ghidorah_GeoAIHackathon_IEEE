"""Module 5: alert rules, in-app alert feed and optional notifications.

Alerts are decision-support prompts, not official warnings. Incendium never
contacts authorities on its own: it lists the nearest fire stations (OpenStreetMap)
in each alert, and sends notifications only to recipients the operator configures
in the environment (email via SMTP and/or a webhook).

Detection rules (evaluated for new VIIRS detections inside Indian states)
-------------------------------------------------------------------------
Detections within 2 km of each other and acquired within 30 minutes are grouped
into one incident. For each incident:
  R1 near settlement   a town/city within 5 km, or a village/hamlet/suburb within 2 km
  R2 fire weather      the cell's FWI class (calibrated scheme if available) is Very High or above
  R3 intense           total fire radiative power ≥ 50 MW, or ≥ 3 detections
Priority: high = all three rules; medium = two rules; low = one rule.
Incidents meeting none of the rules produce no alert.
Persistent heat sources: if detections occurred within 500 m on at least 5 of the
previous 10 days, the incident is flagged "likely persistent heat source"
(industrial plant, kiln, gas flare) and capped at low priority.

Outlook rule (daily, risk regions)
----------------------------------
  R4 forecast cells in the top two FWI classes (Extreme, Very Extreme) on any of the
     next 3 forecast days -> one "fire-weather outlook" alert per region and day.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

import numpy as np

from . import mailer, places
from .config import get_alert_settings, get_secret
from .db import Database, iso, utcnow
from .log import mask
from .providers.http import make_client

log = logging.getLogger(__name__)

CLUSTER_KM = 2.0
CLUSTER_MINUTES = 30
NEAR_TOWN_KM = 5.0
NEAR_VILLAGE_KM = 2.0
FRP_INTENSE_MW = 50.0
HIGH_FWI = ("Very High", "Extreme", "Very Extreme")
OUTLOOK_FWI = ("Extreme", "Very Extreme")
PRIORITY_ORDER = {"low": 0, "medium": 1, "high": 2}
DISCLAIMER = ("Decision support from satellite thermal anomalies and model weather. Not an official warning; "
              "a detection is not a confirmed wildfire. Verify before acting.")
NOTIFY_MIN_INTERVAL = timedelta(minutes=15)
PERSISTENT_RADIUS_KM = 0.5
PERSISTENT_LOOKBACK_DAYS = 10
PERSISTENT_MIN_DAYS = 5


def _persistent_days(db: Database, lat: float, lon: float, before_iso: str) -> int:
    """Distinct UTC days with a detection within 500 m during the 10 days before `before_iso`."""
    before = datetime.fromisoformat(before_iso.replace("Z", "+00:00"))
    since = iso(before - timedelta(days=PERSISTENT_LOOKBACK_DAYS))
    d = PERSISTENT_RADIUS_KM / 111.0
    rows = db.query(
        """SELECT DISTINCT substr(acq_datetime, 1, 10) day FROM detections
           WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? AND acq_datetime>=? AND acq_datetime<?""",
        (lat - d, lat + d, lon - d * 1.1, lon + d * 1.1, since, iso(before - timedelta(hours=12))))
    return len(rows)


# ---------------------------------------------------------------------------- incidents
def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math

    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(h))


def cluster(dets: list[dict]) -> list[list[dict]]:
    """Single-linkage grouping (≤ CLUSTER_KM and ≤ CLUSTER_MINUTES apart) with spatial buckets + union-find."""
    n = len(dets)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cell = CLUSTER_KM / 111.0  # bucket size in degrees (>= link distance in latitude)
    times = [datetime.fromisoformat(d["acq_datetime"].replace("Z", "+00:00")).timestamp() for d in dets]
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, d in enumerate(dets):
        buckets.setdefault((int(d["latitude"] // cell), int(d["longitude"] // cell)), []).append(i)
    for (bi, bj), members in buckets.items():
        cand = [k for di in (-1, 0, 1) for dj in (-2, -1, 0, 1, 2) for k in buckets.get((bi + di, bj + dj), [])]
        for i in members:
            for k in cand:
                if k <= i or find(i) == find(k):
                    continue
                if abs(times[i] - times[k]) <= CLUSTER_MINUTES * 60 and                         _km(dets[i]["latitude"], dets[i]["longitude"], dets[k]["latitude"], dets[k]["longitude"]) <= CLUSTER_KM:
                    parent[find(i)] = find(k)
    groups: dict[int, list[dict]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(dets[i])
    return list(groups.values())


def evaluate_incident(db: Database, group: list[dict], fwi_today: dict[str, str | None]) -> dict | None:
    lat = float(np.mean([d["latitude"] for d in group]))
    lon = float(np.mean([d["longitude"] for d in group]))
    frp = float(sum(d["frp"] or 0.0 for d in group))
    town = places.nearest(db, "settlement", lat, lon, 1, NEAR_TOWN_KM, ("city", "town"))
    village = places.nearest(db, "settlement", lat, lon, 1, NEAR_VILLAGE_KM, ("village", "hamlet", "suburb"))
    cell_id = next((d["cell_id"] for d in group if d.get("cell_id")), None)
    fwi_class = fwi_today.get(cell_id) if cell_id else None
    r1 = bool(town or village)
    r2 = fwi_class in HIGH_FWI
    r3 = frp >= FRP_INTENSE_MW or len(group) >= 3
    if not (r1 or r2 or r3):
        return None
    n_rules = int(r1) + int(r2) + int(r3)
    priority = {3: "high", 2: "medium", 1: "low"}[n_rules]
    persistent_days = _persistent_days(db, lat, lon, min(d["acq_datetime"] for d in group))
    persistent = persistent_days >= PERSISTENT_MIN_DAYS
    if persistent:
        priority = "low"
    near = (town or village or [None])[0]
    stations = places.nearest(db, "fire_station", lat, lon, 3, 80.0)
    first = min(d["acq_datetime"] for d in group)
    state = next((d["state"] for d in group if d.get("state")), None)
    rules = []
    if r1:
        rules.append({"id": "R1", "text": f"{len(group)} detection(s) {near['distance_km']:.1f} km from {near['name'] or 'a settlement'} ({near['subtype']})"})
    if r2:
        rules.append({"id": "R2", "text": f"cell fire weather today: FWI class {fwi_class}"})
    if r3:
        rules.append({"id": "R3", "text": f"intense: {len(group)} detection(s), total FRP {frp:.0f} MW"})
    place = near["name"] if near and near.get("name") else (state or "unknown area")
    title = f"Thermal anomaly near {place}" if r1 else f"Thermal anomaly in {state or 'monitored area'}"
    lines = [f"{title} — {priority.upper()} priority.", *[f"• {r['text']}" for r in rules]]
    if persistent:
        lines.append(f"• likely persistent heat source: detections within 500 m on {persistent_days} of the previous 10 days")
    if stations:
        lines.append("Nearest fire stations (OpenStreetMap, straight-line): " + "; ".join(
            f"{s['name'] or 'unnamed'} {s['distance_km']:.0f} km" + (f" ({s['phone']})" if s.get("phone") else "") for s in stations))
    lines.append(f"Location {lat:.4f}, {lon:.4f}; first detection {first} UTC.")
    lines.append(DISCLAIMER)
    aid = "det-" + hashlib.sha1(",".join(sorted(d["id"] for d in group)).encode()).hexdigest()[:16]
    return {
        "id": aid, "kind": "detection", "priority": priority, "title": title, "message": "\n".join(lines),
        "lat": lat, "lon": lon, "state": state, "cell_id": cell_id,
        "payload": {"rules": rules, "detections": [d["id"] for d in group], "detection_count": len(group), "frp_total_mw": round(frp, 1),
                    "first_acq": first, "fwi_class": fwi_class, "persistent_days": persistent_days, "persistent_source": persistent, "nearest_settlement": near, "fire_stations": stations},
    }


def _fwi_today(db: Database) -> dict[str, str | None]:
    rows = db.query(
        """SELECT f.cell_id, f.fwi_class FROM fwi_daily f JOIN (
               SELECT cell_id, MAX(date) d FROM fwi_daily WHERE is_forecast=0 AND status='ok' AND spinup=0 GROUP BY cell_id
           ) m ON m.cell_id=f.cell_id AND m.d=f.date""")
    return {r["cell_id"]: r["fwi_class"] for r in rows}


def evaluate_detections(db: Database, hours: int = 6) -> dict:
    since = iso(utcnow() - timedelta(hours=hours))
    dets = [dict(r) for r in db.query(
        "SELECT id, latitude, longitude, acq_datetime, frp, confidence, state, cell_id FROM detections WHERE acq_datetime>=? AND state IS NOT NULL",
        (since,))]
    if not dets:
        return {"incidents": 0, "new_alerts": 0}
    fwi = _fwi_today(db)
    existing = {r["id"] for r in db.query("SELECT id FROM alerts WHERE created_at>=?", (iso(utcnow() - timedelta(days=3)),))}
    # Detections already covered by an alert are not re-alerted (an incident may grow between runs).
    covered: set[str] = set()
    for r in db.query("SELECT payload FROM alerts WHERE kind='detection' AND created_at>=?", (iso(utcnow() - timedelta(days=2)),)):
        covered.update(json.loads(r["payload"]).get("detections", []))
    new = []
    groups = cluster(dets)
    for g in groups:
        if all(d["id"] in covered for d in g):
            continue
        a = evaluate_incident(db, g, fwi)
        if a and a["id"] not in existing:
            new.append(a)
    _store(db, new)
    return {"incidents": len(groups), "new_alerts": len(new), "by_priority": _count(new)}


def evaluate_outlook(db: Database, region: str) -> dict:
    today = datetime.now(timezone.utc).date()
    rows = db.query(
        """SELECT f.cell_id, f.date, f.fwi, f.fwi_class, c.lat, c.lon FROM fwi_daily f JOIN cells c ON c.id=f.cell_id
           WHERE c.region=? AND f.is_forecast=1 AND f.date>? AND f.date<=? AND f.status='ok'""",
        (region, today.isoformat(), (today + timedelta(days=3)).isoformat()))
    hot = [dict(r) for r in rows if r["fwi_class"] in OUTLOOK_FWI]
    if not hot:
        return {"new_alerts": 0}
    aid = f"outlook-{region}-{today.isoformat()}"
    if db.one("SELECT id FROM alerts WHERE id=?", (aid,)):
        return {"new_alerts": 0}
    cells = sorted({h["cell_id"] for h in hot})
    dates = sorted({h["date"] for h in hot})
    peak = max(hot, key=lambda h: h["fwi"] or 0)
    title = f"Fire-weather outlook: {len(cells)} cell(s) in the top FWI classes"
    msg = "\n".join([
        f"{title} ({region.title()}, forecast {dates[0]} to {dates[-1]}).",
        f"Peak forecast FWI {peak['fwi']:.0f} ({peak['fwi_class']}) at {peak['lat']:.3f}, {peak['lon']:.3f} on {peak['date']}.",
        "Forecast fire weather only; it does not predict ignitions.", DISCLAIMER,
    ])
    a = {"id": aid, "kind": "outlook", "priority": "medium" if len(cells) >= 5 else "low", "title": title, "message": msg,
         "lat": peak["lat"], "lon": peak["lon"], "state": None, "cell_id": peak["cell_id"],
         "payload": {"rules": [{"id": "R4", "text": f"{len(cells)} forecast cell(s) Extreme or above"}], "cells": cells, "dates": dates,
                     "peak": {k: peak[k] for k in ("cell_id", "date", "fwi", "fwi_class")}}}
    _store(db, [a])
    return {"new_alerts": 1}


def _count(alerts: list[dict]) -> dict:
    out = {"low": 0, "medium": 0, "high": 0}
    for a in alerts:
        out[a["priority"]] += 1
    return out


def _store(db: Database, alerts: list[dict]) -> None:
    if not alerts:
        return
    now = iso(utcnow())
    with db.connect() as con:
        con.executemany(
            """INSERT OR IGNORE INTO alerts(id, created_at, kind, priority, title, message, lat, lon, state, cell_id, payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [(a["id"], now, a["kind"], a["priority"], a["title"], a["message"], a["lat"], a["lon"], a["state"], a["cell_id"],
              json.dumps(a["payload"])) for a in alerts],
        )


# ---------------------------------------------------------------------------- notifications
def channel_config() -> dict:
    """Which channels are configured (presence only; never values)."""
    email = get_alert_settings().can_send  # INCENDIUM_EMAIL_ENABLED + recipients + SMTP (or test mode)
    webhook = bool(get_secret("INCENDIUM_ALERT_WEBHOOK_URL"))
    min_p = (get_secret("INCENDIUM_ALERT_MIN_PRIORITY") or "medium").lower()
    return {"email": email, "webhook": webhook, "min_priority": min_p if min_p in PRIORITY_ORDER else "medium",
            "automatic_authority_contact": False}


def notify_pending(db: Database) -> dict:
    cfg = channel_config()
    if not (cfg["email"] or cfg["webhook"]):
        return {"sent": 0, "reason": "no channels configured"}
    last = db.one("SELECT MAX(notified_at) t FROM alerts WHERE notify_status='sent'")
    if last and last["t"]:
        if utcnow() - datetime.fromisoformat(last["t"].replace("Z", "+00:00")) < NOTIFY_MIN_INTERVAL:
            return {"sent": 0, "reason": "rate limited"}
    minp = PRIORITY_ORDER[cfg["min_priority"]]
    pending = [dict(r) for r in db.query(
        "SELECT * FROM alerts WHERE notified_at IS NULL AND created_at>=? ORDER BY created_at", (iso(utcnow() - timedelta(hours=12)),))
        if PRIORITY_ORDER[r["priority"]] >= minp]
    if not pending:
        return {"sent": 0, "reason": "nothing pending"}
    status, errors = "sent", []
    if cfg["email"]:
        try:
            _send_email(pending)
        except mailer.MailError as exc:
            errors.append(f"email: {mask(str(exc))}")
    if cfg["webhook"]:
        try:
            _send_webhook(pending)
        except Exception as exc:  # noqa: BLE001 - any failure is recorded, never raised into the scheduler
            errors.append(f"webhook: {mask(type(exc).__name__)}")
    if errors and len(errors) == int(cfg["email"]) + int(cfg["webhook"]):
        status = "failed"
    now = iso(utcnow())
    with db.connect() as con:
        con.executemany("UPDATE alerts SET notified_at=?, notify_status=? WHERE id=?",
                        [(now, status if not errors else f"{status}: {'; '.join(errors)}"[:200], a["id"]) for a in pending])
    return {"sent": len(pending) if status == "sent" else 0, "status": status, "errors": errors}


def _send_email(alerts: list[dict]) -> dict:
    cfg = get_alert_settings()
    top = max(alerts, key=lambda a: PRIORITY_ORDER[a["priority"]])
    subject = f"[Incendium] {len(alerts)} alert(s), highest {top['priority']}: {top['title']}"
    body = "\n\n".join(f"[{a['priority'].upper()}] {a['message']}" for a in alerts) + f"\n\n{DISCLAIMER}\n{cfg.public_url}/alerts"
    return mailer.send(cfg, subject, body)


def _send_webhook(alerts: list[dict]) -> None:
    url = get_secret("INCENDIUM_ALERT_WEBHOOK_URL")
    payload = {"source": "incendium", "disclaimer": DISCLAIMER, "alerts": [
        {k: a[k] for k in ("id", "kind", "priority", "title", "message", "lat", "lon", "state", "created_at")} for a in alerts]}
    with make_client(timeout=30) as client:
        resp = client.post(url, json=payload)
    if resp.status_code >= 300:
        raise RuntimeError(f"HTTP {resp.status_code}")


def run_cycle(db: Database, regions: list[str], outlook: bool = False) -> dict:
    """Scheduler entry point: evaluate rules, then send pending notifications (if configured)."""
    run = db.start_run("alerts", "evaluate")
    try:
        res = {"detections": evaluate_detections(db)}
        if outlook:
            res["outlook"] = {r: evaluate_outlook(db, r) for r in regions}
        res["notify"] = notify_pending(db)
    except Exception as exc:
        db.finish_run(run, "error", 0, mask(str(exc))[:300])
        raise
    db.finish_run(run, "ok", res["detections"].get("new_alerts", 0), details=res)
    return res
