# Incendium retrospective comparison: Karnataka, 2026-02-01 → 2026-05-31

_Generated 2026-11-13T22:41:47Z by `python -m incendium validate`. All numbers below are computed from real archived data; nothing is tuned to this sample._

> **This is a descriptive association study, not a validation of predictive skill.** VIIRS thermal anomalies include agricultural burning, and FWI describes fire weather, not ignitions.

## Data

- Weather: Open-Meteo archive API, ERA5 reanalysis (0.25°), 2025-12-01 → 2026-05-31 (62-day FWI spin-up before the study period). Cell centres (x.125°) are served from the nearest ERA5 grid node.
- Detections: NASA FIRMS VIIRS_NOAA20_SP, VIIRS_NOAA21_SP. Standard-processing (science-quality) archive; the live app uses near-real-time products.
- Vegetation: Copernicus Sentinel-2 L2A (Statistical API), 10-day intervals, 0.004° sampling, 282 cells.
- 282 cells × days = **33,840 cell-days**; FWI available for 33,840, composite for 33,840.
- **12,055 cell-days had ≥ 1 detection** (base rate 35.62 %); 282 of 282 cells had at least one. 37,853 detections fell inside the grid.

## Detection rate by FWI class (prototype EFFIS thresholds)

| Class | Cell-days | With detection | Rate | 95 % CI | Lift vs. base |
|---|---:|---:|---:|---|---:|
| Low | 2,641 | 198 | 7.50 % | 6.55 % – 8.56 % | 0.21 |
| Moderate | 2,371 | 319 | 13.45 % | 12.14 % – 14.89 % | 0.38 |
| High | 14,379 | 4,797 | 33.36 % | 32.60 % – 34.14 % | 0.94 |
| Very High | 11,196 | 5,182 | 46.28 % | 45.36 % – 47.21 % | 1.30 |
| Extreme | 3,175 | 1,539 | 48.47 % | 46.74 % – 50.21 % | 1.36 |
| Very Extreme | 78 | 20 | 25.64 % | 17.26 % – 36.31 % | 0.72 |

## Detection rate by experimental composite class

| Class | Cell-days | With detection | Rate | 95 % CI | Lift vs. base |
|---|---:|---:|---:|---|---:|
| Low | 324 | 4 | 1.23 % | 0.48 % – 3.13 % | 0.03 |
| Moderate | 3,434 | 247 | 7.19 % | 6.38 % – 8.11 % | 0.20 |
| High | 22,175 | 8,665 | 39.08 % | 38.44 % – 39.72 % | 1.10 |
| Very High | 7,907 | 3,139 | 39.70 % | 38.63 % – 40.78 % | 1.11 |

## Discrimination (AUC)

Probability that a randomly chosen event cell-day has a higher indicator value than a randomly chosen non-event cell-day (0.5 = no association). 95 % interval from 300 bootstrap resamples of whole cells. AUC computed separately within each calendar month and averaged (weighted by pair counts); removes seasonal co-variation between fire weather and burning.

| Indicator | AUC (pooled) | 95 % CI | AUC within month |
|---|---:|---|---:|
| Fire Weather Index | 0.655 | 0.638 – 0.673 | 0.625 |
| FFMC (fine fuel dryness) | 0.644 | 0.624 – 0.660 | 0.613 |
| ISI (spread) | 0.658 | 0.640 – 0.673 | 0.626 |
| BUI (build-up) | 0.573 | 0.554 – 0.590 | 0.552 |
| Drought Code | 0.330 | 0.315 – 0.344 | 0.445 |
| Days since significant rain (≥ 5 mm) | 0.596 | 0.580 – 0.612 | 0.541 |
| Experimental composite (v0.2, Dryness = days since rain) | 0.616 | 0.593 – 0.640 | 0.645 |
| Experimental composite (v0.1, Dryness = Drought Code) | 0.562 | 0.540 – 0.584 | 0.636 |
| Composite factor T: Temperature | 0.466 | 0.446 – 0.486 | 0.557 |
| Composite factor H: Low humidity | 0.657 | 0.637 – 0.675 | 0.624 |
| Composite factor W: Wind | 0.493 | 0.473 – 0.514 | 0.555 |
| Composite factor D: Dryness | 0.592 | 0.581 – 0.602 | 0.550 |
| Composite factor V: Vegetation moisture | 0.640 | 0.617 – 0.661 | 0.652 |

Restricting events to nominal/high-confidence detections:

- Fire Weather Index: AUC 0.646 (0.629 – 0.663), within month 0.616
- Experimental composite (v0.2, Dryness = days since rain): AUC 0.590 (0.567 – 0.614), within month 0.625
- Experimental composite (v0.1, Dryness = Drought Code): AUC 0.533 (0.509 – 0.558), within month 0.614

## Monthly context

| Month | Cell-days | With detection | Rate | Mean FWI | Mean composite |
|---|---:|---:|---:|---:|---:|
| 2026-02 | 7,896 | 4,173 | 52.85 % | 38.4 | 64.2 |
| 2026-03 | 8,742 | 4,011 | 45.88 % | 36.8 | 67.2 |
| 2026-04 | 8,460 | 2,605 | 30.79 % | 35.3 | 69.5 |
| 2026-05 | 8,742 | 1,266 | 14.48 % | 28.2 | 61.6 |

## Hits, misses and false alarms at the prototype 'High' threshold

| Rule | Hits | Misses | False alarms | Correct negatives | Hit rate | False-alarm ratio | Share of cell-days flagged |
|---|---:|---:|---:|---:|---:|---:|---:|
| FWI ≥ 21.3 (prototype 'High' or above) | 11,538 | 517 | 17,290 | 4,495 | 95.7 % | 60.0 % | 85.2 % |
| Composite v0.1 ≥ 50.0 (Dryness = Drought Code) | 11,894 | 161 | 20,000 | 1,785 | 98.7 % | 62.7 % | 94.2 % |
| Composite ≥ 50.0 (prototype 'High' or above) | 11,804 | 251 | 18,278 | 3,507 | 97.9 % | 60.8 % | 88.9 % |

## Composite weight sensitivity

- Default weights {'T': 0.25, 'H': 0.25, 'W': 0.2, 'D': 0.15, 'V': 0.15}: AUC 0.616.
- 1000 random weight vectors (Dirichlet(1,1,1,1,1)): AUC min 0.474, 5th pct 0.544, median 0.616, 95th pct 0.667, max 0.688. Default weights rank at the 50th percentile.
- One-at-a-time ±0.10 changes (weights renormalised):

| Factor | −0.10 | +0.10 |
|---|---:|---:|
| T (Temperature) | 0.631 | 0.603 |
| H (Low humidity) | 0.607 | 0.623 |
| W (Wind) | 0.620 | 0.612 |
| D (Dryness) | 0.608 | 0.616 |
| V (Vegetation moisture) | 0.600 | 0.625 |

_The best-performing random weights are reported for transparency only; selecting them on this same sample would overfit and is not done._

## Limitations

- Detections are thermal anomalies, including agricultural and other managed burning; they are not a wildfire inventory.
- Undetected fires (cloud, smoke, small/cool fires, between overpasses) count as non-events.
- FWI describes fire-weather conditions; it does not model ignition sources, which dominate where and when burning occurs.
- Cell-days are spatially and temporally correlated; bootstrap intervals resample whole cells but do not remove temporal correlation.
- Weather is ERA5 reanalysis here, but the live app uses forecast-model analysis; results transfer only approximately.
- Composite weights were not tuned on this data. Exploring weights on the same sample would overfit.
- The v0.2 Dryness factor (days since ≥ 5 mm rain, saturating at 30 days) replaced the Drought Code after the 2026 comparison showed the Drought Code inversely associated with detections. Its parameters were fixed a priori, but the decision to replace it was informed by 2026 data, so 2026 is not an independent test of the change; other seasons are.
- One season in one region; results may not generalise to other years or regions.
