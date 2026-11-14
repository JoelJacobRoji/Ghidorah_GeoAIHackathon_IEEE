import math
import random

import numpy as np
import pytest

from incendium import fwi


def synthetic_series(n=90, seed=7):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        month = 1 + (i // 30) % 12
        day = 1 + i % 28
        rain = 0.0 if rnd.random() > 0.2 else round(rnd.uniform(0.2, 25.0), 1)
        out.append(
            fwi.DailyInput(
                f"2026-{month:02d}-{day:02d}",
                round(rnd.uniform(22, 38), 1),
                round(rnd.uniform(15, 85), 1),
                round(rnd.uniform(2, 30), 1),
                rain,
            )
        )
    return out


def test_outputs_are_bounded_and_finite():
    res = fwi.run_series(synthetic_series(), lat=14.0, spinup_days=0)
    for r in res:
        assert r.status == "ok"
        assert 0 <= r.ffmc <= 101
        assert r.dmc >= 0 and r.dc >= 0 and r.isi >= 0 and r.bui >= 0 and r.fwi >= 0
        for v in (r.ffmc, r.dmc, r.dc, r.isi, r.bui, r.fwi, r.dsr):
            assert math.isfinite(v)


def test_rain_lowers_moisture_codes():
    dry = fwi.ffmc(30, 30, 10, 0.0, 88.0)
    wet = fwi.ffmc(30, 30, 10, 20.0, 88.0)
    assert wet < dry
    assert fwi.dmc(30, 30, 20.0, 40.0, 14, 3) < fwi.dmc(30, 30, 0.0, 40.0, 14, 3)
    assert fwi.dc(30, 20.0, 300.0, 14, 3) < fwi.dc(30, 0.0, 300.0, 14, 3)


def test_hot_dry_windy_beats_cool_humid_calm():
    hot = fwi.run_series([fwi.DailyInput(f"2026-03-{d:02d}", 38, 15, 25, 0) for d in range(1, 29)], 14.0, 0)[-1]
    cool = fwi.run_series([fwi.DailyInput(f"2026-03-{d:02d}", 22, 85, 3, 0) for d in range(1, 29)], 14.0, 0)[-1]
    assert hot.fwi > cool.fwi
    assert hot.ffmc > cool.ffmc


def test_missing_input_breaks_rest_of_series():
    s = synthetic_series(10)
    s[4] = fwi.DailyInput(s[4].date, None, 40, 10, 0)
    res = fwi.run_series(s, 14.0, 0)
    assert all(r.status == "ok" for r in res[:4])
    assert all(r.status == "missing_inputs" and r.fwi is None for r in res[4:])


def test_spinup_flag():
    res = fwi.run_series(synthetic_series(50), 14.0, spinup_days=45)
    assert sum(r.spinup for r in res) == 45


@pytest.mark.parametrize(
    "value,expected",
    [(0, "Low"), (11.19, "Low"), (11.2, "Moderate"), (21.3, "High"), (38.0, "Very High"), (50.0, "Extreme"), (70.0, "Very Extreme"), (250, "Very Extreme"), (None, None)],
)
def test_fwi_class_boundaries(value, expected):
    assert fwi.fwi_class(value) == expected


@pytest.mark.parametrize("lat", [12.0, 16.0, 25.0, 45.0])
def test_matches_xclim_reference(lat):
    xr = pytest.importorskip("xarray")
    fire = pytest.importorskip("xclim.compute.fire")
    s = synthetic_series(90, seed=int(lat))
    # use a real contiguous calendar for xclim
    import pandas as pd

    times = pd.date_range("2026-01-01", periods=len(s), freq="D")
    s = [fwi.DailyInput(t.strftime("%Y-%m-%d"), d.temp, d.rh, d.wind, d.rain) for t, d in zip(times, s)]

    def da(vals, units):
        return xr.DataArray(np.array(vals, dtype=float), dims=("time",), coords={"time": times}, attrs={"units": units})

    out = fire.cffwis_indices(
        tas=da([d.temp for d in s], "degC"),
        pr=da([d.rain for d in s], "mm/d"),
        sfcWind=da([d.wind for d in s], "km/h"),
        hurs=da([d.rh for d in s], "%"),
        lat=xr.DataArray(lat, attrs={"units": "degrees_north"}),
        ffmc0=xr.DataArray(85.0, attrs={"units": ""}),
        dmc0=xr.DataArray(6.0, attrs={"units": ""}),
        dc0=xr.DataArray(15.0, attrs={"units": ""}),
        season_method=None,
        overwintering=False,
        initial_start_up=False,
    )
    names = ["dc", "dmc", "ffmc", "isi", "bui", "fwi"]
    ref = dict(zip(names, [np.asarray(o) for o in out]))
    ours = fwi.run_series(s, lat, spinup_days=0)
    # Incendium uses Van Wagner (1987) Eq. 12 as published / as in GFWED:
    #   Mo = 20 + exp(5.6348 - P/43.43)
    # xclim uses the cffdrs variant 20 + 280/exp(0.023 P). The two differ only in
    # the decay constant (1/43.43 = 0.023025 vs 0.023) and only after rain > 1.5 mm,
    # so DMC (and BUI/FWI, which depend on it) get a slightly wider tolerance.
    tol = {"dc": 0.05, "ffmc": 0.05, "isi": 0.05, "dmc": 0.3, "bui": 0.3, "fwi": 0.15}
    for key in names:
        mine = np.array([getattr(r, key) for r in ours])
        np.testing.assert_allclose(mine, ref[key], rtol=1e-3, atol=tol[key], err_msg=f"{key} differs from xclim at lat {lat}")
