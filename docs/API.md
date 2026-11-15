# Incendium backend API

Base URL in development: `http://127.0.0.1:5000` (the Vite dev server proxies `/api` to it).

All endpoints are read-only `GET`s apart from the localhost-only refresh trigger.
Errors use a consistent JSON body and meaningful status codes:

```json
{ "error": "invalid_request", "details": [{ "field": "bbox", "message": "..." }] }
```

| Status | Meaning |
|---|---|
| 400 | A query parameter failed validation (unknown parameters are rejected too) |
| 403 | Manual refresh requested from a non-local address |
| 404 | Unknown cell / region not ingested / no data computed yet |
| 429 | Manual refresh requested again too soon |
| 500 | Internal error (no internals are exposed in the body) |

Internally, every record carries its provider, observation time and retrieval time.

---

### `GET /api/health`
Liveness check. `{"status":"ok","time":"…Z"}`

### `GET /api/providers/status`
Freshness of every data source.

```json
{ "providers": [{ "id": "firms", "label": "...", "state": "fresh|partial|degraded|stale|unavailable|never_run",
  "running": false, "last_success": "…Z", "last_attempt": "…Z", "last_status": "ok", "last_message": null,
  "age_minutes": 12.3, "expected_interval_minutes": 30 }] }
```

The `stale` thresholds are: FIRMS 3 h, Open-Meteo 9 h, Copernicus 10 days, and the risk engine 9 h.

### `GET /api/regions`
The configured regions, each with its outline (Natural Earth, simplified), its grid size and whether a risk grid is ingested. The response also includes the satellite `detection_window` bounding box.

### `GET /api/hotspots`
Satellite thermal anomalies as GeoJSON points.

| Param | Type | Default | Limits |
|---|---|---|---|
| `hours` | int | 48 | 1–720 (ignored when `since` is given) |
| `since`, `until` | ISO datetime | now−hours, now | window ≤ 31 days |
| `bbox` | `w,s,e,n` | — | valid WGS84, w<e, s<n |
| `min_confidence` | `low\|nominal\|high` | — | |
| `satellite` | `NOAA-20\|NOAA-21` | — | |
| `state` | string | — | Natural Earth state name |
| `region` | region id | — | only detections inside that region's grid |
| `limit` | int | 20000 | 1–30000; `meta.truncated` is set if hit |

Feature properties: `id, satellite, instrument, source, acq_datetime (UTC), confidence, frp (MW), bright_ti4, bright_ti5 (K), scan, track (km), daynight, version, state, cell_id, retrieved_at`.

### `GET /api/risk-map?region=karnataka[&date=YYYY-MM-DD]`
The risk grid as GeoJSON polygons for one local date. It defaults to the latest analysed (non-forecast) day.
Properties per cell: FWI and its components (`fwi, fwi_class, ffmc, dmc, dc, isi, bui, fwi_status`), the experimental composite (`composite, composite_class, completeness, missing[]`), vegetation (`ndvi, ndmi`), weather (`t_max, rh_min, ws_max, ws_noon, wd_noon, rain24`), `detections_7d` and `is_forecast`.
`meta.available_dates[]` lists every date, with a forecast flag.

### `GET /api/risk-details?cell_id=karnataka:14.125,76.625[&date=…]`
The full explanation for one cell: FWI row, weather row, composite factors (`value, score, weight, contribution, rule, note`), percentiles within the region, vegetation interval, plain-language `explanation[]`, 40-day FWI and weather series, vegetation intervals, detections within 30 km over the last 7 days, and provenance.

### `GET /api/region-summary?region=karnataka[&date=…]`
Class counts, statistics, mean factor contributions, the top cells by FWI, detection counts, available dates and provider status.

### `GET /api/weather?region=karnataka[&hour_offset=-48..48]`
Model weather for the hour at or before the target time, per cell. It includes units and an `is_forecast` flag.

### `GET /api/vegetation?region=karnataka`
The latest usable Sentinel-2 interval per cell (at least 10 % valid pixels).

### `GET /api/summary`
Detection counts for the last 24 h and 7 days, counts by state, satellite and confidence, daily counts (UTC), the latest acquisition time and provider status.

### `GET /api/validation?region=karnataka[&season=2026]`
A retrospective comparison report, produced by `python -m incendium validate`. One report exists per season (the year of the study period). Without `season` the latest is returned; `meta.available_seasons[]` lists them all. Returns 404 until one has been run. Contents:
- `meta`: counts, base rate, `season`, `composite_version`;
- `fwi_classes_calibrated`: when the region has a calibration, the detection rate per calibrated class, each class's share of cell-days, the contingency table at calibrated "High", whether the rate rises monotonically across classes, and `in_sample` (true when the report's season is the calibration's reference season);
- `data`: provenance;
- `definitions`;
- `fwi_classes` and `composite_classes`: rates with Wilson 95 % CIs and lift;
- `auc`: pooled, cell-bootstrap CI and within-month, per indicator and per composite factor;
- `auc_nominal_high`;
- `monthly`;
- `contingency`;
- `weight_sensitivity`;
- `limitations`.

### `GET /api/methodology`
A machine-readable methodology: sources and attribution, FWI description and classes, composite formula, weights, rules and change log, grid, limitations and validation statement.
`fwi.regional_classes.<region>` gives the class scheme each enabled region uses: `scheme` (`calibrated` or `effis_prototype`), `classes[]` with `min`/`max`, and `calibration` (thresholds, percentiles, method and reference season) or `null`. The frontend takes its FWI legend thresholds from here.

### `POST /api/admin/refresh/<firms|weather|vegetation>`
Starts an ingestion job in the background and returns `202` immediately. It is only accepted from `127.0.0.1` / `::1`, and only when `INCENDIUM_ALLOW_MANUAL_REFRESH=1`. Minimum intervals between runs: 5 min (firms), 30 min (weather), 12 h (vegetation).

---

## Operations modules

### `POST /api/simulate/spread` (Module 4)
Body: `{ "lon": 76.63, "lat": 11.66, "start_local": "2026-03-10T12:00" (optional, IST, default current hour), "hours": 6 (1–12), "curing_pct": 85 (40–100) }`.
Returns a GeoJSON FeatureCollection of cumulative isochrones (`properties.hour`, `area_ha`, `time_local`). `meta` contains:
- the ignition (with the fuel class and the cell it snapped to) and the grid (resolution, radius, bbox);
- hourly areas, and hourly fire weather (FFMC, ISI, wind, RH, head rate of spread per fuel class, L/B);
- inputs, fuel summary and period, terrain range;
- `settlements_reached[]` (OpenStreetMap, with `arrival_hours`) and `fire_stations[]`;
- `assumptions[]`.

Status codes:
- **422** when inputs are unavailable: outside a risk region, no hourly weather for that time, or no previous-day FFMC. The message explains which.
- **429** while another heavy computation is running.
- **503** when Copernicus or Mapterhorn is unavailable.

### `POST /api/firebreaks` (Module 3)
Body: `{ "lon": 76.68, "lat": 11.75, "radius_km": 6 (3–12) }`.
Returns up to 15 ranked corridors as LineStrings. Properties:
- `rank`, `score`, `length_m`;
- `anchor` (road/waterway), `osm_class`, `name`, `osm_id`;
- score components: `upwind_hazard`, `assets`, `orientation`, `access`, `terrain`;
- `mean_slope_deg`, `burnable_share`, `settlements_protected[]`.

`meta` contains:
- the prevailing wind and the dry-condition FFMC/ISI;
- `hazard_image` (PNG data URL plus bounds);
- counts, fuel period and OpenStreetMap retrieval time;
- the scoring method and assumptions.

Errors: 422, 429 and 503 as above.

### `GET /api/alerts` (Module 5)
Query: `hours` (1–720, default 48), `min_priority` (`low|medium|high`), `kind` (`detection|outlook`), `include_acknowledged` (bool), `limit` (1–2000, default 500), `compact` (bool, default false).
With `compact=true`, each alert omits `detections[]`, `nearest_settlement` and the outlook `cells[]`/`dates[]`, and `fire_stations[]` keeps only `id`, `name`, `lat`, `lon`, `phone`, `distance_km` and `osm_url`. The Alerts page uses it, which makes each alert about 27 % smaller. `total` gives the number of alerts matching the filters, so clients can load more by raising `limit`.
Alerts are sorted by priority, then open before acknowledged, then newest. Each alert includes:
- `rules[]`, `detection_count`, `frp_total_mw`, `fwi_class`;
- `persistent_source` / `persistent_days`;
- `nearest_settlement` and `fire_stations[]` (OpenStreetMap, straight-line distance, phone if tagged);
- the full `message`.

The response also includes `summary` (open/total per priority), `channels` (whether email or webhook is configured, presence only; `automatic_authority_contact` is always false), the rule text and a disclaimer.

### `POST /api/alerts/<id>/ack`
Marks an alert as acknowledged. Only accepted from localhost (403 otherwise); 404 for an unknown id.

### `GET /api/places/fire-stations?bbox=w,s,e,n`
OpenStreetMap fire stations in the bbox as GeoJSON points. The bbox may cover at most 400 square degrees.

### `GET /api/ml/report` (Module 1)
The latest detection-likelihood model report: training and test periods, features, hyperparameters, out-of-sample AUC with a cell-bootstrap CI and within-month AUC, Brier skill, reliability bins, baselines (FWI alone, fire history alone), permutation importance and limitations. Returns 404 until `python -m incendium ml-train` has run.

`/api/risk-map` cell properties also include `ml_probability` and `ml_in_season` (false outside February–May, the training window) once a model exists.
