"""Modules 1, 3, 4, 5 and shared data (synthetic data, test-only; no network)."""
import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from incendium import alerts, firebreak, ml, places, spread, terrain
from incendium.db import iso
from incendium.providers import osm


# ----------------------------------------------------------------------------- terrain
def test_terrarium_decoding_and_slope():
    rgb = np.zeros((2, 2, 3), dtype=np.uint8)
    rgb[..., 0], rgb[..., 1], rgb[..., 2] = 128, 0, 0  # 128*256 - 32768 = 0 m
    assert np.allclose(terrain.decode_terrarium(rgb), 0.0)
    rgb[0, 0] = (131, 232, 128)  # 131*256+232+0.5-32768 = 1000.5
    assert terrain.decode_terrarium(rgb)[0, 0] == pytest.approx(1000.5)
    # Plane rising 100 m per 100 m towards the east: slope 45°, downslope aspect west (270°).
    x = np.arange(10) * 100.0
    elev = np.tile(x, (10, 1))
    slope, aspect = terrain.slope_aspect(elev, 100.0, 100.0)
    assert np.allclose(slope[2:-2, 2:-2], 45.0, atol=0.01)
    assert np.allclose(aspect[2:-2, 2:-2], 270.0, atol=0.01)


def test_grid_geometry():
    g = terrain.grid_around(76.0, 12.0, 5000, 100)
    assert g.nx == g.ny == 100
    assert g.dx_m == pytest.approx(100, rel=0.02) and g.dy_m == pytest.approx(100, rel=0.02)
    assert g.index(76.0, 12.0) == (50, 50)


# ----------------------------------------------------------------------------- fire behaviour
def test_hourly_ffmc_dries_in_sun_and_wets_in_rain():
    f = 85.0
    dry = spread.hourly_ffmc(34, 20, 15, 0, f)
    wet = spread.hourly_ffmc(24, 95, 5, 6.0, f)
    assert dry > f and wet < f - 10
    assert 0 <= spread.hourly_ffmc(10, 100, 0, 50, 99) <= 101


def test_fbp_rates_and_shapes():
    assert spread.curing_factor(100) == pytest.approx(0.176 + 0.02 * 41.2)
    assert spread.ros_o1a(10, 100) > spread.ros_o1a(10, 70) > spread.ros_o1a(10, 50)
    assert spread.ros_o1a(20, 90) > spread.ros_d1(20, 40)
    r = spread.head_ros_by_fuel(10, 40, 85)
    assert r[0] == 0 and r[1] > r[2] > r[3] > 0
    assert spread.length_to_breadth(0) == pytest.approx(1.0)
    lb = spread.length_to_breadth(30)
    assert lb > 2
    assert spread.ellipse_factor(0.0, lb) == pytest.approx(1.0)
    assert spread.ellipse_factor(math.pi, lb) < 0.2
    assert spread.slope_factor(0) == 1.0 and spread.slope_factor(-0.3) == 1.0
    assert spread.slope_factor(0.3) == pytest.approx(math.exp(3.533 * 0.3 ** 1.2))
    assert spread.slope_factor(2.0) == spread.slope_factor(0.7)  # capped


def _hours(ws, wd, n=3, isi=10):
    return [spread.HourState(spread.head_ros_by_fuel(isi, 40, 85), (wd + 180) % 360, spread.length_to_breadth(ws), isi, 90, ws, wd, 30, 30,
                             f"2026-03-10T{12 + i:02d}:00") for i in range(n)]


def test_arrival_times_wind_slope_and_barriers():
    fuel = np.ones((61, 61), dtype=np.int8)
    flat = np.zeros((61, 61))
    centre = (30, 30)
    calm = spread.arrival_times(fuel, flat, 50, 50, centre, _hours(0, 0), 180)
    # Calm and flat: roughly circular.
    assert calm[30, 50] == pytest.approx(calm[30, 10], rel=0.05) == pytest.approx(calm[10, 30], rel=0.05)
    # Wind FROM the west pushes fire east.
    windy = spread.arrival_times(fuel, flat, 50, 50, centre, _hours(30, 270), 180)
    assert windy[30, 50] < windy[30, 10] / 3
    # Uphill (terrain rising to the north = lower row index) is faster than downhill.
    hill = np.tile(np.arange(61)[::-1, None] * 20.0, (1, 61))
    up = spread.arrival_times(fuel, hill, 50, 50, centre, _hours(0, 0), 180)
    assert up[10, 30] < up[50, 30]
    # A non-fuel wall blocks spread.
    walled = fuel.copy()
    walled[:, 40] = 0
    t = spread.arrival_times(walled, flat, 50, 50, centre, _hours(30, 270), 600)
    assert np.isinf(t[30, 45:]).all()
    # Ignition on non-fuel: nothing burns.
    assert np.isinf(spread.arrival_times(np.zeros((5, 5), np.int8), np.zeros((5, 5)), 50, 50, (2, 2), _hours(0, 0), 60)).all()


def test_mask_to_polygon_area():
    g = terrain.Grid(76.0, 12.0, 76.1, 12.1, 10, 10)
    m = np.zeros((10, 10), bool)
    m[2:5, 3:7] = True
    poly = spread.mask_to_polygon(m, g)
    assert poly.area == pytest.approx(12 * 0.01 * 0.01, rel=0.05)
    assert spread.mask_to_polygon(np.zeros((3, 3), bool), g) is None


def test_scenario_end_to_end_with_synthetic_inputs(monkeypatch):
    g_holder = {}

    def fake_fuel(grid):
        g_holder["g"] = grid
        f = np.full((grid.ny, grid.nx), 1, np.int8)
        return {"fuel": f, "obs": np.full(f.shape, 5.0), "ndvi": np.full(f.shape, 0.3), "ndmi": np.zeros(f.shape),
                "water_share": np.zeros(f.shape), "retrieved_at": "2026-11-14T00:00:00Z", "period": ["2026-10-01", "2026-11-14"], "cached": True}

    monkeypatch.setattr(spread, "fetch_fuel", fake_fuel)
    monkeypatch.setattr(spread, "sample_dem", lambda grid, z: np.zeros((grid.ny, grid.nx), np.float32))
    weather = [{"time_local": (datetime(2026, 3, 9, 13) + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M"), "t2m": 33.0, "rh2m": 25.0,
                "ws10": 20.0, "wd10": 270.0, "precip": 0.0} for i in range(40)]
    inp = spread.ScenarioInputs(76.5, 13.5, datetime(2026, 3, 10, 11), 4, 85, weather, 88.0, 40.0, "karnataka:13.375,76.375")
    settl = lambda bb: [{"name": "East village", "subtype": "village", "lat": 13.5, "lon": 76.52, "id": "node/1"},
                        {"name": "West village", "subtype": "village", "lat": 13.5, "lon": 76.40, "id": "node/2"}]
    res = spread.run_scenario(inp, settl, lambda la, lo: [{"name": "Station", "distance_km": 12.0}])
    m = res["meta"]
    assert m["hours"] == 4 and len(res["features"]) == 4
    areas = [h["area_ha"] for h in m["hourly"]]
    assert areas == sorted(areas) and areas[-1] > 0
    names = [s["name"] for s in m["settlements_reached"]]
    assert "East village" in names and "West village" not in names  # downwind only
    assert m["weather_by_hour"][0]["ffmc"] > 88  # dried by a hot, dry morning
    assert any("not a forecast" in a for a in m["assumptions"])


# ----------------------------------------------------------------------------- firebreaks
def test_firebreak_ranks_crosswind_segment_between_fuel_and_village(monkeypatch, db):
    lon0, lat0 = 76.5, 13.5
    # wind FROM the west; forest west of x=0, village 2 km east.
    monkeypatch.setattr(firebreak, "prevailing_wind", lambda db_, cid, now: {"from_deg": 270.0, "p90_kmh": 20.0, "mean_kmh": 12.0, "steadiness": 0.9, "hours": 60, "period_start": "2026-03-01"})

    def fake_fuel(grid):
        lons, _ = grid.centres()
        f = np.where(lons < lon0, 3, 0).astype(np.int8)
        return {"fuel": f, "obs": np.full(f.shape, 5.0), "retrieved_at": "x", "period": ["a", "b"]}

    monkeypatch.setattr(firebreak, "fetch_fuel", fake_fuel)
    monkeypatch.setattr(firebreak, "sample_dem", lambda grid, z: np.zeros((grid.ny, grid.nx), np.float32))
    monkeypatch.setattr("incendium.geo.cell_for_point", lambda region, lon, lat: "karnataka:13.375,76.375")
    with db.connect() as con:
        con.execute("INSERT INTO fwi_daily(cell_id,date,ffmc,dmc,dc,isi,bui,fwi,dsr,fwi_class,is_forecast,spinup,status,computed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("karnataka:13.375,76.375", datetime.now().date().isoformat(), 90, 30, 300, 8, 40, 20, 5, "High", 0, 0, "ok", "x"))
    d = 0.01
    north_south = {"id": "way/1", "kind": "road", "class": "track", "name": "Crosswind track", "coords": [(lon0 + 0.003, lat0 - d * 2), (lon0 + 0.003, lat0 + d * 2)]}
    east_west = {"id": "way/2", "kind": "road", "class": "track", "name": "Along-wind track", "coords": [(lon0 + 0.003, lat0 - 0.03), (lon0 + 0.04, lat0 - 0.03)]}
    village = [{"id": "node/9", "name": "Village", "subtype": "village", "lat": lat0, "lon": lon0 + 0.02}]
    res = firebreak.recommend(db, ["karnataka"], lon0, lat0, 4, lambda bb: ([north_south, east_west], "t"), lambda bb: village)
    top = res["features"][0]["properties"]
    assert top["name"] == "Crosswind track" and top["orientation"] > 0.9
    assert top["settlements_protected"][0]["name"] == "Village"
    assert res["meta"]["hazard_image"]["url"].startswith("data:image/png;base64,")


# ----------------------------------------------------------------------------- alerts
def _det(i, lat, lon, minutes=0, frp=5.0, cell=None, state="Karnataka"):
    t = datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)
    return {"id": f"d{i}", "latitude": lat, "longitude": lon, "acq_datetime": iso(t), "frp": frp, "confidence": "n", "state": state, "cell_id": cell}


def test_cluster_groups_by_distance_and_time():
    dets = [_det(1, 13.0, 76.0), _det(2, 13.005, 76.005, 5), _det(3, 13.1, 76.0), _det(4, 13.0, 76.0, 120)]
    groups = sorted(alerts.cluster(dets), key=len, reverse=True)
    assert sorted(d["id"] for d in groups[0]) == ["d1", "d2"]
    assert len(groups) == 3


def _seed_places(db):
    with db.connect() as con:
        con.executemany("INSERT INTO osm_features(id,kind,subtype,name,lat,lon,tags,scope,retrieved_at) VALUES (?,?,?,?,?,?,?,?,?)", [
            ("node/1", "settlement", "village", "Hosahalli", 13.01, 76.0, "{}", "IN-KA", "t"),
            ("node/2", "fire_station", "fire_station", "Town FS", 13.2, 76.1, json.dumps({"phone": "101"}), "IN", "t"),
        ])


def test_incident_priorities_and_persistent_sources(db):
    _seed_places(db)
    cell = "karnataka:13.125,76.125"
    near = [_det(i, 13.0, 76.0, i, frp=30) for i in range(3)]  # near village (1.1 km) + ≥ 3 detections
    a = alerts.evaluate_incident(db, near, {cell: None})
    assert a["priority"] == "medium" and {r["id"] for r in a["payload"]["rules"]} == {"R1", "R3"}
    assert a["payload"]["fire_stations"][0]["name"] == "Town FS" and "101" in a["message"]
    assert "Not an official warning" in a["message"]
    # all three rules -> high
    near_hot = [dict(d, cell_id=cell) for d in near]
    assert alerts.evaluate_incident(db, near_hot, {cell: "Extreme"})["priority"] == "high"
    # far from everything, small: no alert
    assert alerts.evaluate_incident(db, [_det(9, 14.5, 77.5)], {}) is None
    # persistent heat source: detections on 6 of the previous 10 days -> capped at low
    with db.connect() as con:
        for k in range(1, 7):
            t = iso(datetime(2026, 3, 10, 8, tzinfo=timezone.utc) - timedelta(days=k))
            con.execute("INSERT INTO detections(id,source,satellite,instrument,latitude,longitude,acq_datetime,confidence,frp,daynight,version,retrieved_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (f"p{k}", "s", "NOAA-20", "VIIRS", 13.0, 76.0, t, "n", 5, "D", "2", t))
    p = alerts.evaluate_incident(db, near_hot, {cell: "Extreme"})
    assert p["priority"] == "low" and p["payload"]["persistent_source"] and p["payload"]["persistent_days"] == 6


def test_notifications_disabled_without_channels(db, monkeypatch):
    monkeypatch.setattr(alerts, "get_secret", lambda name: None)
    cfg = alerts.channel_config()
    assert cfg == {"email": False, "webhook": False, "min_priority": "medium", "automatic_authority_contact": False}
    assert alerts.notify_pending(db)["reason"] == "no channels configured"


def test_webhook_notification_marks_alerts(db, monkeypatch):
    secrets = {"INCENDIUM_ALERT_WEBHOOK_URL": "https://example.invalid/hook"}
    monkeypatch.setattr(alerts, "get_secret", lambda name: secrets.get(name))
    sent = {}
    monkeypatch.setattr(alerts, "_send_webhook", lambda a: sent.setdefault("n", len(a)))
    alerts._store(db, [{"id": "x1", "kind": "detection", "priority": "medium", "title": "t", "message": "m", "lat": 1, "lon": 2,
                        "state": None, "cell_id": None, "payload": {}},
                       {"id": "x2", "kind": "detection", "priority": "low", "title": "t", "message": "m", "lat": 1, "lon": 2,
                        "state": None, "cell_id": None, "payload": {}}])
    res = alerts.notify_pending(db)
    assert res["sent"] == 1 and sent["n"] == 1  # low is below the default minimum priority
    assert db.one("SELECT notify_status FROM alerts WHERE id='x1'")["notify_status"] == "sent"
    assert alerts.notify_pending(db)["reason"] in ("rate limited", "nothing pending")


# ----------------------------------------------------------------------------- OSM + places
def test_osm_parsing():
    body = {"elements": [
        {"type": "node", "id": 1, "lat": 13.0, "lon": 76.0, "tags": {"place": "village", "name": "A", "secret": "x"}},
        {"type": "way", "id": 2, "center": {"lat": 13.1, "lon": 76.1}, "tags": {"amenity": "fire_station", "name:en": "FS"}},
        {"type": "node", "id": 3, "tags": {"place": "town"}},  # no coordinates -> skipped
    ]}
    pts = osm.parse_points(body, "settlement")
    assert [p["id"] for p in pts] == ["node/1", "way/2"] and "secret" not in pts[0]["tags"]
    assert osm.parse_points(body, "fire_station")[1]["name"] == "FS"
    lines = osm.parse_lines({"elements": [{"type": "way", "id": 5, "tags": {"highway": "track"}, "geometry": [{"lat": 1, "lon": 2}, {"lat": 1.1, "lon": 2.1}]}]})
    assert lines[0]["kind"] == "road" and lines[0]["class"] == "track"


def test_places_nearest(db):
    _seed_places(db)
    r = places.nearest(db, "fire_station", 13.0, 76.0, 3, 80)
    assert r[0]["name"] == "Town FS" and r[0]["distance_km"] == pytest.approx(24.5, abs=1.0) and r[0]["phone"] == "101"
    assert places.nearest(db, "fire_station", 13.0, 76.0, 3, 5) == []
    assert places.nearest(db, "settlement", 13.0, 76.0, 1, 5, ("town",)) == []


# ----------------------------------------------------------------------------- ML helpers
def test_history_rate_and_reliability(monkeypatch):
    cid = "karnataka:12.875,77.625"
    h = {"year": 2025, "detections": [
        {"lat": 12.97, "lon": 77.59, "acq": "2025-03-01T08:00:00Z"}, {"lat": 12.97, "lon": 77.59, "acq": "2025-03-01T09:00:00Z"},
        {"lat": 12.97, "lon": 77.59, "acq": "2025-03-02T08:00:00Z"}, {"lat": 12.97, "lon": 77.59, "acq": "2025-07-02T08:00:00Z"}]}
    rates = ml.history_rate("karnataka", [h], "Asia/Kolkata")
    assert rates[cid] == pytest.approx(2 / 120)
    p = np.linspace(0, 1, 100)
    y = p > 0.5
    rel = ml.reliability(p, y, bins=4)
    assert len(rel) == 4 and rel[0]["observed_rate"] == 0 and rel[-1]["observed_rate"] == 1


def test_ml_scoring_without_model_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(ml, "MODEL_DIR", tmp_path)
    ml._cache.clear()
    assert ml.score_rows([{"cell_id": "a", "date": "2026-03-01"}], {}, {}) == [None]
    assert ml.in_training_season("2026-03-01") and not ml.in_training_season("2026-11-14")


# ----------------------------------------------------------------------------- API
def test_ops_endpoints(client, db, monkeypatch):
    r = client.post("/api/simulate/spread", json={"lon": 77.2, "lat": 28.6})
    assert r.status_code == 422 and "risk-grid" in r.get_json()["message"]
    assert client.post("/api/simulate/spread", json={"lon": 500, "lat": 0}).status_code == 400
    assert client.post("/api/simulate/spread", json={"lon": 76, "lat": 13, "hours": 99}).status_code == 400
    assert client.post("/api/firebreaks", json={"lon": 76, "lat": 13, "radius_km": 1}).status_code == 400
    alerts._store(db, [{"id": "a1", "kind": "detection", "priority": "high", "title": "t", "message": "m", "lat": 13, "lon": 76,
                        "state": "Karnataka", "cell_id": None, "payload": {"rules": []}}])
    body = client.get("/api/alerts?min_priority=medium").get_json()
    assert [a["id"] for a in body["alerts"]] == ["a1"] and body["channels"]["automatic_authority_contact"] is False
    assert client.get("/api/alerts?min_priority=urgent").status_code == 400
    assert client.post("/api/alerts/a1/ack", environ_base={"REMOTE_ADDR": "10.0.0.2"}).status_code == 403
    assert client.post("/api/alerts/a1/ack").status_code == 200
    assert client.get("/api/alerts?include_acknowledged=false").get_json()["alerts"] == []
    assert client.post("/api/alerts/nope/ack").status_code == 404
    assert client.get("/api/places/fire-stations?bbox=1,2,3").status_code == 400
    assert client.get("/api/places/fire-stations?bbox=60,0,90,40").status_code == 400  # too large
    assert client.get("/api/places/fire-stations?bbox=76,12,77,13").get_json()["meta"]["count"] == 0
    monkeypatch.setattr(ml, "MODEL_DIR", __import__("pathlib").Path("does-not-exist"))
    assert client.get("/api/ml/report").status_code == 404
