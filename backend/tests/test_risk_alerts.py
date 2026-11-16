"""Fire Risk Index email alerts (synthetic data, no network: SMTP is replaced by a fake)."""
import json
import smtplib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from incendium import mailer, risk_alerts
from incendium.config import AlertSettings, get_alert_settings

NOW = datetime(2026, 3, 10, 6, 0, tzinfo=timezone.utc)  # 11:30 IST
TODAY = "2026-03-10"
C1, C2 = "karnataka:12.125,76.125", "karnataka:12.375,76.375"


class FakeSMTP:
    sent: list = []
    password_ok = "good-app-password"

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, pwd):
        if pwd != self.password_ok:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    def send_message(self, msg, to_addrs=None):
        FakeSMTP.sent.append((msg, list(to_addrs)))
        return {}


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    FakeSMTP.sent = []
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer, "OUTBOX_DIR", tmp_path / "outbox")
    monkeypatch.setenv("INCENDIUM_SMTP_PASSWORD", FakeSMTP.password_ok)
    return AlertSettings(fire_alert_threshold=75.0, alert_recipients=("ops@example.org", "forest@example.org"),
                         email_sender="alerts@example.org", alert_cooldown_hours=12.0, email_enabled=True,
                         email_test_mode=False, smtp_host="smtp.example.org", smtp_port=587, smtp_user="alerts@example.org",
                         public_url="https://example.org")


@pytest.fixture
def grid(db):
    with db.connect() as con:
        for cid, lat, lon in ((C1, 12.125, 76.125), (C2, 12.375, 76.375)):
            con.execute("INSERT INTO cells VALUES (?,?,?,?,?,?,?,?,?)", (cid, "karnataka", lat, lon, lon - .125, lat - .125, lon + .125, lat + .125, 1.0))
    return db


def score(db, cid, value, factors=True, date=TODAY):
    fs = [{"key": "T", "label": "Temperature", "unit": "°C", "value": 38.0, "score": 90, "weight": .25, "contribution": 22.5},
          {"key": "H", "label": "Low humidity", "unit": "%", "value": 18.0, "score": 95, "weight": .25, "contribution": 23.8},
          {"key": "W", "label": "Wind", "unit": "km/h", "value": 30.0, "score": 70, "weight": .2, "contribution": 14.0}] if factors else []
    payload = {"composite": {"score": value, "class": None, "factors": fs}, "explanation": [], "vegetation": None}
    with db.connect() as con:
        con.execute("INSERT OR REPLACE INTO risk_snapshot(cell_id, date, payload, computed_at) VALUES (?,?,?,?)",
                    (cid, date, json.dumps(payload), "x"))


def run(db, cfg, hours=0):
    return risk_alerts.evaluate(db, "karnataka", cfg=cfg, now=NOW + timedelta(hours=hours), sync=True)


def events(db):
    return [dict(r) for r in db.query("SELECT * FROM risk_alert_events ORDER BY id")]


def test_full_lifecycle_cross_stay_rearm_recross(grid, cfg):
    db, cfg = grid, replace(cfg, alert_cooldown_hours=3.0)
    score(db, C1, 60)
    assert run(db, cfg)["initialised"] == 1 and not FakeSMTP.sent          # first sighting: state only
    score(db, C1, 70)
    assert run(db, cfg, 1)["crossed"] == [] and not FakeSMTP.sent          # below threshold: no email
    score(db, C1, 80)
    r = run(db, cfg, 2)
    assert r["crossed"] == [C1] and len(FakeSMTP.sent) == 1                # crossing: one email
    msg, to = FakeSMTP.sent[0]
    assert sorted(to) == ["forest@example.org", "ops@example.org"]         # every recipient
    assert msg["Subject"].startswith("[WILDFIRE ALERT] Very High Fire Risk Detected")
    assert "ops@example.org" not in msg["To"]                              # recipients not disclosed to each other
    score(db, C1, 90)
    assert run(db, cfg, 3)["crossed"] == [] and len(FakeSMTP.sent) == 1    # stays above: no repeat
    score(db, C1, 50)
    run(db, cfg, 4)
    assert db.one("SELECT alert_active FROM risk_alert_state WHERE area_id=?", (C1,))["alert_active"] == 0  # re-armed
    score(db, C1, 78)
    r = run(db, cfg, 4.5)                                                  # re-cross inside the 3 h cooldown
    assert r["suppressed_cooldown"] == [C1] and len(FakeSMTP.sent) == 1
    score(db, C1, 50)
    run(db, cfg, 5)
    score(db, C1, 81)
    assert run(db, cfg, 6)["crossed"] == [C1] and len(FakeSMTP.sent) == 2  # after cooldown: new alert
    assert [e["status"] for e in events(db)] == ["sent", "suppressed_cooldown", "sent"]


def test_no_email_at_startup_even_if_already_above(grid, cfg):
    score(grid, C1, 95)
    assert run(grid, cfg)["initialised"] == 1 and not FakeSMTP.sent
    assert run(grid, cfg, 3)["crossed"] == [] and not FakeSMTP.sent


def test_several_areas_crossing_together_share_one_email(grid, cfg):
    for c in (C1, C2):
        score(grid, c, 60)
    run(grid, cfg)
    score(grid, C1, 80)
    score(grid, C2, 88)
    r = run(grid, cfg, 3)
    assert sorted(r["crossed"]) == sorted([C1, C2]) and len(FakeSMTP.sent) == 1
    body = FakeSMTP.sent[0][0].get_body(("plain",)).get_content()
    assert "2 area(s) crossed" in body and body.index(C2) < body.index(C1)  # highest index first


def test_invalid_credentials_are_logged_and_do_not_raise(grid, cfg, monkeypatch):
    monkeypatch.setenv("INCENDIUM_SMTP_PASSWORD", "wrong")
    score(grid, C1, 60)
    run(grid, cfg)
    score(grid, C1, 80)
    r = run(grid, cfg, 3)                                                  # must not raise
    ev = events(grid)[-1]
    assert r["crossed"] == [C1] and ev["status"] == "failed" and "authentication failed" in ev["message"]
    assert "wrong" not in ev["message"]
    # the event was handled: staying above does not retry-spam
    score(grid, C1, 85)
    assert run(grid, cfg, 4)["crossed"] == []


def test_missing_data_sends_with_available_information(grid, cfg):
    score(grid, C1, 60)
    run(grid, cfg)
    score(grid, C1, 80, factors=False)            # no weather factor details, no FWI, no hourly wind, no vegetation
    run(grid, cfg, 3)
    body = FakeSMTP.sent[0][0].get_body(("plain",)).get_content()
    assert "Max temperature: not available" in body and "Fire Weather Index: not available" in body
    assert "Active fire hotspots: none in the last 24 h" in body


def test_missing_or_invalid_index_is_skipped_without_changing_state(grid, cfg):
    score(grid, C1, 70)
    run(grid, cfg)
    for bad in (None, "n/a", 140, float("nan")):
        score(grid, C1, bad)
        r = run(grid, cfg, 1)
        assert r["missing"] == 1 and not FakeSMTP.sent
    assert grid.one("SELECT last_index FROM risk_alert_state WHERE area_id=?", (C1,))["last_index"] == 70
    score(grid, C1, 80)
    assert run(grid, cfg, 2)["crossed"] == [C1]    # crossing still detected against the last valid value


def test_email_disabled_sends_nothing(grid, cfg):
    off = replace(cfg, email_enabled=False)
    score(grid, C1, 60)
    run(grid, off)
    score(grid, C1, 80)
    r = run(grid, off, 3)
    assert r["notification"]["status"] == "disabled" and not FakeSMTP.sent
    assert events(grid)[-1]["status"] == "disabled"


def test_test_mode_writes_outbox_without_smtp(grid, cfg, tmp_path, monkeypatch):
    monkeypatch.delenv("INCENDIUM_SMTP_PASSWORD")
    tm = replace(cfg, email_test_mode=True, smtp_host=None)
    score(grid, C1, 60)
    run(grid, tm)
    score(grid, C1, 80)
    run(grid, tm, 3)
    assert not FakeSMTP.sent and events(grid)[-1]["status"] == "test"
    files = sorted((tmp_path / "outbox").iterdir())
    assert any(f.suffix == ".eml" for f in files) and any(f.suffix == ".html" for f in files)
    assert "Open the wildfire monitoring platform" in next(f for f in files if f.suffix == ".html").read_text(encoding="utf-8")


def test_background_delivery_does_not_block(grid, cfg):
    score(grid, C1, 60)
    risk_alerts.evaluate(grid, "karnataka", cfg=cfg, now=NOW)
    score(grid, C1, 80)
    r = risk_alerts.evaluate(grid, "karnataka", cfg=cfg, now=NOW + timedelta(hours=3))
    assert r["notification"]["status"] == "queued"
    risk_alerts.wait_pending(10)
    assert len(FakeSMTP.sent) == 1 and events(grid)[-1]["status"] == "sent"


def test_manual_test_message_is_marked_and_leaves_state_alone(grid, cfg):
    score(grid, C1, 66)
    r = risk_alerts.send_test(grid, "karnataka", cfg=cfg, really_send=False, now=NOW)  # defaults to outbox
    assert r["subject"].startswith("[TEST] [WILDFIRE ALERT]") and r["delivery"]["status"] == "test" and not FakeSMTP.sent
    assert grid.one("SELECT COUNT(*) n FROM risk_alert_state")["n"] == 0


def test_settings_from_environment(monkeypatch, tmp_path):
    f = tmp_path / "rcpt.txt"
    f.write_text("# team\nb@example.org\nA@example.org\n", encoding="utf-8")
    monkeypatch.setenv("INCENDIUM_ALERT_EMAIL_TO", "a@example.org; c@example.org")
    monkeypatch.setenv("INCENDIUM_ALERT_RECIPIENTS_FILE", str(f))
    monkeypatch.setenv("INCENDIUM_FIRE_ALERT_THRESHOLD", "60")
    monkeypatch.setenv("INCENDIUM_EMAIL_ENABLED", "1")
    s = get_alert_settings()
    assert s.alert_recipients == ("a@example.org", "c@example.org", "b@example.org") and s.fire_alert_threshold == 60
    monkeypatch.setenv("INCENDIUM_FIRE_ALERT_THRESHOLD", "abc")
    assert get_alert_settings().fire_alert_threshold == 75.0        # invalid -> safe default


# ----------------------------------------------------------------------------- operator actions (ack + manual email)
def test_operator_key_guards_ack_and_manual_email(client, db, cfg, monkeypatch):
    from incendium import alerts, api_ops

    monkeypatch.setattr("incendium.config.get_alert_settings", lambda: cfg)
    monkeypatch.setattr(alerts, "get_alert_settings", lambda: cfg)
    api_ops._op_fails.clear()
    api_ops._manual_sends.clear()
    alerts._store(db, [{"id": "a1", "kind": "detection", "priority": "high", "title": "Thermal anomaly near X", "message": "3 detections near X",
                        "lat": 13, "lon": 76, "state": "Karnataka", "cell_id": None, "payload": {"rules": []}}])
    web = {"X-Real-IP": "203.0.113.5"}  # as forwarded by nginx

    monkeypatch.delenv("INCENDIUM_OPERATOR_KEY", raising=False)
    r = client.post("/api/alerts/a1/ack", headers=web)
    assert r.status_code == 403 and r.get_json()["error"] == "operator_disabled"
    assert client.get("/api/alerts").get_json()["operator_actions"] is False

    monkeypatch.setenv("INCENDIUM_OPERATOR_KEY", "correct-horse-battery")
    assert client.get("/api/alerts").get_json()["operator_actions"] is True
    assert client.post("/api/alerts/a1/ack", headers=web).get_json()["error"] == "operator_key_required"
    assert client.post("/api/alerts/a1/ack", headers={**web, "X-Operator-Key": "wrong-key-123"}).status_code == 401
    ok = {**web, "X-Operator-Key": "correct-horse-battery"}
    assert client.post("/api/operator/check", headers=ok).status_code == 200

    r = client.post("/api/alerts/a1/notify", headers=ok)
    assert r.status_code == 200 and r.get_json()["recipients"] == 2
    assert len(FakeSMTP.sent) == 1 and sorted(FakeSMTP.sent[0][1]) == ["forest@example.org", "ops@example.org"]
    assert "Thermal anomaly near X" in FakeSMTP.sent[0][0]["Subject"]
    assert db.one("SELECT notify_status FROM alerts WHERE id='a1'")["notify_status"].startswith("sent manually")
    assert client.post("/api/alerts/a1/notify", headers=ok).status_code == 429          # per-alert rate limit
    assert client.post("/api/alerts/a1/ack", headers=ok).status_code == 200
    assert client.post("/api/alerts/nope/notify", headers=ok).status_code == 404

    monkeypatch.setattr("incendium.config.get_alert_settings", lambda: replace(cfg, email_enabled=False))
    alerts._store(db, [{"id": "a2", "kind": "detection", "priority": "low", "title": "t", "message": "m", "lat": 13, "lon": 76,
                        "state": None, "cell_id": None, "payload": {}}])
    assert client.post("/api/alerts/a2/notify", headers=ok).get_json()["error"] == "email_disabled"

    other = {"X-Real-IP": "198.51.100.9", "X-Operator-Key": "guess-000000"}
    codes = [client.post("/api/operator/check", headers=other).status_code for _ in range(9)]
    assert codes[:8] == [401] * 8 and codes[8] == 429                               # lockout after repeated wrong keys
    assert client.post("/api/operator/check", headers=ok).status_code == 200         # other clients unaffected
