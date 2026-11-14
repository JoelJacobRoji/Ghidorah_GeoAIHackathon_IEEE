from datetime import datetime, timezone

import httpx
import pytest
import respx

from incendium.providers import firms
from incendium.providers.http import ProviderError, make_client

from .conftest import firms_csv

BBOX = (68.0, 6.0, 97.5, 37.5)


def test_parse_basic_and_types():
    text = firms_csv(["14.12345,76.54321,330.5,0.39,0.36,2026-11-12,0805,N20,VIIRS,n,2.0NRT,295.1,4.7,D"])
    dets, rejected = firms.parse_csv(text, "VIIRS_NOAA20_NRT", BBOX)
    assert rejected == 0 and len(dets) == 1
    d = dets[0]
    assert d.satellite == "NOAA-20"
    assert d.confidence == "nominal"
    assert d.acq_datetime == datetime(2026, 11, 12, 8, 5, tzinfo=timezone.utc)
    assert d.frp == pytest.approx(4.7)


def test_acq_time_zero_padding():
    dets, _ = firms.parse_csv(firms_csv(["14.1,76.5,330,0.4,0.4,2026-11-12,5,N21,VIIRS,h,2.0NRT,295,1.0,N"]), "VIIRS_NOAA21_NRT", BBOX)
    assert dets[0].acq_datetime.hour == 0 and dets[0].acq_datetime.minute == 5
    assert dets[0].satellite == "NOAA-21"
    assert dets[0].confidence == "high"


def test_rejects_invalid_and_out_of_bbox_rows():
    text = firms_csv(
        [
            "abc,76.5,330,0.4,0.4,2026-11-12,0805,N20,VIIRS,n,2.0NRT,295,1.0,D",
            "14.1,176.5,330,0.4,0.4,2026-11-12,0805,N20,VIIRS,n,2.0NRT,295,1.0,D",
            "14.1,76.5,330,0.4,0.4,not-a-date,0805,N20,VIIRS,n,2.0NRT,295,1.0,D",
            "95.0,76.5,330,0.4,0.4,2026-11-12,0805,N20,VIIRS,n,2.0NRT,295,1.0,D",
            "14.1,76.5,330,0.4,0.4,2026-11-12,0805,N20,VIIRS,l,2.0NRT,295,,D",
        ]
    )
    dets, rejected = firms.parse_csv(text, "VIIRS_NOAA20_NRT", BBOX)
    assert rejected == 4
    assert len(dets) == 1 and dets[0].frp is None and dets[0].confidence == "low"


def test_duplicates_collapse():
    row = "14.1,76.5,330,0.4,0.4,2026-11-12,0805,N20,VIIRS,n,2.0NRT,295,1.0,D"
    dets, _ = firms.parse_csv(firms_csv([row, row]), "VIIRS_NOAA20_NRT", BBOX)
    assert len(dets) == 1


def test_empty_response_is_not_an_error():
    assert firms.parse_csv("", "VIIRS_NOAA20_NRT", BBOX) == ([], 0)
    dets, _ = firms.parse_csv(firms_csv([]), "VIIRS_NOAA20_NRT", BBOX)
    assert dets == []


def test_plain_text_error_raises_and_masks_key():
    from incendium.log import register_secret

    register_secret("TESTKEY_firms_0123456789abcdef")
    with pytest.raises(ProviderError) as e:
        firms.parse_csv("Invalid MAP_KEY: TESTKEY_firms_0123456789abcdef", "VIIRS_NOAA20_NRT", BBOX)
    assert "TESTKEY_firms_0123456789abcdef" not in str(e.value)


@respx.mock
def test_fetch_area_url_and_http_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    route = respx.get(url__startswith=firms.BASE).mock(return_value=httpx.Response(503))
    with make_client() as c, pytest.raises(ProviderError):
        firms.fetch_area(c, "KEY123456", "VIIRS_NOAA20_NRT", BBOX, 2)
    assert route.call_count == 4  # bounded retries
    assert "/VIIRS_NOAA20_NRT/68,6,97.5,37.5/2" in str(route.calls[0].request.url)


@respx.mock
def test_timeout_is_handled(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    respx.get(url__startswith=firms.BASE).mock(side_effect=httpx.ReadTimeout("slow"))
    with make_client() as c, pytest.raises(ProviderError):
        firms.fetch_area(c, "KEY123456", "VIIRS_NOAA20_NRT", BBOX, 1)


def test_day_range_validation():
    with make_client() as c, pytest.raises(ValueError):
        firms.fetch_area(c, "KEY", "VIIRS_NOAA20_NRT", BBOX, 6)
