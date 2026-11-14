"""Integration-style tests with mocked upstreams (synthetic data, test-only)."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from incendium import ingest as ingest_mod
from incendium.ingest import Ingestor
from incendium.providers import firms, openmeteo

from .conftest import firms_csv


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(ingest_mod, "OPEN_METEO_PER_MINUTE_BUDGET", 1e9)


def _recent_rows():
    now = datetime.now(timezone.utc) - timedelta(hours=2)
    d, t = now.strftime("%Y-%m-%d"), now.strftime("%H%M")
    return [
        f"12.97,77.59,330,0.4,0.4,{d},{t},N20,VIIRS,n,2.0NRT,295,3.2,D",  # Bengaluru, in grid
        f"28.61,77.21,340,0.4,0.4,{d},{t},N20,VIIRS,h,2.0NRT,300,9.1,D",  # Delhi, outside grid
    ]


@respx.mock
def test_firms_ingest_dedupes_and_tags(settings, db):
    respx.get(url__startswith=firms.BASE).mock(return_value=httpx.Response(200, text=firms_csv(_recent_rows())))
    ing = Ingestor(db, settings)
    ing.sync_cells()
    r1 = ing.ingest_firms()
    r2 = ing.ingest_firms()
    assert r1["status"] == "ok" and r1["new"] == 2  # two sources return same rows -> deduped
    assert r2["new"] == 0
    rows = {r["state"]: dict(r) for r in db.query("SELECT * FROM detections")}
    assert rows["Karnataka"]["cell_id"] == "karnataka:12.875,77.625"
    assert rows["Delhi"]["cell_id"] is None


@respx.mock
def test_firms_failure_records_error_without_data(settings, db):
    respx.get(url__startswith=firms.BASE).mock(return_value=httpx.Response(500))
    ing = Ingestor(db, settings)
    res = ing.ingest_firms()
    assert res["status"] == "error"
    assert db.one("SELECT COUNT(*) n FROM detections")["n"] == 0
    run = db.one("SELECT * FROM provider_runs WHERE provider='firms' ORDER BY id DESC")
    assert run["status"] == "error"
    assert settings.firms_map_key not in (run["message"] or "")


def _om_response(request: httpx.Request):
    """Synthetic Open-Meteo payload for every requested location (test fixture)."""
    q = dict(request.url.params)
    lats = q["latitude"].split(",")
    past, fc = int(q["past_days"]), int(q["forecast_days"])
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=past)
    hours = (past + fc) * 24
    times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    hourly = {
        "time": times,
        "temperature_2m": [24 + 8 * ((h % 24) / 24) for h in range(hours)],
        "relative_humidity_2m": [70 - 30 * ((h % 24) / 24) for h in range(hours)],
        "wind_speed_10m": [8.0] * hours,
        "wind_direction_10m": [250.0] * hours,
        "wind_gusts_10m": [15.0] * hours,
        "precipitation": [0.5 if h % 120 == 3 else 0.0 for h in range(hours)],
        "vapour_pressure_deficit": [1.2] * hours,
    }
    units = {"temperature_2m": "°C", "relative_humidity_2m": "%", "wind_speed_10m": "km/h", "wind_gusts_10m": "km/h", "wind_direction_10m": "°", "precipitation": "mm", "vapour_pressure_deficit": "kPa"}
    items = [{"latitude": float(la), "longitude": 76.0, "elevation": 800.0, "timezone": "Asia/Kolkata", "utc_offset_seconds": 19800, "hourly_units": units, "hourly": hourly} for la in lats]
    return httpx.Response(200, json=items if len(items) > 1 else items[0])


@respx.mock
def test_weather_ingest_computes_fwi_and_composite_requires_vegetation(settings, db, client):
    respx.get(openmeteo.BASE).mock(side_effect=_om_response)
    ing = Ingestor(db, settings)
    ing.sync_cells()
    res = ing.ingest_weather("karnataka")
    assert res["status"] == "ok" and res["past_days"] == 92
    n_ok = db.one("SELECT COUNT(*) n FROM fwi_daily WHERE status='ok' AND spinup=0")["n"]
    assert n_ok > 0
    # Without vegetation data the composite must NOT be produced.
    r = client.get("/api/risk-map?region=karnataka")
    assert r.status_code == 200
    props = [f["properties"] for f in r.get_json()["features"]]
    assert all(p["fwi"] is not None for p in props)
    assert all(p["composite"] is None and "V" in p["missing"] for p in props)
    # Second run is incremental.
    assert ing.ingest_weather("karnataka")["past_days"] == 4


@respx.mock
def test_weather_unit_mismatch_rejected(settings, db):
    def bad(request):
        resp = _om_response(request)
        body = resp.json()
        items = body if isinstance(body, list) else [body]
        for it in items:
            it["hourly_units"]["wind_speed_10m"] = "m/s"
        return httpx.Response(200, json=body)

    respx.get(openmeteo.BASE).mock(side_effect=bad)
    ing = Ingestor(db, settings)
    ing.sync_cells()
    res = ing.ingest_weather("karnataka")
    assert res["status"] == "error"
    assert db.one("SELECT COUNT(*) n FROM weather_daily")["n"] == 0


# ----------------------------------------------------------------------------- API
def test_health_and_status(client):
    assert client.get("/api/health").status_code == 200
    st = client.get("/api/providers/status").get_json()
    assert {p["id"] for p in st["providers"]} == {"firms", "open-meteo", "cdse", "risk-engine"}
    assert all(p["state"] == "never_run" for p in st["providers"])


@pytest.mark.parametrize(
    "qs",
    ["hours=0", "hours=10000", "bbox=1,2,3", "bbox=10,0,5,1", "min_confidence=extreme", "limit=0", "satellite=Landsat", "unknown=1", "region=atlantis"],
)
def test_hotspot_validation(client, qs):
    r = client.get(f"/api/hotspots?{qs}")
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_request"


@respx.mock
def test_hotspots_endpoint_filters(settings, db, client):
    respx.get(url__startswith=firms.BASE).mock(return_value=httpx.Response(200, text=firms_csv(_recent_rows())))
    Ingestor(db, settings).ingest_firms()
    fc = client.get("/api/hotspots?hours=24").get_json()
    assert fc["meta"]["count"] == 2
    assert "not confirmed wildfires" in fc["meta"]["semantics"]
    assert client.get("/api/hotspots?hours=24&min_confidence=high").get_json()["meta"]["count"] == 1
    assert client.get("/api/hotspots?hours=24&region=karnataka").get_json()["meta"]["count"] == 1
    assert client.get("/api/hotspots?hours=24&bbox=70,20,80,30").get_json()["meta"]["count"] == 1


def test_risk_endpoints_without_data(client):
    assert client.get("/api/risk-map?region=karnataka").status_code == 404
    assert client.get("/api/risk-map?region=odisha").status_code == 404
    assert client.get("/api/risk-details?cell_id=bad").status_code == 400
    assert client.get("/api/risk-details?cell_id=karnataka:0.125,0.125").status_code == 404


def test_regions_and_methodology(client):
    regs = client.get("/api/regions").get_json()
    ka = next(r for r in regs["regions"] if r["id"] == "karnataka")
    assert ka["enabled"] and ka["cells"] > 200 and ka["outline"]["type"] in ("Polygon", "MultiPolygon")
    m = client.get("/api/methodology").get_json()
    assert m["composite"]["weights"]["T"] == 0.25
    assert any("not confirmed wildfires" in x for x in m["limitations"])


def test_validation_endpoint(client, monkeypatch):
    from incendium import validation

    monkeypatch.setattr(validation, "load_report", lambda region, season=None: None)
    r = client.get("/api/validation?region=karnataka")
    assert r.status_code == 404 and "incendium validate" in r.get_json()["message"]
    assert client.get("/api/validation?region=atlantis").status_code == 400
    monkeypatch.setattr(validation, "load_report", lambda region, season=None: {"meta": {"region": region, "season": season}})
    assert client.get("/api/validation?region=karnataka").get_json()["meta"]["region"] == "karnataka"
    assert client.get("/api/validation?region=karnataka&season=2025").get_json()["meta"]["season"] == "2025"
    assert client.get("/api/validation?region=karnataka&season=25").status_code == 400


def test_admin_refresh_is_local_only(client):
    r = client.post("/api/admin/refresh/firms", environ_base={"REMOTE_ADDR": "10.1.2.3"})
    assert r.status_code == 403
    assert client.post("/api/admin/refresh/nope").status_code == 404


def test_errors_do_not_leak_internals(client, monkeypatch):
    from incendium import api

    def boom():
        raise RuntimeError("secret internals /path/to/db")

    monkeypatch.setattr(api, "_provider_status", boom)
    r = client.get("/api/providers/status")
    assert r.status_code == 500
    assert "internals" not in r.get_data(as_text=True)
