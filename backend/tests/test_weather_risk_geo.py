from datetime import datetime, timedelta

import pytest

from incendium import geo
from incendium.risk import compute_composite, composite_class, linear_score
from incendium.weather import derive_daily

W = {"T": 0.25, "H": 0.25, "W": 0.20, "D": 0.15, "V": 0.15}


def hourly(start: datetime, hours: int, precip_at: dict[str, float] | None = None):
    times, vals = [], {k: [] for k in ("temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "precipitation", "vapour_pressure_deficit")}
    for h in range(hours):
        t = start + timedelta(hours=h)
        ts = t.strftime("%Y-%m-%dT%H:%M")
        times.append(ts)
        vals["temperature_2m"].append(20.0 + t.hour)
        vals["relative_humidity_2m"].append(90.0 - 2 * t.hour)
        vals["wind_speed_10m"].append(float(t.hour))
        vals["wind_direction_10m"].append(180.0)
        vals["wind_gusts_10m"].append(float(t.hour) + 5)
        vals["precipitation"].append((precip_at or {}).get(ts, 0.0))
        vals["vapour_pressure_deficit"].append(1.0)
    return times, vals


def test_noon_values_and_24h_rain_window():
    # rain at 12:00 day1 (belongs to day1's window), 13:00 day1 (day2's window), 12:00 day2 (day2), 13:00 day2 (day3)
    precip = {"2026-03-01T12:00": 1.0, "2026-03-01T13:00": 2.0, "2026-03-02T12:00": 4.0, "2026-03-02T13:00": 8.0}
    times, vals = hourly(datetime(2026, 3, 1), 72, precip)
    days = {d["date"]: d for d in derive_daily(times, vals, now_local=datetime(2026, 3, 2, 18))}
    assert days["2026-03-01"]["rain24_noon"] is None  # window starts before series
    assert days["2026-03-02"]["rain24_noon"] == pytest.approx(6.0)
    assert days["2026-03-03"]["rain24_noon"] == pytest.approx(8.0)
    assert days["2026-03-02"]["t_noon"] == 32.0 and days["2026-03-02"]["rh_noon"] == 66.0
    assert days["2026-03-02"]["t_max"] == 43.0 and days["2026-03-02"]["rh_min"] == 44.0
    assert days["2026-03-02"]["is_forecast"] == 0
    assert days["2026-03-03"]["is_forecast"] == 1


@pytest.mark.parametrize("x,expected", [(20, 0), (40, 100), (30, 50), (10, 0), (50, 100)])
def test_linear_score_clipping(x, expected):
    assert linear_score(x, 20, 40) == pytest.approx(expected)


def test_inverted_scale_for_humidity():
    assert linear_score(15, 80, 15) == 100
    assert linear_score(80, 80, 15) == 0


@pytest.mark.parametrize("s,c", [(0, "Low"), (24.9, "Low"), (25, "Moderate"), (49.9, "Moderate"), (50, "High"), (74.9, "High"), (75, "Very High"), (100, "Very High"), (None, None)])
def test_composite_class_boundaries(s, c):
    assert composite_class(s) == c


def test_composite_complete():
    r = compute_composite(W, t_max=40, rh_min=15, ws_max=40, dry_days=30, ndmi=-0.1, ndvi=0.5)
    assert r["score"] == 100.0 and r["class"] == "Very High" and r["completeness"] == 1.0
    r = compute_composite(W, t_max=20, rh_min=80, ws_max=5, dry_days=0, ndmi=0.4, ndvi=0.5)
    assert r["score"] == 0.0 and r["class"] == "Low"


def test_composite_refuses_with_missing_factor():
    r = compute_composite(W, t_max=35, rh_min=30, ws_max=20, dry_days=15, ndmi=None, ndvi=None)
    assert r["score"] is None and r["status"] == "insufficient_data"
    assert r["missing"] == ["V"] and r["completeness"] == 0.8


def test_sparse_vegetation_sets_v_to_zero():
    r = compute_composite(W, t_max=35, rh_min=30, ws_max=20, dry_days=15, ndmi=-0.3, ndvi=0.05)
    v = next(f for f in r["factors"] if f["key"] == "V")
    assert v["score"] == 0.0 and "sparse" in v["note"]


def test_contributions_sum_to_score():
    r = compute_composite(W, t_max=33, rh_min=35, ws_max=18, dry_days=12, ndmi=0.1, ndvi=0.4)
    assert sum(f["contribution"] for f in r["factors"]) == pytest.approx(r["score"], abs=0.3)


def test_grid_and_cell_assignment():
    g = geo.build_grid("karnataka")
    assert 200 < len(g) < 400
    assert all(abs((c.east - c.west) - 0.25) < 1e-9 for c in g)
    assert geo.cell_for_point("karnataka", 77.59, 12.97) == "karnataka:12.875,77.625"
    assert geo.cell_for_point("karnataka", 72.0, 19.0) is None  # Arabian Sea
    assert geo.state_for_point(77.59, 12.97) == "Karnataka"
    assert geo.state_for_point(80.27, 13.08) == "Tamil Nadu"


@pytest.mark.parametrize("bbox", [(10, 0, 5, 1), (0, 10, 1, 5), (-200, 0, 0, 1), (0, -95, 1, 0)])
def test_bbox_validation(bbox):
    with pytest.raises(ValueError):
        geo.validate_bbox(bbox)


def test_haversine():
    assert geo.haversine_km(12.97, 77.59, 13.08, 80.27) == pytest.approx(290, abs=10)
