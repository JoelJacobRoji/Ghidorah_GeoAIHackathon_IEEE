"""Regional FWI class calibration and the v0.2 Dryness factor."""
import json
import math
from datetime import date, timedelta

import numpy as np
import pytest

from incendium import fwi
from incendium.risk import DRY_DAYS_LOOKBACK, compute_composite, days_since_rain

W = {"T": 0.25, "H": 0.25, "W": 0.20, "D": 0.15, "V": 0.15}


def _rain(values, end="2026-03-31"):
    e = date.fromisoformat(end)
    n = len(values)
    return {(e - timedelta(days=n - 1 - i)).isoformat(): v for i, v in enumerate(values)}


def test_days_since_rain_counts_from_last_significant_day():
    r = _rain([0.0] * 40 + [7.0] + [0.0] * 5 + [4.9] + [0.0] * 3)  # 4.9 mm is below the threshold
    assert days_since_rain(r, "2026-03-31") == 9
    assert days_since_rain(_rain([0.0] * 40 + [5.0]), "2026-03-31") == 0  # same-day rain -> 0


def test_days_since_rain_unknown_when_history_too_short():
    assert days_since_rain(_rain([0.0] * 10), "2026-03-31") is None  # gap reached before saturation
    assert days_since_rain(_rain([0.0] * 35), "2026-03-31") == 35  # saturated: score is 100 either way
    assert days_since_rain(_rain([0.0] * 100), "2026-03-31") == DRY_DAYS_LOOKBACK
    r = _rain([0.0] * 40)
    r[(date(2026, 3, 31) - timedelta(days=3)).isoformat()] = None  # missing day inside the window
    assert days_since_rain(r, "2026-03-31") is None


def test_dryness_factor_scoring_and_note():
    c = compute_composite(W, t_max=30, rh_min=40, ws_max=10, dry_days=15, ndmi=0.1, ndvi=0.5)
    d = next(f for f in c["factors"] if f["key"] == "D")
    assert d["score"] == 50.0 and d["unit"] == "days" and d["note"] is None
    c = compute_composite(W, t_max=30, rh_min=40, ws_max=10, dry_days=DRY_DAYS_LOOKBACK, ndmi=0.1, ndvi=0.5)
    d = next(f for f in c["factors"] if f["key"] == "D")
    assert d["score"] == 100.0 and "at least" in d["note"]
    assert compute_composite(W, t_max=30, rh_min=40, ws_max=10, dry_days=None, ndmi=0.1, ndvi=0.5)["missing"] == ["D"]


def test_calibrate_thresholds_are_percentiles():
    vals = np.arange(0, 10000) / 100.0  # 0 .. 99.99, uniform
    th = fwi.calibrate_thresholds(vals.tolist() + [None, float("nan")])
    assert th == pytest.approx([25.0, 50.0, 75.0, 90.0, 97.0], abs=0.02)
    with pytest.raises(ValueError):
        fwi.calibrate_thresholds([1.0] * 10)


def test_scheme_from_thresholds_and_classing():
    scheme = fwi.scheme_from_thresholds([10, 20, 30, 40, 50])
    assert [n for n, _, _ in scheme] == list(fwi.CLASS_NAMES)
    assert scheme[0][1] == 0.0 and math.isinf(scheme[-1][2])
    assert fwi.fwi_class(9.99, scheme) == "Low"
    assert fwi.fwi_class(20, scheme) == "High"
    assert fwi.fwi_class(50, scheme) == "Very Extreme"
    assert fwi.fwi_class(None, scheme) is None
    for bad in ([10, 20, 30, 40], [10, 10, 30, 40, 50], [0, 20, 30, 40, 50]):
        with pytest.raises(ValueError):
            fwi.scheme_from_thresholds(bad)


def test_class_scheme_falls_back_to_effis(tmp_path):
    p = tmp_path / "cal.json"
    assert fwi.class_scheme("karnataka", p) == (fwi.FWI_CLASSES, None)
    p.write_text(json.dumps({"karnataka": {"thresholds": [10, 20, 30, 40, 50]}}))
    scheme, cal = fwi.class_scheme("karnataka", p)
    assert cal is not None and scheme[1][1] == 10
    assert fwi.class_scheme("odisha", p) == (fwi.FWI_CLASSES, None)
