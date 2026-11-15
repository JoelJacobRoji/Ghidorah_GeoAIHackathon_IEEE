# Incendium retrospective comparison: Karnataka, 2025-02-01 → 2025-05-31

_Generated 2026-11-14T16:06:57Z by `python -m incendium validate`. All numbers below are computed from real archived data; nothing is tuned to this sample._

> **This is a descriptive association study, not a validation of predictive skill.** VIIRS thermal anomalies include agricultural burning, and FWI describes fire weather, not ignitions.

## Data

- Weather: Open-Meteo archive API, ERA5 reanalysis (0.25°), 2024-12-01 → 2025-05-31 (62-day FWI spin-up before the study period). Cell centres (x.125°) are served from the nearest ERA5 grid node.
- Detections: NASA FIRMS VIIRS_NOAA20_SP, VIIRS_NOAA21_SP. Standard-processing (science-quality) archive; the live app uses near-real-time products.
- Vegetation: Copernicus Sentinel-2 L2A (Statistical API), 10-day intervals, 0.004° sampling, 282 cells.
- 282 cells × days = **33,840 cell-days**; FWI available for 33,840, composite for 33,840.
- **11,727 cell-days had ≥ 1 detection** (base rate 34.65 %); 280 of 282 cells had at least one. 42,462 detections fell inside the grid.

## Detection rate by FWI class (prototype EFFIS thresholds)

| Class | Cell-days | With detection | Rate | 95 % CI | Lift vs. base |
|---|---:|---:|---:|---|---:|
| Low | 7,802 | 401 | 5.14 % | 4.67 % – 5.65 % | 0.15 |
| Moderate | 2,434 | 406 | 16.68 % | 15.25 % – 18.21 % | 0.48 |
| High | 10,673 | 4,305 | 40.34 % | 39.41 % – 41.27 % | 1.16 |
| Very High | 9,377 | 4,854 | 51.76 % | 50.75 % – 52.78 % | 1.49 |
| Extreme | 3,472 | 1,735 | 49.97 % | 48.31 % – 51.63 % | 1.44 |
| Very Extreme | 82 | 26 | 31.71 % | 22.65 % – 42.40 % | 0.91 |

## Detection rate by Karnataka-calibrated FWI class (percentiles P25, P50, P75, P90, P97 of season 2025)

**IN-SAMPLE: these thresholds were derived from this season's FWI values (detections were not used).** Thresholds: 14.18, 34.06, 42.47, 50.4, 57.78. Detection rate rises with every class (classes with ≥ 200 cell-days): **no**.

| Class | Cell-days | Share of cell-days | With detection | Rate | 95 % CI | Lift vs. base |
|---|---:|---:|---:|---:|---|---:|
| Low | 8,459 | 25.0 % | 492 | 5.82 % | 5.34 % – 6.34 % | 0.17 |
| Moderate | 8,461 | 25.0 % | 2,755 | 32.56 % | 31.57 % – 33.57 % | 0.94 |
| High | 8,458 | 25.0 % | 4,085 | 48.30 % | 47.23 % – 49.36 % | 1.39 |
| Very High | 5,077 | 15.0 % | 2,725 | 53.67 % | 52.30 % – 55.04 % | 1.55 |
| Extreme | 2,369 | 7.0 % | 1,203 | 50.78 % | 48.77 % – 52.79 % | 1.47 |
| Very Extreme | 1,016 | 3.0 % | 467 | 45.96 % | 42.92 % – 49.04 % | 1.33 |

## Detection rate by experimental composite class

| Class | Cell-days | With detection | Rate | 95 % CI | Lift vs. base |
|---|---:|---:|---:|---|---:|
| Low | 901 | 4 | 0.44 % | 0.17 % – 1.14 % | 0.01 |
| Moderate | 7,392 | 436 | 5.90 % | 5.38 % – 6.46 % | 0.17 |
| High | 20,495 | 8,898 | 43.42 % | 42.74 % – 44.10 % | 1.25 |
| Very High | 5,052 | 2,389 | 47.29 % | 45.91 % – 48.67 % | 1.36 |

## Discrimination (AUC)

Probability that a randomly chosen event cell-day has a higher indicator value than a randomly chosen non-event cell-day (0.5 = no association). 95 % interval from 300 bootstrap resamples of whole cells. AUC computed separately within each calendar month and averaged (weighted by pair counts); removes seasonal co-variation between fire weather and burning.

| Indicator | AUC (pooled) | 95 % CI | AUC within month |
|---|---:|---|---:|
| Fire Weather Index | 0.724 | 0.708 – 0.740 | 0.612 |
| FFMC (fine fuel dryness) | 0.708 | 0.690 – 0.725 | 0.594 |
| ISI (spread) | 0.720 | 0.704 – 0.734 | 0.613 |
| BUI (build-up) | 0.677 | 0.663 – 0.691 | 0.544 |
| Drought Code | 0.479 | 0.463 – 0.494 | 0.501 |
| Days since significant rain (≥ 5 mm) | 0.704 | 0.693 – 0.716 | 0.554 |
| Experimental composite (v0.2, Dryness = days since rain) | 0.725 | 0.706 – 0.745 | 0.644 |
| Experimental composite (v0.1, Dryness = Drought Code) | 0.671 | 0.655 – 0.689 | 0.639 |
| Composite factor T: Temperature | 0.579 | 0.561 – 0.597 | 0.552 |
| Composite factor H: Low humidity | 0.706 | 0.689 – 0.722 | 0.602 |
| Composite factor W: Wind | 0.468 | 0.452 – 0.487 | 0.533 |
| Composite factor D: Dryness | 0.704 | 0.694 – 0.715 | 0.563 |
| Composite factor V: Vegetation moisture | 0.682 | 0.660 – 0.703 | 0.674 |

Restricting events to nominal/high-confidence detections:

- Fire Weather Index: AUC 0.715 (0.699 – 0.731), within month 0.600
- Experimental composite (v0.2, Dryness = days since rain): AUC 0.706 (0.683 – 0.725), within month 0.622
- Experimental composite (v0.1, Dryness = Drought Code): AUC 0.639 (0.617 – 0.658), within month 0.613

## Monthly context

| Month | Cell-days | With detection | Rate | Mean FWI | Mean composite |
|---|---:|---:|---:|---:|---:|
| 2025-02 | 7,896 | 4,515 | 57.18 % | 42.6 | 68.3 |
| 2025-03 | 8,742 | 4,137 | 47.32 % | 38.8 | 69.7 |
| 2025-04 | 8,460 | 2,148 | 25.39 % | 24.7 | 58.5 |
| 2025-05 | 8,742 | 927 | 10.60 % | 13.4 | 47.5 |

## Hits, misses and false alarms at the prototype 'High' threshold

| Rule | Hits | Misses | False alarms | Correct negatives | Hit rate | False-alarm ratio | Share of cell-days flagged |
|---|---:|---:|---:|---:|---:|---:|---:|
| FWI ≥ 21.3 (prototype 'High' or above) | 10,920 | 807 | 12,684 | 9,429 | 93.1 % | 53.7 % | 69.8 % |
| Composite v0.1 ≥ 50.0 (Dryness = Drought Code) | 11,632 | 95 | 17,343 | 4,770 | 99.2 % | 59.9 % | 85.6 % |
| Composite ≥ 50.0 (prototype 'High' or above) | 11,287 | 440 | 14,260 | 7,853 | 96.2 % | 55.8 % | 75.5 % |
| FWI ≥ 34.06 (calibrated 'High' or above) | 8,480 | 3,247 | 8,440 | 13,673 | 72.3 % | 49.9 % | 50.0 % |

## Composite weight sensitivity

- Default weights {'T': 0.25, 'H': 0.25, 'W': 0.2, 'D': 0.15, 'V': 0.15}: AUC 0.725.
- 1000 random weight vectors (Dirichlet(1,1,1,1,1)): AUC min 0.547, 5th pct 0.647, median 0.719, 95th pct 0.759, max 0.770. Default weights rank at the 57th percentile.
- One-at-a-time ±0.10 changes (weights renormalised):

| Factor | −0.10 | +0.10 |
|---|---:|---:|
| T (Temperature) | 0.736 | 0.716 |
| H (Low humidity) | 0.723 | 0.726 |
| W (Wind) | 0.730 | 0.721 |
| D (Dryness) | 0.713 | 0.730 |
| V (Vegetation moisture) | 0.707 | 0.735 |

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
