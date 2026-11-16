# Incendium — Early Wildfire Awareness & Environmental Risk Intelligence

Incendium is a geospatial intelligence web app for wildfire awareness in India. It combines:

- **Satellite thermal-anomaly detections**: NASA FIRMS, VIIRS 375 m, NOAA-20 / NOAA-21, near-real-time.
- **Fire-weather danger**: the Canadian Fire Weather Index (FWI), computed daily from Open-Meteo model weather on a 0.25° grid.
- **Vegetation condition**: Sentinel-2 L2A NDVI and NDMI with cloud masking, from the Copernicus Data Space Statistical API.
- **An experimental, fully explained composite indicator** that shows how temperature, humidity, wind, dryness and vegetation each contribute.

It is organised as **five connected modules** (from the Team Ghidorah GeoAI Hackathon 2026 proposal):

| # | Module | Where | What it does |
|---|---|---|---|
| 1 | Wildfire prediction | Dashboard risk surfaces · Methodology | FWI (with Karnataka-calibrated classes), the experimental composite, and an experimental ML detection-likelihood model trained on 2025 and tested on 2026 |
| 2 | Geospatial monitoring | Dashboard (Monitor) · Detections · Regional analysis | Risk zones, vegetation, model weather, wind and live satellite detections with provenance |
| 3 | Firebreak recommendation | Dashboard → Firebreaks | Proposes firebreak lines along the forest edge that separate the zone within 500 m of roads and settlements (where most fires are started by people) from large, high-intensity forest behind it. |
| 4 | Fire spread simulation | Dashboard → Spread (or "Simulate spread" on a detection/alert) | Hour-by-hour spread scenario from an ignition point (FBP equations, Sentinel-2 fuel, terrain, hourly model weather) |
| 5 | Emergency alert system | Alerts page · nav badge | Rule-based alerts with the nearest fire stations; optional email/webhook to recipients you configure; never contacts authorities automatically |

The project was renamed from EMBER to Incendium; old `EMBER_*` environment variables are still read as a fallback.

It is a research prototype, not an official warning service. Detections are heat signals, not confirmed fires, and risk indicators describe conditions; they do not predict ignitions.

## Live demo

**https://162.35.42.114**

The app is hosted on a VPS and reached directly by its IP address over HTTPS. Plain `http://` redirects to HTTPS automatically.

---

## 1. Architecture

```
Browser (React 19 · TypeScript · Vite 8 · Tailwind 4)
  ├─ Landing: React Three Fiber globe (NASA Blue Marble) + real FIRMS points
  ├─ Dashboard: MapLibre GL 6 · OpenFreeMap vector basemap (restyled) · Mapterhorn 3D terrain
  ├─ Regional analysis · Detection explorer · Methodology
  └─ typed API client with request de-duplication (no secrets in the bundle)
          │  /api (JSON / GeoJSON)
Flask API (Python 3.11)
  ├─ providers/  firms.py · openmeteo.py · cdse.py · http.py (timeouts, retries, backoff, secret masking)
  ├─ ingest.py   ingestion jobs, quota budgeting, provenance, retention
  ├─ fwi.py      Van Wagner (1987) FWI system, GFWED latitude convention
  ├─ risk.py     experimental composite + plain-language explanations
  ├─ geo.py      regions, Natural Earth state polygons, 0.25° grid, point→cell lookup
  ├─ terrain.py  Mapterhorn DEM: per-cell elevation/slope, local DEM windows
  ├─ fuel.py     Sentinel-2 median NDVI/NDMI fuel proxy for local windows (Process API)
  ├─ spread.py   Module 4: hourly FFMC, FBP rate of spread, elliptical time-dependent Dijkstra
  ├─ firebreak.py Module 3: forest-edge firebreak lines between the road/settlement zone and deep forest
  ├─ alerts.py   Module 5: incident clustering, rules, persistence filter, email/webhook
  ├─ ml.py       Module 1: detection-likelihood model (train 2025 / test 2026), live scoring
  ├─ places.py · providers/osm.py  OpenStreetMap fire stations, settlements, lines (Overpass)
  ├─ scheduler.py APScheduler: FIRMS 30 min (+ alerts) · weather 3 h (+ outlook) · vegetation weekly · OSM weekly
  └─ SQLite (WAL): detections, cells, weather_hourly/daily, fwi_daily, veg_stats, risk_snapshot, provider_runs, api_usage,
                   cell_terrain, osm_features, alerts
```

No request from the dashboard reaches an upstream provider directly. Jobs fill SQLite, and the API reads from it. When a provider fails, the API serves the last valid data with its timestamp and a `stale` / `degraded` state. It never substitutes invented values.

## 2. Technology stack

| Layer | Choice |
|---|---|
| Frontend | React 19, TypeScript 7, Vite 8, Tailwind CSS 4, React Router 7 |
| Maps | MapLibre GL JS 6, OpenFreeMap (Positron, restyled), Mapterhorn Terrarium DEM (terrain + hillshade) |
| 3D | three.js 0.187, React Three Fiber 9 (custom globe and atmosphere shaders) |
| Motion | GSAP (camera), Motion for React (UI), shared tokens in `src/lib/motion.ts`, reduced-motion aware |
| Charts | Recharts 3 (palettes validated for colour-vision deficiency) |
| Backend | Flask 3, Pydantic 2, httpx, pandas, numpy, shapely 2, APScheduler 3, python-dotenv |
| Storage | SQLite (WAL). PostGIS is unnecessary: the grid is regular, so point→cell lookup is arithmetic |
| Tests | pytest + respx (backend); Vitest + Testing Library (frontend); playwright-core visual smoke test using your installed Chrome |

## 3. Directory structure

```
Incendium/
├─ backend/
│  ├─ incendium/                 Flask app package (see architecture)
│  │  └─ data/               regions.json, india_states.geojson (Natural Earth, public domain)
│  ├─ tests/                 pytest suite (synthetic fixtures only)
│  ├─ requirements.txt       runtime deps
│  ├─ requirements-dev.txt   + pytest, respx, xclim (FWI reference)
│  └─ .env.example
├─ frontend/
│  ├─ src/{components,lib,pages,test}
│  ├─ public/textures/earth-blue-marble.jpg  (NASA Blue Marble, public domain)
│  └─ scripts/screenshots.mjs                 visual smoke test
└─ docs/API.md
```

## 4. Setup

Prerequisites: Python 3.11, Node 24 or 26 LTS (installed via fnm here), Git, and Chrome or Edge.

```powershell
# backend
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m incendium check-credentials     # prints present/valid only, never the values
.venv\Scripts\python -m incendium bootstrap             # first run: ~15–25 min (see §6 quotas)
.venv\Scripts\python -m incendium serve                 # http://127.0.0.1:5000, scheduler on

# frontend (second terminal)
cd frontend
npm install
npm run dev                                         # http://localhost:5173
```

### Environment variables

Secrets are read from the process environment, then `backend/.env` (git-ignored), then (on Windows) your user environment (`HKCU\Environment`). Incendium never writes them to disk or logs. Any accidental occurrence in a log line is masked as `***`.

| Variable | Required | Purpose |
|---|---|---|
| `FIRMS_MAP_KEY` | yes | NASA FIRMS Area API key (free, https://firms.modaps.eosdis.nasa.gov/api/map_key/) |
| `CDSE_CLIENT_ID`, `CDSE_CLIENT_SECRET` | yes | Copernicus Data Space OAuth client (free, Sentinel Hub dashboard → User settings → OAuth clients) |
| `INCENDIUM_DB_PATH` | no | default `instance/incendium.sqlite3` |
| `INCENDIUM_RISK_REGIONS` | no | comma list of region ids with a risk grid (default `karnataka`; also `uttarakhand`, `odisha`) |
| `INCENDIUM_CORS_ORIGINS` | no | default `http://localhost:5173,http://127.0.0.1:5173` |
| `INCENDIUM_SCHEDULER` | no | `1`/`0`, run ingestion jobs inside the API process |
| `INCENDIUM_ALLOW_MANUAL_REFRESH` | no | `1`/`0`, allow the localhost-only refresh endpoint |
| `INCENDIUM_COMPOSITE_WEIGHTS` | no | five weights for T,H,W,D,V summing to 1 (default `0.25,0.25,0.20,0.15,0.15`) |
| `INCENDIUM_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM`, `INCENDIUM_ALERT_EMAIL_TO` | no | alert emails to recipients you choose (all four of host, user, password, recipients must be set) |
| `INCENDIUM_ALERT_WEBHOOK_URL` | no | alert JSON POSTed to a webhook you control |
| `INCENDIUM_ALERT_MIN_PRIORITY` | no | `low` / `medium` (default) / `high` |
| `VITE_API_BASE` | no (frontend) | API origin for production builds, if not served from the same origin |

## 5. External integrations

| Provider | Endpoint | Auth | Limits honoured |
|---|---|---|---|
| NASA FIRMS | `/api/area/csv/{key}/{VIIRS_NOAA20_NRT,VIIRS_NOAA21_NRT}/68,6,97.5,37.5/{1..5}` | MAP_KEY | 5 000 transactions / 10 min; Incendium uses ~4 requests every 30 min |
| Open-Meteo | `api.open-meteo.com/v1/forecast` (hourly, 25 locations per request) | none (non-commercial) | 600/min, 5 000/h, 10 000/day; Incendium budgets ≤ 450 weighted calls/min and ≤ 9 000/day, assuming each location counts as a call |
| Copernicus Data Space | `sh.dataspace.copernicus.eu/api/v1/statistics` + OIDC token | OAuth client credentials | 10 000 requests and 10 000 PU / month; 300 / min. **Measured: 0.79 PU per cell request**, so a weekly Karnataka run is ~225 PU (~970 PU/month). Limited to 60 req/min; 429s honour `Retry-After` |
| OpenFreeMap | `tiles.openfreemap.org/styles/positron` | none | no stated limits |
| Mapterhorn | `tiles.mapterhorn.com/tilejson.json` (Terrarium, 512 px WebP) | none | map terrain in the browser; the backend also caches tiles on disk for per-cell terrain (z10, ~260 tiles once) and local windows (z11/12) |
| Copernicus Process API | `sh.dataspace.copernicus.eu/api/v1/process` | same OAuth client | fuel maps for spread/firebreak windows (≤ 220 × 220 px, multi-temporal median), cached 7 days |
| OpenStreetMap Overpass | `overpass-api.de/api/interpreter` | none | fire stations + settlements once a week (3 queries); roads/waterways per firebreak window, cached 7 days; one request at a time. © OpenStreetMap contributors (ODbL) |

Suomi NPP stopped producing data on 2 November 2026 (FIRMS FAQ), so it is deliberately not used.

## 6. Data model & processing

- **Detections**: identity = satellite + overpass minute + position rounded to 4 decimals. Each is tagged with an Indian state (Natural Earth polygon) and a grid cell when inside an enabled region. Detections are kept for 30 days.
- **Weather**: hourly values per cell centre (last 72 h + 3 forecast days kept), plus daily rows holding noon temperature, RH, wind and the 24 h rain ending at noon (13:00 D‑1 → 12:00 D), daily extremes, and an `is_forecast` flag. The first warm-up pulls 92 past days; later refreshes pull 4.
- **FWI**: run per cell over the whole contiguous daily series with start values 85/6/15. The first 45 days are flagged as spin-up and hidden. A missing day breaks the series, and later days are marked `missing_inputs`.
- **Vegetation**: 10-day NDVI/NDMI intervals over the last 40 days per cell, SCL-masked (no data, saturated, shadow, water, cloud, cirrus and snow are excluded). Usable intervals need ≥ 10 % valid pixels and must be ≤ 45 days old.
- **Composite**: computed only when all five factors exist; otherwise `insufficient_data`. Stored per cell per date with factors, percentiles and explanation text.

## 7. Risk methodology (summary; full text on the Methodology page)

**FWI (primary).** Van Wagner (1987) equations, cross-checked against `xclim` at latitudes 12°, 16°, 25° and 45° N.
- Day-length factors follow GFWED (bands at ±15°/±30°); Karnataka straddles 15° N.
- **Classes.** A region with a calibration in `backend/incendium/data/fwi_calibration.json` uses its own thresholds; otherwise the EFFIS thresholds apply (Low < 11.2 < Moderate < 21.3 < High < 38 < Very High < 50 < Extreme < 70 < Very Extreme), labelled **prototype, not calibrated for India**.
- **Calibration method** (`python -m incendium calibrate-fwi`), fixed before any reference-season data was examined: class boundaries at the 25th, 50th, 75th, 90th and 97th percentiles of the pooled daily FWI over every cell-day of one reference fire season (Feb–May, ERA5 inputs). Only FWI values are used, never detections. Detections are used afterwards, on a different season, to test the classes. Classes are therefore relative to the region's own fire season: "Very Extreme" means FWI in the top 3 % of reference-season cell-days, not an absolute danger level.
- **Karnataka thresholds** (reference season Feb–May 2025, 33 840 cell-days over 282 cells): Low < 14.18 ≤ Moderate < 34.06 ≤ High < 42.47 ≤ Very High < 50.40 ≤ Extreme < 57.78 ≤ Very Extreme. These are much higher than EFFIS, because Karnataka's dry season routinely produces FWI values that EFFIS would call Very High.

**Experimental composite.** `R = 0.25T + 0.25H + 0.20W + 0.15D + 0.15V`. Each factor is normalised linearly and clipped:

| Factor | Input | Score 0 at | Score 100 at |
|---|---|---|---|
| T | daily max 2 m temperature | 20 °C | 40 °C |
| H | daily min RH | 80 % | 15 % |
| W | daily max 10 m wind speed | 5 km/h | 40 km/h |
| D | days since the last 24 h (noon-to-noon) rain total ≥ 5 mm | 0 days | 30 days |
| V | NDMI | 0.4 | −0.1 |

If NDVI < 0.15 the cell is treated as sparsely vegetated and V = 0. Dryness is unknown (so no composite) if a missing weather day is reached before 30 days; it is capped at 60.

**Change in composite v0.2.** Dryness was the FWI Drought Code (0 at 0, 100 at 600) in v0.1. The 2026 Karnataka comparison showed the Drought Code inversely associated with detections (AUC 0.33; 0.45 within month): it keeps accumulating into May while burning declines once pre-monsoon showers start. The replacement's 5 mm threshold (about the Keetch–Byram net-rain threshold) and 30-day saturation (dead fine and medium fuels are long equilibrated by then) were fixed a priori. Because the decision to change was informed by 2026, other seasons provide the independent test. Prototype classes are Low < 25 ≤ Moderate < 50 ≤ High < 75 ≤ Very High. The weights are assumptions. The score is **not** an ignition probability.

### Retrospective comparison (`python -m incendium validate`)

This reruns the full pipeline over a past season. It then compares the indicators with what satellites actually detected. The default is Karnataka, 1 Feb – 31 May 2026; `--start/--end` select another season (for example 2025-02-01 / 2025-05-31). Seasons whose weather does not fit in the remaining Open-Meteo 24 h budget are fetched partly, cached, and finished on a later run.

**Data, each retrieved once and cached under `backend/instance/validation/cache/`:**
- **Weather:** Open-Meteo ERA5 reanalysis, with a 62-day FWI spin-up from December.
- **Detections:** NASA FIRMS **standard-processing** VIIRS NOAA-20/21.
- **Vegetation:** Sentinel-2 10-day NDVI/NDMI.

**Unit of analysis:** one cell on one local day. The event is "≥ 1 detection in the cell that day".

**What is reported:**
- detection rate per FWI and composite class, with 95 % Wilson intervals and lift;
- AUC per indicator and per composite factor, with a 95 % cell-cluster bootstrap;
- a **within-month AUC** that removes the seasonal co-variation of fire weather and burning;
- monthly context;
- hits, misses and false alarms at the prototype "High" thresholds;
- composite weight sensitivity: 1 000 Dirichlet samples plus ±0.10 one-at-a-time changes;
- the v0.1 composite (Drought Code) next to v0.2, for comparison;
- when the region has an FWI calibration: detection rate per calibrated class, each class's share of cell-days, a monotonicity check and hits/false alarms at calibrated "High", marked **in-sample** for the reference season and **out-of-sample** otherwise.

Composite weights are not tuned on any season.

**Outputs:**
- `backend/instance/validation/report-karnataka-<season>.json`, served at `/api/validation?season=` and shown on the Methodology page with a season switch;
- `docs/VALIDATION-karnataka-<season>.md`.

This is a descriptive association, not a test of predictive skill (see the limitations in the report).

### The planning and response modules

**Fire spread simulation (Module 4, `POST /api/simulate/spread`).**
- **Fuel moisture:** hourly FFMC (Van Wagner 1977 hourly equations) is started from the previous day's daily FFMC and driven by the cell's hourly model weather.
- **Rate of spread:** FBP head rate of spread uses surrogate fuel types, O-1a for grass and D-1 for forest, with shrub as their mean. Fuel classes come from a 45-day Sentinel-2 median NDVI.
- **Shape:** an elliptical wind shape uses the FBP length-to-breadth ratio, and an uphill slope factor adds terrain.
- **Arrival times:** computed with a time-dependent Dijkstra over a 16-neighbour grid. Long moves cannot jump over non-fuel cells.
- **Output:** hourly isochrones, settlements reached (OpenStreetMap) and the nearest fire stations.
- **Limits:** this is illustrative, not a forecast. There is no spotting, suppression or crown fire, and roads are not barriers.

**Firebreak recommendation (Module 3, `POST /api/firebreaks`).**
- **Why:** most fires here are started by people near roads and villages. The goal is a line that holds such a fire at the forest edge instead of letting it run into the forest interior.
- **Design fire day:** firebreaks are built before the season, so each cell uses its fire-season 90th-percentile FFMC, BUI and wind, and the prevailing afternoon wind direction, from Feb–May 2025 and 2026 (ERA5). `python -m incendium firebreak-design` builds `incendium/data/firebreak_design.json`.
- **Hazard surface:** 100 m FBP head-fire intensity (kW/m): grass O-1a, forest C-7 (open forest with grass/litter understorey, surface fire), shrub the mean, times the slope factor. Absolute scale: 4000 kW/m = 1.
- **Human ignition zone:** within 500 m of motorable OSM roads or of settlements' approximate built-up edge.
- **Fuel:** fire-season Sentinel-2 imagery (1 Feb–30 Apr of the latest season), when crops are harvested and deciduous forest is leaf-off.
- **Lines:** the outer edge of that zone, kept only where it is in forest/shrub, at least 5 km² of forest lies behind it, and fire-season intensity there is ≥ 2000 kW/m. Lines shorter than 1 km are dropped.
- **High-risk test:** at least 3 VIIRS forest-fire detections within 2 km in the Feb–May 2025 and 2026 seasons. Evergreen and plantation belts that rarely burn drop out.
- **Score:** forest hazard × past fires × wind blowing from the zone into the forest (weighted by how steady it is) × human pressure × forest size protected × gentle terrain. Existing tracks or streams along a line are named as possible anchors. Proposals for field assessment only.

**Alerts (Module 5, `GET /api/alerts`).**
- **Incidents:** detections within 2 km and 30 min of each other are grouped into incidents.
- **Rules:**
  - near a settlement: a town within 5 km, or a village within 2 km;
  - FWI class Very High or above;
  - intense: at least 50 MW FRP or at least 3 detections.
- **Priority:** high when all three rules apply, medium for two, low for one.
- **Persistent sources:** detections on at least 5 of the previous 10 days at the same spot are capped at low priority. This catches industrial sources and gas flares.
- **Outlook:** a daily fire-weather outlook covers forecast cells in the top two FWI classes.
- **Fire stations:** each alert lists the nearest fire stations from OpenStreetMap.
- **Notifications:** sent by email or webhook only to recipients configured in the environment, at most once every 15 min. Authorities are never contacted automatically.

**Detection-likelihood model (Module 1, `python -m incendium ml-train`, `GET /api/ml/report`).**
- **Training and test:** trained on Feb–May 2025 and tested once on Feb–May 2026.
- **Inputs:** FWI components, weather, days since rain, NDVI/NDMI, terrain, the cell's fire history from the two prior seasons (NOAA-20 only) and day of year.
- **Models:** logistic regression and gradient-boosted trees with fixed settings.
- **Baselines:** compared against FWI alone and fire history alone.
- **What it predicts:** satellite-detected burning, including crop fires. It does not predict wildfire ignition.
- **Out of season:** live scores outside February–May are flagged as outside the training range.

### Results: Karnataka, trained or calibrated on 2025, tested on 2026

The unit is one 0.25° cell on one local day, and the event is "VIIRS detected at least one thermal anomaly in the cell that day". About 35 % of cell-days have one: the base rate is 34.7 % in 2025 and 35.6 % in 2026. 95 % intervals come from a cell-cluster bootstrap for AUCs and Wilson intervals for rates. Full reports are in `docs/VALIDATION-karnataka-2025.md`, `docs/VALIDATION-karnataka-2026.md` and on the Methodology page.

**Calibrated FWI classes, out-of-sample on 2026:**

| Class | Share of cell-days | Detection rate (95 % CI) | Lift vs base rate |
|---|---|---|---|
| Low | 9.8 % | 8.7 % (7.8–9.7) | 0.24 |
| Moderate | 32.7 % | 27.1 % (26.2–27.9) | 0.76 |
| High | 30.2 % | 42.7 % (41.7–43.7) | 1.20 |
| Very High | 18.3 % | 47.7 % (46.4–48.9) | 1.34 |
| Extreme | 6.4 % | 51.1 % (49.0–53.2) | 1.44 |
| Very Extreme | 2.5 % | 39.3 % (36.0–42.6) | 1.10 |

- Detection rates climb steadily from Low to Extreme, but not strictly: **Very Extreme is below Extreme**, in 2025 (in-sample) as well. The highest FWI values come late in the season, when burning is already declining.
- At calibrated "High" or above: hit rate 72.7 %, false-alarm ratio 54.9 %, with 57.5 % of cell-days flagged. 2026 was drier than 2025, so fewer cell-days fell in Low than the 25 % the percentiles imply.

**Indicator AUC, 2026 (0.5 = no skill):**

| Indicator | AUC (95 % CI) | Within-month AUC |
|---|---|---|
| FWI | 0.655 (0.638–0.673) | 0.625 |
| ISI | 0.658 | 0.626 |
| Experimental composite v0.2 | 0.616 (0.593–0.640) | 0.645 |
| Drought Code | 0.330 | 0.445 |

FWI's AUC was 0.724 in 2025, so its seasonal skill varies from year to year.

**Detection-likelihood model, out-of-sample on 2026** (tested once; 12 055 positive cell-days):

| Model | AUC (95 % CI) | Within-month AUC | Brier skill vs climatology |
|---|---|---|---|
| **Gradient-boosted trees (shown on the map)** | **0.806 (0.792–0.818)** | **0.761** | **0.26** |
| Gradient-boosted trees without fire history | 0.803 (0.786–0.815) | 0.754 | — |
| Logistic regression | 0.793 (0.778–0.811) | 0.749 | 0.24 |
| Baseline: fire history alone | 0.705 (0.680–0.725) | 0.726 | — |
| Baseline: FWI alone | 0.655 (0.638–0.673) | 0.625 | — |

- **Selection:** gradient-boosted trees were chosen over logistic regression because of their higher 2026 AUC. This is the only choice made after seeing the test season, and it adds a small optimistic bias.
- **Overfitting:** training AUC is 0.894 (logistic regression 0.839), so there is some overfitting, but it generalises well beyond both baselines.
- **What drives it:** by permutation importance on 2026, day of year (AUC drop 0.091) and fire history (0.078) dominate. Elevation, NDVI, NDMI, minimum humidity, Drought Code and FFMC each contribute under 0.01. Much of the skill is knowing when and where burning usually happens, which also includes crop-residue burning.

### Wind flow layer (illustrative)

The dashboard's "Wind flow" toggle animates streaks over the map on a 2D canvas, re-projected every frame so they stay registered in 2D and 3D.
- **Wind source:** Open-Meteo 10 m model wind for the current hour.
- **Interpolation:** bilinear between 0.25° cell centres (`src/lib/wind.ts`).
- **Speed:** visually scaled and constant across zoom levels.
- **Labelling:** marked illustrative in the layer list and legend. It is not a wind simulation.
- **Reduced motion:** the layer is disabled; the static wind arrows remain available.

## 8. Scientific limitations

- Detections include agricultural burning, industrial heat and other hot surfaces. Cloud, smoke, overpass timing and small or cool fires cause misses.
- The detection window is a bounding box that includes parts of neighbouring countries; those detections are labelled "outside Indian state polygons". Incendium does not draw national boundaries itself.
- Weather is model output at roughly 9–25 km resolution, not station data. Local terrain and microclimate effects are not resolved.
- FWI was designed for Canadian forest fuels. Indian deciduous forests and grasslands respond differently. Regional class thresholds come from one reference season of ERA5-driven FWI; the live app uses forecast-model weather, whose FWI distribution may differ somewhat.
- The calibration and the ML model are tested on one independent season (2026) in one region (Karnataka). Other years and regions may behave differently.
- The ML model learns satellite-detected burning, including crop fires, mostly from season and fire history. It is not an ignition or wildfire-probability model, and it was trained on February–May only.
- NDVI is greenness, not dryness. NDMI is an indirect water-content proxy. Neither measures dead-fuel moisture.
- Sentinel-2 composites can be stale or missing under persistent cloud.

## 9. Testing

```powershell
cd backend;  .venv\Scripts\python -m pytest            # 107 tests
cd frontend; npx vitest run                            # 38 tests
cd frontend; node scripts/screenshots.mjs <outDir>     # visual smoke test (needs both servers running)
```

The backend suite covers:
- FIRMS parsing: types, UTC padding, rejected rows, duplicates, plain-text errors, secret masking.
- HTTP behaviour: retries, timeouts, bounds.
- Open-Meteo rain-window and noon logic, and unit-mismatch rejection.
- FWI behaviour and agreement with xclim.
- Normalisation and class boundaries, missing-factor refusal, the sparse-vegetation rule.
- Grid and state assignment, bbox validation.
- Ingestion with mocked upstreams: dedupe, failure without data, incremental refresh.
- API validation (9 bad-input cases), filters, 404s, the localhost-only refresh, and that internals don't leak.
- Validation statistics: Wilson intervals, AUC against a brute-force pairwise computation (with ties), a constructed seasonal-confounding case for the within-month AUC, cluster bootstrap, contingency counts, and an end-to-end report on cached synthetic inputs with no network.

The frontend suite covers formatting (UTC/IST, missing values never shown as 0), class thresholds that mirror the backend, non-colour class badges, error and stale states, source status, accessible factor bars, detection semantics, Explorer filtering and keyboard rows, and the WebGL fallback.

**Software tests are not scientific validation.** No predictive accuracy is claimed (see Methodology → Validation).

## 10. Performance notes

- three.js and MapLibre load lazily per page, and only one WebGL scene is alive at a time. Unmounting the map calls `map.remove()`, which releases the GL context.
- Detections are clustered below zoom 7. Point radius scales with √FRP. The pulse animation is throttled to ~25 fps and disabled for reduced motion.
- API reads are indexed SQLite queries. A full risk map is about 160 KB and 24 h of India-window detections about 1.8 MB.

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `check-credentials` says missing | Restart the terminal after setting user env vars, or add `backend/.env` |
| Risk map 404 "No risk data computed yet" | FWI needs 45+ days of weather. Run `python -m incendium ingest weather --warmup` |
| Composite shows "insufficient data" | Vegetation not ingested yet, or the cell is cloudy. Run `python -m incendium ingest vegetation` |
| Copernicus HTTP 429 in logs | Per-minute processing-unit limit. Incendium waits for `Retry-After` and continues |
| Map blank, console "Worker failed to load" | Use the provided `vite.config.ts` (the MapLibre worker is bundled via `?worker&url`) |
| Status "stale" | The provider failed or the API ran without the scheduler. Start `serve` without `--no-jobs` |

## 12. Deployment

Deployment is **deferred by decision**: Incendium runs locally until the build is complete, then we choose a host together. Requirements for any host:
- a persistent disk for SQLite, or a migration to managed Postgres;
- secrets set as host environment variables;
- an IP that is not shared with heavy Open-Meteo users;
- the API served over HTTPS behind the same origin as the static frontend (or with `VITE_API_BASE` and `INCENDIUM_CORS_ORIGINS` set).

## 13. Attribution

Detections: NASA FIRMS, NASA LANCE (VIIRS NOAA-20/21 NRT). Weather data by Open-Meteo.com (CC BY 4.0). Contains modified Copernicus Sentinel data processed by the Copernicus Data Space Ecosystem. Basemap © OpenFreeMap © OpenMapTiles, data © OpenStreetMap contributors. Fire stations, settlements, roads and waterways © OpenStreetMap contributors (ODbL), via the Overpass API. Terrain © Mapterhorn. Fire-behaviour equations: Canadian Forest Fire Behavior Prediction System (Forestry Canada 1992, ST-X-3) and Van Wagner (1977, 1987). Boundaries: Made with Natural Earth. Globe imagery: NASA Blue Marble (public domain).
