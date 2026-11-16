"""Fire Risk Index email alerts (extension of Module 5).

Index monitored: the composite risk score (0-100) that the risk engine stores per
0.25° grid cell and day (risk.py; classes Low < 25 ≤ Moderate < 50 ≤ High < 75 ≤
Very High). The score is not changed here; this module only watches it.

When: at the end of every risk computation (Ingestor.compute_risk), which the
scheduler runs after each 3-hourly weather update and each vegetation update.
Only today's value (region-local date) is evaluated; forecast days are not.

Trigger, per cell (each cell is an independent area with its own state):
  previous valid index < FIRE_ALERT_THRESHOLD <= current index, and the cell is not
  already in an active alert.
  * While the index stays at or above the threshold, no further email (alert_active).
  * When it falls below, the cell is re-armed; a later crossing alerts again.
  * Cooldown: a cell that was notified less than ALERT_COOLDOWN hours ago is not
    notified again even if it re-crosses (recorded as suppressed_cooldown).
  * Missing or invalid values are skipped and do not change the stored index.
  * A cell seen for the first time (fresh install, new cell, or changed threshold)
    only initialises its state: no email at startup.
All cells that cross in the same computation go into one email, so recipients get
one message per update, not one per cell.

Delivery runs on a background thread so map updates are never delayed; failures are
logged and recorded in risk_alert_events and never raised into the risk engine.
"""
from __future__ import annotations

import html
import json
import logging
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import mailer
from .config import AlertSettings, get_alert_settings
from .db import Database, iso, utcnow
from .geo import region_def
from .risk import composite_class

log = logging.getLogger(__name__)

MAX_DETAILED_AREAS = 10
HOTSPOT_LOOKBACK_H = 24
RECOMMENDED_ACTIONS = (
    "Increase monitoring of the affected area(s), including the Incendium map and satellite detections over the next 24-48 hours.",
    "Notify the relevant local forest department and emergency response authorities so they can review conditions.",
    "Inspect vulnerable vegetation zones, fire lines and access routes near settlements and roads.",
    "Discourage open burning (crop residue, waste) in and near the affected area while conditions persist.",
    "Follow official disaster management and forest department instructions.",
)
DISCLAIMER = ("This is an automated decision-support message from a weather-and-vegetation risk indicator. "
              "It is not an official warning, and a high risk score does not mean a fire has been detected or confirmed.")
_threads: list[threading.Thread] = []


@dataclass
class Area:
    cell_id: str
    index: float
    prev: float | None
    risk_class: str | None


def _valid(score) -> float | None:
    try:
        v = float(score)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and 0.0 <= v <= 100.0 else None


def _today(region: str, now: datetime | None = None) -> str:
    tz = ZoneInfo(region_def(region).get("timezone", "UTC"))
    return (now or datetime.now(timezone.utc)).astimezone(tz).date().isoformat()


# ---------------------------------------------------------------------------- evaluation
def evaluate(db: Database, region: str, cfg: AlertSettings | None = None, now: datetime | None = None,
             sync: bool = False) -> dict:
    """Compare today's index per cell with the stored state; queue one email for new crossings."""
    cfg = cfg or get_alert_settings()
    thr = cfg.fire_alert_threshold
    now = now or utcnow()
    now_s = iso(now)
    today = _today(region, now)
    snaps = {r["cell_id"]: r["payload"] for r in db.query(
        "SELECT s.cell_id, s.payload FROM risk_snapshot s JOIN cells c ON c.id=s.cell_id WHERE c.region=? AND s.date=?",
        (region, today))}
    states = {r["area_id"]: dict(r) for r in db.query("SELECT * FROM risk_alert_state WHERE region=?", (region,))}
    cooldown = timedelta(hours=cfg.alert_cooldown_hours)

    crossed: list[Area] = []
    suppressed: list[Area] = []
    missing = initialised = 0
    updates: list[tuple] = []
    for cell_id, payload in snaps.items():
        try:
            score = _valid(((json.loads(payload).get("composite") or {}).get("score")))
        except (ValueError, TypeError):
            score = None
        st = states.get(cell_id)
        if score is None:
            missing += 1
            if st:
                updates.append(("touch", cell_id, now_s))
            continue
        cls = composite_class(score)
        if st is None:
            # First sighting (fresh install or new cell): initialise silently, never email at startup.
            initialised += 1
            updates.append(("upsert", cell_id, region, score, cls, today, now_s, int(score >= thr), thr, None, None, None))
            continue
        if st["threshold"] != thr:
            # Threshold changed: re-derive the alert state from the last value against the new threshold.
            st = {**st, "alert_active": int(st["last_index"] is not None and st["last_index"] >= thr), "threshold": thr}
        prev = st["last_index"]
        active = int(st["alert_active"])
        if score < thr:
            active = 0  # re-arm
        elif prev is not None and prev < thr and not active:
            active = 1
            area = Area(cell_id, score, prev, cls)
            last_n = st["last_notified_at"]
            if last_n and now - datetime.fromisoformat(last_n.replace("Z", "+00:00")) < cooldown:
                suppressed.append(area)
            else:
                crossed.append(area)
        updates.append(("upsert", cell_id, region, score, cls, today, now_s, active, thr,
                        st["last_notified_at"], st["last_notified_index"], st["last_notified_class"]))

    with db.connect() as con:
        for u in updates:
            if u[0] == "touch":
                con.execute("UPDATE risk_alert_state SET last_evaluated_at=? WHERE area_id=?", (u[2], u[1]))
            else:
                con.execute(
                    """INSERT INTO risk_alert_state(area_id, region, last_index, last_class, last_date, last_evaluated_at,
                           alert_active, threshold, last_notified_at, last_notified_index, last_notified_class)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(area_id) DO UPDATE SET region=excluded.region, last_index=excluded.last_index,
                           last_class=excluded.last_class, last_date=excluded.last_date, last_evaluated_at=excluded.last_evaluated_at,
                           alert_active=excluded.alert_active, threshold=excluded.threshold""", u[1:])

    res = {"date": today, "threshold": thr, "cells": len(snaps), "missing": missing, "initialised": initialised,
           "crossed": [a.cell_id for a in crossed], "suppressed_cooldown": [a.cell_id for a in suppressed]}
    if suppressed:
        _record(db, region, suppressed, thr, "suppressed_cooldown", 0, None,
                f"crossed again within the {cfg.alert_cooldown_hours:g} h cooldown")
        log.info("risk alert: %d cell(s) crossed %.0f within cooldown; not notified", len(suppressed), thr)
    if crossed:
        res["notification"] = notify(db, region, crossed, cfg, now, sync=sync)
    return res


def _record(db: Database, region: str, areas: list[Area], thr: float, status: str, recipients: int,
            subject: str | None, message: str | None) -> int:
    with db.connect() as con:
        cur = con.execute(
            """INSERT INTO risk_alert_events(created_at, region, area_ids, max_index, threshold, status, recipients, subject, message)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (iso(utcnow()), region, json.dumps([a.cell_id for a in areas]), max(a.index for a in areas), thr, status,
             recipients, subject, (message or "")[:300] or None))
        return int(cur.lastrowid)


def notify(db: Database, region: str, areas: list[Area], cfg: AlertSettings, now: datetime | None = None,
           sync: bool = False, test: bool = False) -> dict:
    areas = sorted(areas, key=lambda a: -a.index)
    subject, text, body_html = build_email(db, region, areas, cfg, now or utcnow(), test=test)
    if not cfg.email_enabled and not test:
        _record(db, region, areas, cfg.fire_alert_threshold, "disabled", 0, subject, "INCENDIUM_EMAIL_ENABLED is off")
        log.info("risk alert for %d cell(s) not emailed: email notifications are disabled", len(areas))
        return {"status": "disabled", "subject": subject}
    if not cfg.alert_recipients or not (cfg.email_test_mode or cfg.smtp_ready):
        msg = "no recipients configured" if not cfg.alert_recipients else "SMTP not configured"
        _record(db, region, areas, cfg.fire_alert_threshold, "failed", 0, subject, msg)
        log.error("risk alert for %d cell(s) not sent: %s", len(areas), msg)
        return {"status": "failed", "error": msg, "subject": subject}
    event = _record(db, region, areas, cfg.fire_alert_threshold, "queued", len(cfg.alert_recipients), subject, None)
    if not test:
        with db.connect() as con:
            con.executemany(
                "UPDATE risk_alert_state SET last_notified_at=?, last_notified_index=?, last_notified_class=? WHERE area_id=?",
                [(iso(now or utcnow()), a.index, a.risk_class, a.cell_id) for a in areas])
    if sync:
        _deliver(db, event, cfg, subject, text, body_html)
    else:
        t = threading.Thread(target=_deliver, args=(db, event, cfg, subject, text, body_html), name=f"risk-alert-{event}", daemon=False)
        _threads.append(t)
        t.start()
    return {"status": "queued", "event_id": event, "subject": subject, "recipients": len(cfg.alert_recipients)}


def _deliver(db: Database, event: int, cfg: AlertSettings, subject: str, text: str, body_html: str) -> None:
    try:
        r = mailer.send(cfg, subject, text, body_html)
        status, n, msg = r["status"], r["recipients"], r.get("file")
    except mailer.MailError as exc:
        status, n, msg = "failed", 0, str(exc)
        log.error("risk alert email failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 - never propagate into the scheduler
        status, n, msg = "failed", 0, f"unexpected {type(exc).__name__}"
        log.exception("risk alert email failed unexpectedly")
    try:
        with db.connect() as con:
            con.execute("UPDATE risk_alert_events SET status=?, recipients=?, message=?, finished_at=? WHERE id=?",
                        (status, n, (msg or "")[:300] or None, iso(utcnow()), event))
    except Exception:  # noqa: BLE001
        log.exception("could not record risk alert delivery status")


def wait_pending(timeout: float = 60.0) -> None:
    """Let queued deliveries finish (used by the command line before exiting)."""
    for t in list(_threads):
        t.join(timeout)
        if not t.is_alive():
            _threads.remove(t)


# ---------------------------------------------------------------------------- email content
def _area_details(db: Database, region: str, a: Area, now: datetime) -> dict:
    from . import places

    cell = db.one("SELECT * FROM cells WHERE id=?", (a.cell_id,))
    today = _today(region, now)
    snap = db.one("SELECT payload FROM risk_snapshot WHERE cell_id=? AND date=?", (a.cell_id, today))
    p = json.loads(snap["payload"]) if snap else {}
    factors = {f["key"]: f for f in (p.get("composite") or {}).get("factors", [])}
    fwi = db.one("SELECT fwi, fwi_class FROM fwi_daily WHERE cell_id=? AND date=? AND status='ok'", (a.cell_id, today))
    wind_dir = db.one(
        """SELECT wd10, ws10 FROM weather_hourly WHERE cell_id=? AND time_local BETWEEN ? AND ? AND wd10 IS NOT NULL
           ORDER BY ws10 DESC LIMIT 1""", (a.cell_id, f"{today}T12:00", f"{today}T17:00"))
    since = iso(now - timedelta(hours=HOTSPOT_LOOKBACK_H))
    hot = db.one("SELECT COUNT(*) n, MAX(frp) frp, MAX(acq_datetime) last FROM detections WHERE cell_id=? AND acq_datetime>=?",
                 (a.cell_id, since))
    lat, lon = (cell["lat"], cell["lon"]) if cell else (None, None)
    near = places.nearest(db, "settlement", lat, lon, k=1, max_km=40, subtypes=("city", "town")) if cell else []
    village = places.nearest(db, "settlement", lat, lon, k=1, max_km=20) if cell else []
    contrib = sorted((f for f in factors.values() if f.get("contribution") is not None), key=lambda f: -f["contribution"])
    return {
        "area": a, "cell": dict(cell) if cell else None, "lat": lat, "lon": lon,
        "town": near[0] if near else None, "village": village[0] if village else None,
        "T": factors.get("T", {}).get("value"), "H": factors.get("H", {}).get("value"), "W": factors.get("W", {}).get("value"),
        "D": factors.get("D", {}).get("value"), "ndmi": (p.get("vegetation") or {}).get("ndmi"),
        "ndvi": (p.get("vegetation") or {}).get("ndvi"), "veg_to": (p.get("vegetation") or {}).get("interval_to"),
        "wind_from": wind_dir["wd10"] if wind_dir else None,
        "fwi": fwi["fwi"] if fwi else None, "fwi_class": fwi["fwi_class"] if fwi else None,
        "hot_n": hot["n"] if hot else 0, "hot_frp": hot["frp"] if hot else None, "hot_last": hot["last"] if hot else None,
        "ml": (p.get("ml") or {}).get("probability"), "contrib": contrib[:3], "explanation": p.get("explanation") or [],
    }


def _compass(deg: float | None) -> str:
    if deg is None:
        return ""
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int(((deg % 360) + 22.5) // 45) % 8]


def _fmt(v, unit: str = "", nd: int = 0) -> str:
    if v is None:
        return "not available"
    return f"{v:.{nd}f}{unit}" if isinstance(v, (int, float)) else f"{v}{unit}"


def _place(d: dict) -> str:
    t, v = d["town"], d["village"]
    parts = []
    if v and v.get("name"):
        parts.append(f"{v['name']} ({v['subtype']}, {v['distance_km']:.0f} km)")
    if t and t.get("name") and (not v or t["id"] != v["id"]):
        parts.append(f"near {t['name']} ({t['distance_km']:.0f} km)")
    return ", ".join(parts) or "no mapped settlement within 20 km"


def _rows(d: dict, cfg: AlertSettings) -> list[tuple[str, str]]:
    wind = _fmt(d["W"], " km/h", 0) + (f" from {_compass(d['wind_from'])} ({d['wind_from']:.0f}°)" if d["wind_from"] is not None else "")
    hot = ("none in the last 24 h" if not d["hot_n"] else
           f"{d['hot_n']} VIIRS detection(s) in the last 24 h, max FRP {_fmt(d['hot_frp'], ' MW', 1)} (not confirmed fires)")
    return [
        ("Fire Risk Index", f"{d['area'].index:.1f} / 100 ({d['area'].risk_class})"),
        ("Previous value", _fmt(d["area"].prev, "", 1) + f" (threshold {cfg.fire_alert_threshold:g})"),
        ("Location", _place(d)),
        ("Grid cell (0.25°)", f"{d['area'].cell_id}, centre {_fmt(d['lat'], '', 3)}°N {_fmt(d['lon'], '', 3)}°E"),
        ("Max temperature", _fmt(d["T"], " °C", 1)),
        ("Min relative humidity", _fmt(d["H"], " %", 0)),
        ("Max wind", wind),
        ("Days since ≥5 mm rain", _fmt(d["D"], " days", 0)),
        ("Vegetation (Sentinel-2)", "not available" if d["ndmi"] is None else
         f"NDMI {d['ndmi']:.2f}, NDVI {_fmt(d['ndvi'], '', 2)} (to {d['veg_to']})"),
        ("Fire Weather Index", "not available" if d["fwi"] is None else f"{d['fwi']:.1f} ({d['fwi_class']})"),
        ("Active fire hotspots", hot),
    ]


def _why(d: dict, cfg: AlertSettings, test: bool = False) -> str:
    a = d["area"]
    thr = cfg.fire_alert_threshold
    if test or a.prev is None:
        rel = "at or above" if a.index >= thr else "below"
        parts = [f"The Fire Risk Index is {a.index:.1f}, {rel} the alert threshold of {thr:g}."]
    else:
        parts = [f"The Fire Risk Index rose from {a.prev:.1f} at the previous update to {a.index:.1f}, crossing the alert threshold of {thr:g}."]
    if d["contrib"]:
        parts.append("Largest contributions: " + ", ".join(
            f"{f['label'].lower()} ({f['contribution']:.0f} pts; {_fmt(f['value'], ' ' + f['unit'] if f['unit'] else '', 1)})"
            for f in d["contrib"]) + ".")
    fwi_line = next((e for e in d["explanation"] if e.startswith("Fire Weather Index")), None)
    if fwi_line:
        parts.append(fwi_line)
    return " ".join(parts)


def build_email(db: Database, region: str, areas: list[Area], cfg: AlertSettings, now: datetime,
                test: bool = False) -> tuple[str, str, str]:
    rname = region_def(region).get("name", region)
    tz = ZoneInfo(region_def(region).get("timezone", "UTC"))
    stamp = now.astimezone(tz).strftime("%d %b %Y, %H:%M %Z")
    details = [_area_details(db, region, a, now) for a in areas[:MAX_DETAILED_AREAS]]
    top = details[0]
    top_place = (top["town"] or top["village"] or {}).get("name")
    cls = areas[0].risk_class or "High"
    where = f"near {top_place}, {rname}" if top_place else rname
    subject = (f"[WILDFIRE ALERT] {cls} Fire Risk Detected – {where}" if len(areas) == 1 else
               f"[WILDFIRE ALERT] {cls} Fire Risk Detected – {len(areas)} areas in {rname} (highest {where.split(',')[0]})")
    if test:
        subject = "[TEST] " + subject
    app = f"{cfg.public_url}/dashboard?region={region}&cell={areas[0].cell_id}"

    # ---- plain text
    t = []
    if test:
        t += ["*** TEST MESSAGE: generated on request to check the alert email. No threshold crossing triggered it. ***", ""]
    t += [f"{cls.upper()} FIRE RISK ALERT – {rname}", f"Generated {stamp}", "",
          (f"Test using today's {len(areas)} highest-index area(s); the alert threshold is {cfg.fire_alert_threshold:g} (scale 0-100)."
           if test else
           f"{len(areas)} area(s) crossed the Fire Risk Index alert threshold of {cfg.fire_alert_threshold:g} (scale 0-100; "
           f"{composite_class(cfg.fire_alert_threshold)} class starts at {cfg.fire_alert_threshold:g})."), ""]
    for i, d in enumerate(details, 1):
        t.append(f"AREA {i}")
        t += [f"  {k}: {v}" for k, v in _rows(d, cfg)]
        t += [f"  Why: {_why(d, cfg, test)}", ""]
    if len(areas) > MAX_DETAILED_AREAS:
        t += [f"...and {len(areas) - MAX_DETAILED_AREAS} more area(s): " + ", ".join(a.cell_id for a in areas[MAX_DETAILED_AREAS:]), ""]
    t += ["RECOMMENDED ACTIONS"] + [f"  - {r}" for r in RECOMMENDED_ACTIONS] + [""]
    t += [f"Open the Incendium wildfire monitoring platform: {app}", "", DISCLAIMER,
          "Index: Incendium experimental composite of temperature, humidity, wind, days since rain and vegetation moisture "
          "(model weather from Open-Meteo, vegetation from Copernicus Sentinel-2, detections from NASA FIRMS).",
          "You receive this because your address is on the alert recipient list configured by the Incendium operator."]
    text = "\n".join(t)

    # ---- HTML
    e = html.escape
    cards = []
    for i, d in enumerate(details, 1):
        rows = "".join(f'<tr><td style="padding:4px 12px 4px 0;color:#5b6366;white-space:nowrap;vertical-align:top">{e(k)}</td>'
                       f'<td style="padding:4px 0;color:#1d2527">{e(v)}</td></tr>' for k, v in _rows(d, cfg))
        cards.append(
            f'<div style="border:1px solid #e3ded2;border-radius:10px;padding:14px 16px;margin:0 0 14px">'
            f'<div style="font-weight:600;font-size:15px;margin-bottom:6px">Area {i}: {e(_place(d))}</div>'
            f'<table style="border-collapse:collapse;font-size:13.5px">{rows}</table>'
            f'<p style="margin:10px 0 0;font-size:13.5px;color:#1d2527"><b>Why:</b> {e(_why(d, cfg, test))}</p></div>')
    more = (f'<p style="font-size:13px;color:#5b6366">…and {len(areas) - MAX_DETAILED_AREAS} more area(s): '
            f'{e(", ".join(a.cell_id for a in areas[MAX_DETAILED_AREAS:]))}</p>' if len(areas) > MAX_DETAILED_AREAS else "")
    test_banner = ('<div style="background:#fff3cd;border:1px solid #e0c36b;border-radius:8px;padding:10px 12px;margin-bottom:14px;'
                   'font-size:13px"><b>Test message.</b> Generated on request to check the alert email; no threshold crossing triggered it.</div>'
                   if test else "")
    actions = "".join(f"<li style='margin:3px 0'>{e(r)}</li>" for r in RECOMMENDED_ACTIONS)
    body_html = f"""<!doctype html><html><body style="margin:0;background:#f4f1ea;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#1d2527">
<div style="max-width:640px;margin:0 auto;padding:20px 16px">
{test_banner}<div style="background:#7c1d12;color:#fff;border-radius:10px 10px 0 0;padding:16px 18px">
<div style="font-size:12px;letter-spacing:.08em;opacity:.85">INCENDIUM · WILDFIRE RISK ALERT</div>
<div style="font-size:20px;font-weight:700;margin-top:4px">{e(cls)} fire risk – {e(rname)}</div>
<div style="font-size:13px;opacity:.9;margin-top:4px">{"Test with today's highest-index area(s) · alert threshold" if test else f"{len(areas)} area(s) crossed the alert threshold of"} {cfg.fire_alert_threshold:g} · {e(stamp)}</div></div>
<div style="background:#fff;border-radius:0 0 10px 10px;padding:18px">
{''.join(cards)}{more}
<div style="font-weight:600;margin:6px 0 4px">Recommended actions</div><ul style="margin:0 0 14px;padding-left:20px;font-size:13.5px">{actions}</ul>
<p style="margin:0 0 16px"><a href="{e(app)}" style="display:inline-block;background:#232d30;color:#fff;text-decoration:none;padding:9px 14px;border-radius:8px;font-size:14px">Open the wildfire monitoring platform</a></p>
<p style="font-size:12px;color:#5b6366;margin:0 0 6px">{e(DISCLAIMER)}</p>
<p style="font-size:12px;color:#5b6366;margin:0">Index: Incendium experimental composite (0–100) of temperature, humidity, wind, days since rain and vegetation moisture. Model weather: Open-Meteo. Vegetation: Copernicus Sentinel-2. Detections: NASA FIRMS. You receive this because the Incendium operator added your address to the alert list.</p>
</div></div></body></html>"""
    return subject, text, body_html


# ---------------------------------------------------------------------------- manual test
def send_test(db: Database, region: str, cfg: AlertSettings | None = None, really_send: bool = False, n: int = 2,
              now: datetime | None = None) -> dict:
    """Build an alert from today's highest-index cells and deliver it marked [TEST].

    Without really_send the message goes to the outbox (test mode) even if SMTP is configured.
    Alert state is not touched.
    """
    from dataclasses import replace

    cfg = cfg or get_alert_settings()
    if not really_send:
        cfg = replace(cfg, email_test_mode=True)
    now = now or utcnow()
    today = _today(region, now)
    rows = []
    for r in db.query("SELECT s.cell_id, s.payload FROM risk_snapshot s JOIN cells c ON c.id=s.cell_id WHERE c.region=? AND s.date=?",
                      (region, today)):
        sc = _valid((json.loads(r["payload"]).get("composite") or {}).get("score"))
        if sc is not None:
            rows.append((sc, r["cell_id"]))
    if not rows:
        return {"status": "failed", "error": f"no Fire Risk Index values for {today}; run `python -m incendium compute` first"}
    rows.sort(reverse=True)
    st = {r["area_id"]: r["last_index"] for r in db.query("SELECT area_id, last_index FROM risk_alert_state WHERE region=?", (region,))}
    areas = [Area(c, sc, st.get(c), composite_class(sc)) for sc, c in rows[:n]]
    res = notify(db, region, areas, cfg, now=now, sync=True, test=True)
    ev = db.one("SELECT status, recipients, message FROM risk_alert_events WHERE id=?", (res.get("event_id"),)) if res.get("event_id") else None
    return {**res, **({"delivery": dict(ev)} if ev else {})}


def status(db: Database) -> dict:
    cfg = get_alert_settings()
    return {
        "threshold": cfg.fire_alert_threshold, "cooldown_hours": cfg.alert_cooldown_hours,
        "email_enabled": cfg.email_enabled, "test_mode": cfg.email_test_mode, "recipients": len(cfg.alert_recipients),
        "smtp_configured": cfg.smtp_ready, "sender_set": bool(cfg.email_sender), "public_url": cfg.public_url,
        "areas_tracked": (db.one("SELECT COUNT(*) n FROM risk_alert_state") or {"n": 0})["n"],
        "areas_in_alert": (db.one("SELECT COUNT(*) n FROM risk_alert_state WHERE alert_active=1") or {"n": 0})["n"],
        "recent_events": [dict(r) for r in db.query(
            "SELECT created_at, region, status, recipients, max_index, subject, message FROM risk_alert_events ORDER BY id DESC LIMIT 5")],
    }
