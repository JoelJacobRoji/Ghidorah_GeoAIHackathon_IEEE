"""Machine-readable methodology, attribution and limitations (served at /api/methodology)."""
from __future__ import annotations

import math

from .fwi import CALIBRATION_PERCENTILES, FWI_CLASSES, class_scheme
from .risk import (
    COMPOSITE_CLASSES, DRY_DAYS_LOOKBACK, FACTOR_RULES, NDVI_SPARSE, SIGNIFICANT_RAIN_MM, VEG_MAX_AGE_DAYS,
    VEG_MIN_VALID_FRACTION,
)


def _classes(scheme) -> list[dict]:
    return [{"name": n, "min": lo, "max": None if math.isinf(hi) else hi} for n, lo, hi in scheme]


def regional_fwi_classes(regions: list[str]) -> dict:
    """Class scheme in use per region (calibrated if available, otherwise EFFIS prototype)."""
    out = {}
    for r in regions:
        scheme, cal = class_scheme(r)
        out[r] = {
            "scheme": "calibrated" if cal else "effis_prototype",
            "classes": _classes(scheme),
            "calibration": cal,
        }
    return out


def methodology(weights: dict[str, float], regions: list[str] | None = None) -> dict:
    return {
        "version": "0.2-prototype",
        "sources": [
            {
                "id": "firms",
                "name": "NASA FIRMS — VIIRS 375 m Active Fire / Thermal Anomalies (NOAA-20, NOAA-21), NRT",
                "url": "https://firms.modaps.eosdis.nasa.gov/",
                "what": "Satellite-detected thermal anomalies at ~375 m pixel resolution, with acquisition time (UTC), confidence (low/nominal/high) and fire radiative power (MW).",
                "latency": "Near-real-time products are published within ~3 hours of the satellite overpass on a best-effort basis.",
                "attribution": "We acknowledge the use of data and imagery from NASA's Fire Information for Resource Management System (FIRMS) (https://www.earthdata.nasa.gov/data/tools/firms), part of NASA's Earth Science Data and Information System (ESDIS). NASA VIIRS Land Science Team, VIIRS I-Band 375 m Active Fire NRT products.",
                "notes": "Suomi NPP stopped producing data on 2 November 2026, so Incendium uses NOAA-20 and NOAA-21 only.",
            },
            {
                "id": "open-meteo",
                "name": "Open-Meteo Forecast API (best-match model blend)",
                "url": "https://open-meteo.com/",
                "what": "Hourly 2 m temperature, relative humidity, 10 m wind speed/direction/gusts, precipitation and vapour-pressure deficit at each grid-cell centre. These are numerical weather-model values (past days and forecast), not station observations.",
                "resolution": "Global models ~9–25 km; regional models finer where available. Incendium samples one point per 0.25° cell.",
                "attribution": "Weather data by Open-Meteo.com (CC BY 4.0).",
                "licence": "https://creativecommons.org/licenses/by/4.0/",
            },
            {
                "id": "cdse",
                "name": "Copernicus Sentinel-2 L2A via Copernicus Data Space Ecosystem (Sentinel Hub Statistical API)",
                "url": "https://dataspace.copernicus.eu/",
                "what": "Per-cell mean NDVI and NDMI over 10-day intervals within the last 40 days, from cloud-masked surface reflectance (~0.002° sampling).",
                "attribution": "Contains modified Copernicus Sentinel data processed by the Copernicus Data Space Ecosystem.",
                "licence": "Free, full and open access under the Legal Notice on the use of Copernicus Sentinel Data and Service Information.",
            },
            {
                "id": "basemap",
                "name": "OpenFreeMap vector tiles (OpenMapTiles schema, OpenStreetMap data)",
                "url": "https://openfreemap.org/",
                "attribution": "© OpenFreeMap © OpenMapTiles Data from OpenStreetMap",
            },
            {
                "id": "terrain",
                "name": "Mapterhorn terrain tiles (Terrarium encoding)",
                "url": "https://mapterhorn.com/",
                "attribution": "© Mapterhorn (see https://mapterhorn.com/attribution for underlying elevation sources)",
            },
            {
                "id": "osm",
                "name": "OpenStreetMap via the Overpass API",
                "url": "https://www.openstreetmap.org/copyright",
                "what": "Fire stations (amenity=fire_station) across India; cities and towns across India and villages, hamlets and suburbs in the risk regions (refreshed weekly); roads, tracks and waterways fetched on demand for firebreak analysis windows.",
                "attribution": "© OpenStreetMap contributors, available under the Open Database Licence (ODbL).",
                "licence": "https://opendatacommons.org/licenses/odbl/",
            },
            {
                "id": "boundaries",
                "name": "Natural Earth 1:10m admin-1 states and provinces",
                "url": "https://www.naturalearthdata.com/",
                "attribution": "Made with Natural Earth (public domain).",
                "notes": "Used only for state outlines and tagging detections. Incendium does not draw or assert national boundaries.",
            },
        ],
        "fwi": {
            "name": "Canadian Forest Fire Weather Index (FWI) System",
            "reference": "Van Wagner (1987), Canadian Forestry Service Technical Report 35",
            "why": "A widely used fire-weather framework (e.g. EFFIS in Europe). The Forest Survey of India states it is developing an FWI-based danger rating using NASA GFWED inputs.",
            "inputs": "Local-noon temperature, relative humidity and 10 m wind speed; precipitation accumulated over the 24 h ending at noon.",
            "components": {
                "FFMC": "Fine Fuel Moisture Code — moisture of litter and fine fuels; responds within hours to a day.",
                "DMC": "Duff Moisture Code — moisture of loosely compacted organic layers; responds over ~2 weeks.",
                "DC": "Drought Code — deep, compact organic layers; seasonal drought indicator (~7 weeks).",
                "ISI": "Initial Spread Index — combines wind and FFMC: expected rate of spread.",
                "BUI": "Buildup Index — combines DMC and DC: fuel available for combustion.",
                "FWI": "Fire Weather Index — combines ISI and BUI: fire-line intensity potential.",
            },
            "startup": "FFMC 85, DMC 6, DC 15, followed by a 45-day spin-up period that is not displayed.",
            "day_length": "Latitude-adjusted day-length factors following the GFWED convention (bands at ±15° and ±30°). Karnataka straddles 15°N, so cells north and south of it use different DMC/DC day-length factors, an inherent discontinuity of the method.",
            "classes": _classes(FWI_CLASSES),
            "classes_note": "EFFIS (Europe) thresholds, shown for reference. They are not calibrated for Indian fuels or climate; regions with a calibration use their own thresholds (regional_classes).",
            "regional_classes": regional_fwi_classes(regions or []),
            "calibration_method": (
                f"Class boundaries at percentiles {', '.join(f'P{p:g}' for p in CALIBRATION_PERCENTILES)} of the pooled daily FWI "
                "over every cell-day of one reference fire season (Feb–May, ERA5 inputs). Only FWI values are used; detections are used "
                "afterwards, on a different season, to test the classes. Classes are therefore relative to that region's own fire season: "
                "'Very Extreme' means FWI in the top 3 % of reference-season cell-days, not an absolute danger level."
            ),
        },
        "composite": {
            "name": "Incendium experimental composite",
            "formula": "R = wT·T + wH·H + wW·W + wD·D + wV·V",
            "weights": weights,
            "factors": [
                {"key": k, "label": v[0], "input": v[1], "unit": v[2], "score_0_at": v[3], "score_100_at": v[4]}
                for k, v in FACTOR_RULES.items()
            ],
            "classes": [{"name": n, "min": lo, "max": min(hi, 100)} for n, lo, hi in COMPOSITE_CLASSES],
            "rules": [
                "Each factor is normalised linearly to 0–100 between the stated breakpoints and clipped.",
                "A composite score is produced only when all five factors are available; otherwise the cell is marked 'insufficient data'.",
                f"Vegetation intervals need at least {VEG_MIN_VALID_FRACTION:.0%} cloud-free valid pixels and must be at most {VEG_MAX_AGE_DAYS} days old.",
                f"Cells with NDVI below {NDVI_SPARSE} are treated as sparsely vegetated (little fuel): vegetation factor = 0.",
                f"Dryness counts days since the last 24 h (noon-to-noon) model rain total of at least {SIGNIFICANT_RAIN_MM:g} mm; it is unknown if a missing day is reached before 30 days, and capped at {DRY_DAYS_LOOKBACK}.",
            ],
            "changes": [
                {
                    "version": "0.2",
                    "change": "Dryness factor: FWI Drought Code (0 at 0, 100 at 600) replaced by days since significant rain (0 at 0 days, 100 at 30 days).",
                    "reason": "In the Feb–May 2026 Karnataka retrospective comparison the Drought Code was inversely associated with satellite detections (AUC 0.33; 0.45 within month): it keeps accumulating into May while burning declines once pre-monsoon showers begin. Parameters of the replacement were fixed a priori; the decision itself was informed by 2026 data, so other seasons provide the independent test.",
                },
            ],
            "status": "Weights and breakpoints are initial assumptions for interpretability. The composite is not validated and is not an ignition probability.",
        },
        "modules": _modules(),
        "grid": {
            "cell_size_deg": 0.25,
            "rationale": "Matches the effective resolution of the weather models; finer cells would imply precision the inputs do not have.",
            "cell_inclusion": "A cell is included if at least 20% of its area lies inside the region boundary.",
        },
        "limitations": [
            "Satellite detections are thermal anomalies, not confirmed wildfires. They include agricultural burning, industrial heat sources and other hot surfaces.",
            "Detections can be missed under cloud or smoke, between overpasses, or for fires smaller or cooler than the sensor can resolve.",
            "Weather values are model estimates at ~9–25 km resolution, not measurements at a specific site; local terrain and microclimate effects are not resolved.",
            "The FWI system was developed for Canadian forest fuels; Indian deciduous forests and grasslands behave differently. Thresholds are uncalibrated for India.",
            "NDVI measures greenness, not dryness. NDMI is a better (still indirect) proxy for vegetation water content. Neither measures dead fuel moisture directly.",
            "Sentinel-2 values are composites over 10-day intervals and may be stale or missing under persistent cloud.",
            "Risk indicators describe fire-weather and fuel conditions. They do not predict where or whether a fire will start.",
        ],
        "validation": {
            "software": "Automated tests cover parsing, validation, unit handling, deduplication, normalisation boundaries and FWI equations (cross-checked against the xclim reference implementation).",
            "scientific": "No predictive accuracy is claimed. A retrospective comparison (below) reports how often VIIRS detected thermal anomalies on cell-days in each FWI and composite class over a past fire season, with confidence intervals, AUC with a cell-level bootstrap, a month-stratified AUC that removes seasonal co-variation, and composite weight sensitivity. This is a descriptive association, not a validation of predictive skill.",
        },
    }


def _modules() -> dict:
    from . import alerts, firebreak, ml, spread

    def doc(m) -> str:
        return (m.__doc__ or "").strip()

    return {
        "prediction": {"title": "Wildfire prediction", "number": 1,
                       "summary": "Rule-based fire-weather indices (FWI, experimental composite) plus an experimental machine-learning model of satellite-detected burning, tested out of sample.",
                       "method": doc(ml)},
        "monitoring": {"title": "Geospatial monitoring", "number": 2,
                       "summary": "Live map of risk zones, vegetation condition, model weather, wind and active satellite detections with provenance and freshness."},
        "firebreak": {"title": "Firebreak recommendation", "number": 3,
                      "summary": "Ranks existing roads, tracks and waterways as candidate fuel-break anchors between hazardous fuel and downwind settlements.",
                      "method": doc(firebreak)},
        "spread": {"title": "Fire spread simulation", "number": 4,
                   "summary": "Illustrative hour-by-hour spread scenario from an ignition point using FBP fire-behaviour equations, Sentinel-2 fuel, terrain and hourly model weather.",
                   "method": doc(spread)},
        "alerts": {"title": "Emergency alert system", "number": 5,
                   "summary": "Rule-based alerts with the nearest fire stations; notifications only to operator-configured recipients, never automatic contact with authorities.",
                   "method": doc(alerts)},
    }
