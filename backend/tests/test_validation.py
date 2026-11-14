"""Statistics and pipeline tests for the retrospective comparison (synthetic data, test-only)."""
import json
from datetime import date, timedelta

import numpy as np
import pytest

from incendium import fwi as fwi_mod
from incendium import validation as V
from incendium.geo import build_grid


def brute_auc(s, y):
    pos, neg = s[y], s[~y]
    tot = 0.0
    for p in pos:
        tot += np.sum(p > neg) + 0.5 * np.sum(p == neg)
    return tot / (len(pos) * len(neg))


def test_wilson_known_values():
    lo, hi = V.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3) and hi == pytest.approx(0.7634, abs=1e-3)
    assert V.wilson(0, 10)[0] == 0.0
    assert V.wilson(0, 0) == (None, None)


def test_auc_matches_bruteforce_with_ties():
    rng = np.random.default_rng(1)
    s = rng.integers(0, 6, 300).astype(float)
    y = rng.random(300) < 0.3
    assert V.auc(s, y) == pytest.approx(brute_auc(s, y), abs=1e-12)


def test_auc_edge_cases():
    y = np.array([0, 0, 1, 1], bool)
    assert V.auc(np.array([1, 2, 3, 4.0]), y) == 1.0
    assert V.auc(np.array([4, 3, 2, 1.0]), y) == 0.0
    assert V.auc(np.array([1, 1, 1, 1.0]), y) == 0.5
    assert V.auc(np.array([1, np.nan, 3, 4.0]), y) == 1.0
    assert V.auc(np.array([1, 2.0]), np.array([0, 0], bool)) is None


def test_stratified_auc_removes_seasonal_confounding():
    # Two "months": month B is both hotter and has more events, but within each month
    # the score carries no information. Pooled AUC is high; within-month AUC is 0.5.
    rng = np.random.default_rng(3)
    n = 4000
    month = np.array(["A"] * n + ["B"] * n)
    score = np.concatenate([rng.normal(10, 1, n), rng.normal(30, 1, n)])
    y = np.concatenate([rng.random(n) < 0.02, rng.random(n) < 0.20])
    assert V.auc(score, y) > 0.7
    assert V.stratified_auc(score, y, month) == pytest.approx(0.5, abs=0.04)


def test_cluster_bootstrap_brackets_point_estimate():
    rng = np.random.default_rng(5)
    clusters = np.repeat(np.arange(60), 50)
    s = rng.normal(size=3000)
    y = (s + rng.normal(scale=1.5, size=3000)) > 1.5
    lo, hi = V.cluster_bootstrap_auc(s, y, clusters, 200, np.random.default_rng(0))
    assert lo < V.auc(s, y) < hi


def test_contingency_counts():
    pred = np.array([1, 1, 0, 0, 1, 0], bool)
    obs = np.array([1, 0, 1, 0, 1, 0], bool)
    c = V.contingency(pred, obs)
    assert (c["hits"], c["false_alarms"], c["misses"], c["correct_negatives"]) == (2, 1, 1, 2)
    assert c["hit_rate"] == pytest.approx(2 / 3)
    assert c["false_alarm_ratio"] == pytest.approx(1 / 3)


def test_class_table_rates_and_lift():
    classes = ["Low"] * 80 + ["High"] * 20 + [None] * 5
    y = np.array([False] * 78 + [True] * 2 + [False] * 15 + [True] * 5 + [False] * 5)
    t = {r["class"]: r for r in V.class_table(classes, y, ["Low", "Moderate", "High"])}
    assert "Moderate" not in t
    assert t["High"]["rate"] == pytest.approx(0.25)
    assert t["High"]["lift"] == pytest.approx(0.25 / (7 / 105))
    assert t["No data"]["cell_days"] == 5


def test_weight_sensitivity_structure():
    rng = np.random.default_rng(7)
    X = rng.uniform(0, 100, (500, 5))
    y = X[:, 1] + rng.normal(0, 30, 500) > 70
    default = np.array([0.25, 0.25, 0.2, 0.15, 0.15])
    r = V.weight_sensitivity(X, y, default, 200, np.random.default_rng(1))
    assert r["default_auc"] == pytest.approx(V.auc(X @ default / default.sum(), y))
    assert r["random_weights"]["min"] <= r["random_weights"]["median"] <= r["random_weights"]["max"]
    assert len(r["one_at_a_time"]) == 10
    assert "overfit" in r["caveat"]


@pytest.mark.parametrize("calibrated", [False, True])
def test_pipeline_end_to_end_on_cached_inputs(settings, db, tmp_path, monkeypatch, calibrated):
    """Runs build/analyse on synthetic cached inputs: no network is touched."""
    cal_file = tmp_path / "fwi_calibration.json"
    if calibrated:
        cal_file.write_text(json.dumps({"karnataka": {
            "thresholds": [5, 10, 20, 30, 40], "percentiles": [25, 50, 75, 90, 97], "method": "test",
            "source": {"start": "2025-02-01", "end": "2025-05-31", "cell_days": 1, "cells": 1, "weather": "test"}}}))
    monkeypatch.setattr(fwi_mod, "CALIBRATION_FILE", cal_file)
    cfg = V.ValidationConfig(start="2026-03-01", end="2026-03-31", spinup_days=20, bootstrap=30, weight_samples=50)
    val = V.Validator(db, settings, cfg, out_dir=tmp_path)
    cells = list(build_grid("karnataka"))[:6]
    rng = np.random.default_rng(11)
    d0 = date(2026, 3, 1) - timedelta(days=20)
    wx = {"cells": {}, "retrieved_at": "2026-11-13T00:00:00Z"}
    for c in cells:
        days = []
        for i in range(52):
            d = (d0 + timedelta(days=i)).isoformat()
            t = float(rng.uniform(28, 38))
            days.append({"date": d, "t_noon": t, "rh_noon": float(rng.uniform(15, 60)), "ws_noon": float(rng.uniform(2, 20)),
                         "rain24_noon": None if i == 0 else float(rng.choice([0, 0, 0, 5])), "t_max": t + 2,
                         "rh_min": float(rng.uniform(10, 50)), "ws_max": float(rng.uniform(5, 30))})
        wx["cells"][c.id] = {"model_point": [c.lat, c.lon, 500], "days": days}
    det = {"detections": [
        {"id": f"x{i}", "source": "VIIRS_NOAA20_SP", "satellite": "NOAA-20", "lat": cells[i % 6].lat, "lon": cells[i % 6].lon,
         "acq": f"2026-03-{10 + i:02d}T08:00:00Z", "confidence": "nominal", "frp": 3.0, "daynight": "D"} for i in range(8)
    ], "bbox": [74, 11, 79, 19], "requests": 0, "retrieved_at": "2026-11-13T00:00:00Z", "sources": ["VIIRS_NOAA20_SP"]}
    veg = {"cells": {c.id: [{"interval_from": "2026-02-20", "interval_to": "2026-03-01", "ndvi_mean": 0.5, "ndvi_std": 0.1,
                             "ndmi_mean": 0.05, "ndmi_std": 0.05, "valid_fraction": 0.8, "sample_count": 15000}] for c in cells},
           "retrieved_at": "2026-11-13T00:00:00Z"}
    for name, obj in (("weather_daily.json", wx), ("detections.json", det), ("vegetation.json", veg)):
        (val.cache / name).write_text(json.dumps(obj))
    val.cells = cells
    report = val.run()
    m = report["meta"]
    assert m["cell_days"] == 6 * 31
    assert m["cell_days_with_detection"] == 8
    assert m["cell_days_with_fwi"] == 6 * 31
    assert sum(r["cell_days"] for r in report["fwi_classes"]) == 6 * 31
    assert 0 <= report["auc"]["fwi"]["auc"] <= 1
    md = V.to_markdown(report)
    assert "descriptive association study" in md and "| Class |" in md
    assert (tmp_path / "report-karnataka-2026.json").exists()
    assert m["season"] == "2026" and m["composite_version"] == "0.2"
    assert "dry_days" in report["auc"] and "composite_v01" in report["auc"]
    assert "composite_v01" in report["contingency"]
    cal = report["fwi_classes_calibrated"]
    if calibrated:
        assert cal["in_sample"] is False and cal["status"].startswith("OUT-OF-SAMPLE")
        assert sum(r["cell_days"] for r in cal["classes"]) == 6 * 31
        assert abs(sum(cal["share_by_class"].values()) - 1) < 1e-9
        assert "calibrated" in md
    else:
        assert cal is None


def test_monotonic_rates_ignores_small_classes():
    rows = [{"class": "Low", "cell_days": 500, "rate": 0.1}, {"class": "High", "cell_days": 500, "rate": 0.3},
            {"class": "Extreme", "cell_days": 50, "rate": 0.05}, {"class": "No data", "cell_days": 900, "rate": 0.0}]
    assert V._monotonic_rates(rows)
    rows[1]["rate"] = 0.05
    assert not V._monotonic_rates(rows)
