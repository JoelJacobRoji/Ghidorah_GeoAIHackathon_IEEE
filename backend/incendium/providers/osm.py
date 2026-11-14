"""OpenStreetMap features via the Overpass API.

Used for fire stations, settlements (alerts, simulation, firebreak planning) and,
on demand per analysis window, roads/tracks and waterways (firebreak anchors).

Data © OpenStreetMap contributors, available under the Open Database Licence
(ODbL 1.0). Requests are few, cached, and follow the Overpass usage policy
(one request at a time, generous timeouts, identifying User-Agent without
personal data).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import timedelta
from pathlib import Path

import httpx

from ..config import BACKEND_DIR
from .http import ProviderError, make_client, request_with_retry

log = logging.getLogger(__name__)

PROVIDER = "OpenStreetMap Overpass"
URL = "https://overpass-api.de/api/interpreter"
ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"
LINE_CACHE_DIR = BACKEND_DIR / "instance" / "cache" / "osm"
LINE_CACHE_MAX_AGE = timedelta(days=7)
KEEP_TAGS = ("name", "name:en", "population", "phone", "contact:phone", "emergency", "operator", "website",
             "contact:website", "addr:city", "addr:district", "place", "amenity", "highway", "waterway", "surface")

# Area filters (ISO 3166). Only point features are used; Incendium never draws boundaries.
AREA = {
    "IN": 'area["ISO3166-1"="IN"][admin_level=2]',
    "IN-KA": 'area["ISO3166-2"="IN-KA"]',
    "IN-UT": 'area["ISO3166-2"="IN-UT"]',
    "IN-OR": 'area["ISO3166-2"="IN-OR"]',
}

_last = {"t": 0.0}


def _post(query: str, timeout: float = 240) -> dict:
    wait = 2.0 - (time.monotonic() - _last["t"])
    if wait > 0:
        time.sleep(wait)
    with make_client(timeout=timeout + 30) as client:
        resp = request_with_retry(client, PROVIDER, "POST", URL, data={"data": query}, attempts=3, base_delay=10.0)
    _last["t"] = time.monotonic()
    if resp.status_code != 200:
        raise ProviderError(PROVIDER, f"HTTP {resp.status_code}", resp.status_code)
    try:
        body = resp.json()
    except (json.JSONDecodeError, httpx.DecodingError) as exc:
        raise ProviderError(PROVIDER, f"invalid JSON ({type(exc).__name__})") from exc
    remark = body.get("remark") or ""
    if "error" in remark.lower() or "timed out" in remark.lower():
        # Overpass reports server-side timeouts/memory errors with HTTP 200 and partial or no data.
        raise ProviderError(PROVIDER, f"server remark: {remark[:160]}")
    return body


def _tags(el: dict) -> dict:
    t = el.get("tags") or {}
    return {k: t[k] for k in KEEP_TAGS if k in t}


def _point(el: dict) -> tuple[float, float] | None:
    if "lat" in el and "lon" in el:
        return float(el["lat"]), float(el["lon"])
    c = el.get("center")
    if c:
        return float(c["lat"]), float(c["lon"])
    return None


def parse_points(body: dict, kind: str) -> list[dict]:
    out = []
    for el in body.get("elements", []):
        pt = _point(el)
        if pt is None:
            continue
        tags = _tags(el)
        if kind == "settlement":
            subtype = tags.get("place")
        else:
            subtype = tags.get("amenity")
        out.append({
            "id": f"{el['type']}/{el['id']}", "kind": kind, "subtype": subtype,
            "name": tags.get("name:en") or tags.get("name"), "lat": pt[0], "lon": pt[1], "tags": tags,
        })
    return out


def fetch_fire_stations(scope: str = "IN") -> list[dict]:
    q = f"""[out:json][timeout:200];
{AREA[scope]}->.a;
nwr["amenity"="fire_station"](area.a);
out center tags;"""
    return parse_points(_post(q), "fire_station")


def fetch_settlements(scope: str, places: tuple[str, ...]) -> list[dict]:
    rx = "|".join(places)
    q = f"""[out:json][timeout:200];
{AREA[scope]}->.a;
node["place"~"^({rx})$"](area.a);
out body;"""
    return parse_points(_post(q), "settlement")


# ---------------------------------------------------------------------------- lines for one window
LINE_HIGHWAYS = ("trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "track", "service", "path")
LINE_WATERWAYS = ("river", "stream", "canal")


def parse_lines(body: dict) -> list[dict]:
    out = []
    for el in body.get("elements", []):
        if el.get("type") != "way" or not el.get("geometry"):
            continue
        tags = _tags(el)
        coords = [(float(p["lon"]), float(p["lat"])) for p in el["geometry"]]
        if len(coords) < 2:
            continue
        kind = "road" if "highway" in tags else "waterway"
        out.append({"id": f"way/{el['id']}", "kind": kind, "class": tags.get("highway") or tags.get("waterway"),
                    "name": tags.get("name:en") or tags.get("name"), "coords": coords})
    return out


def fetch_lines(bbox: tuple[float, float, float, float], cache_dir: Path | None = None) -> tuple[list[dict], str]:
    """Roads/tracks and waterways inside bbox (w, s, e, n). Cached on disk for 7 days."""
    from ..db import iso, utcnow

    w, s, e, n = (round(v, 3) for v in bbox)
    key = hashlib.sha1(f"{w},{s},{e},{n}".encode()).hexdigest()[:16]
    cdir = cache_dir or LINE_CACHE_DIR
    path = cdir / f"lines_{key}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        from datetime import datetime

        age = utcnow() - datetime.fromisoformat(cached["retrieved_at"].replace("Z", "+00:00"))
        if age < LINE_CACHE_MAX_AGE:
            return cached["lines"], cached["retrieved_at"]
    hw = "|".join(LINE_HIGHWAYS)
    ww = "|".join(LINE_WATERWAYS)
    q = f"""[out:json][timeout:120];
(way["highway"~"^({hw})$"]({s},{w},{n},{e});
 way["waterway"~"^({ww})$"]({s},{w},{n},{e}););
out geom tags;"""
    lines = parse_lines(_post(q, timeout=120))
    retrieved = iso(utcnow())
    cdir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lines": lines, "retrieved_at": retrieved, "bbox": [w, s, e, n]}), encoding="utf-8")
    return lines, retrieved
