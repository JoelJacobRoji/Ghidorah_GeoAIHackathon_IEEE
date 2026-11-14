"""Incendium experimental composite indicator and explanations.

    R = wT*T + wH*H + wW*W + wD*D + wV*V      (default weights 0.25/0.25/0.20/0.15/0.15)

D (dryness) = days since significant rain since v0.2; it was the FWI Drought Code in v0.1.

Every factor is normalised to 0-100 with a simple, documented linear rule.
The weights and breakpoints are ASSUMPTIONS chosen for interpretability; they
are not calibrated and the result is NOT an ignition probability. The
recognised index in Incendium is the Canadian FWI (see fwi.py); this composite
exists to make individual contributions easy to inspect.

A composite score is only produced when all five factors are available.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

COMPOSITE_CLASSES: tuple[tuple[str, float, float], ...] = (
    ("Low", 0, 25),
    ("Moderate", 25, 50),
    ("High", 50, 75),
    ("Very High", 75, 100.0001),
)

# (key, label, input description, unit, x_at_0, x_at_100)
FACTOR_RULES = {
    "T": ("Temperature", "Daily maximum 2 m air temperature (model)", "°C", 20.0, 40.0),
    "H": ("Low humidity", "Daily minimum 2 m relative humidity (model)", "%", 80.0, 15.0),
    "W": ("Wind", "Daily maximum 10 m wind speed (model)", "km/h", 5.0, 40.0),
    "D": ("Dryness", "Days since significant rain (24 h total ≥ 5 mm, noon to noon, model)", "days", 0.0, 30.0),
    "V": ("Vegetation moisture", "Sentinel-2 NDMI (canopy water-content proxy), lower = drier", "NDMI", 0.4, -0.1),
}

# Dryness factor (v0.2). Replaces the FWI Drought Code, which in the 2026 Karnataka
# retrospective comparison was inversely associated with detections (it keeps
# rising into May while burning declines). Parameters fixed a priori, not fitted:
#   * 5 mm / 24 h ~ the 0.2-inch net-rain threshold of the Keetch-Byram drought
#     index; smaller totals barely wet dead fuels and are within model drizzle noise.
#   * Score saturates at 30 days: by then fine and medium dead fuels have long
#     reached equilibrium with the air (time lags of 1 h - 10 days).
SIGNIFICANT_RAIN_MM = 5.0
DRY_DAYS_LOOKBACK = 60  # values are capped here ("60 or more")

NDVI_SPARSE = 0.15  # below this, the cell is treated as sparsely vegetated (little fuel)
VEG_MIN_VALID_FRACTION = 0.10  # minimum cloud-free fraction for a vegetation interval to be usable
VEG_MAX_AGE_DAYS = 45


def select_vegetation(rows: list[dict], on: str) -> dict | None:
    """Latest usable vegetation interval for date `on` (shared by live engine and validation).

    Usable = ≥ VEG_MIN_VALID_FRACTION valid pixels, NDMI present, interval started on or
    before `on`, and interval ended no more than VEG_MAX_AGE_DAYS before `on`.
    """
    from datetime import date

    target = date.fromisoformat(on)
    best = None
    for v in rows:
        if v.get("valid_fraction") is None or v["valid_fraction"] < VEG_MIN_VALID_FRACTION or v.get("ndmi_mean") is None:
            continue
        if date.fromisoformat(v["interval_from"]) > target:
            continue
        if (target - date.fromisoformat(v["interval_to"])).days > VEG_MAX_AGE_DAYS:
            continue
        if best is None or v["interval_to"] > best["interval_to"]:
            best = v
    return best


def days_since_rain(rain_by_date: dict[str, float | None], on: str) -> int | None:
    """Days since the last 24 h total ≥ SIGNIFICANT_RAIN_MM, counting `on` itself as day 0.

    Walks back from `on` through `rain_by_date` (local date -> noon-to-noon rain, mm).
    Returns None when a missing day is reached before the score would saturate,
    because the true value then cannot be known. Capped at DRY_DAYS_LOOKBACK.
    """
    from datetime import date, timedelta

    saturate = FACTOR_RULES["D"][4]
    d = date.fromisoformat(on)
    for n in range(DRY_DAYS_LOOKBACK + 1):
        r = rain_by_date.get((d - timedelta(days=n)).isoformat())
        if r is None:
            return n if n >= saturate else None
        if r >= SIGNIFICANT_RAIN_MM:
            return n
    return DRY_DAYS_LOOKBACK


def linear_score(x: float, at0: float, at100: float) -> float:
    s = (x - at0) / (at100 - at0) * 100.0
    return max(0.0, min(100.0, s))


def composite_class(score: float | None) -> str | None:
    if score is None:
        return None
    for name, lo, hi in COMPOSITE_CLASSES:
        if lo <= score < hi:
            return name
    return COMPOSITE_CLASSES[-1][0]


@dataclass
class Factor:
    key: str
    label: str
    input: str
    unit: str
    value: float | None
    score: float | None
    weight: float
    contribution: float | None
    rule: str
    note: str | None = None


def compute_composite(
    weights: dict[str, float],
    t_max: float | None,
    rh_min: float | None,
    ws_max: float | None,
    dry_days: float | None,
    ndmi: float | None,
    ndvi: float | None,
) -> dict:
    raw = {"T": t_max, "H": rh_min, "W": ws_max, "D": dry_days, "V": ndmi}
    factors: list[Factor] = []
    missing: list[str] = []
    for key, (label, desc, unit, at0, at100) in FACTOR_RULES.items():
        x = raw[key]
        note = None
        rule = f"0 at {at0:g} {unit}, 100 at {at100:g} {unit}, linear, clipped"
        if x is None:
            missing.append(key)
            factors.append(Factor(key, label, desc, unit, None, None, weights[key], None, rule))
            continue
        score = linear_score(x, at0, at100)
        if key == "V" and ndvi is not None and ndvi < NDVI_SPARSE:
            score = 0.0
            note = f"NDVI {ndvi:.2f} < {NDVI_SPARSE}: sparse vegetation, little fuel; vegetation factor set to 0"
        if key == "D" and x >= DRY_DAYS_LOOKBACK:
            note = f"No 24 h rain total of {SIGNIFICANT_RAIN_MM:g} mm or more in at least {DRY_DAYS_LOOKBACK} days"
        factors.append(
            Factor(key, label, desc, unit, round(x, 3), round(score, 1), weights[key], round(score * weights[key], 1), rule, note)
        )
    if missing:
        score = None
    else:
        score = round(sum(f.score * f.weight for f in factors if f.score is not None), 1)
    completeness = round((5 - len(missing)) / 5, 2)
    return {
        "score": score,
        "class": composite_class(score),
        "factors": [asdict(f) for f in factors],
        "missing": missing,
        "completeness": completeness,
        "status": "ok" if score is not None else "insufficient_data",
    }


EFFIS_NOTE = "prototype EFFIS thresholds not calibrated for India"


def explain(
    composite: dict, fwi_row: dict | None, percentiles: dict[str, float] | None, class_note: str = EFFIS_NOTE
) -> list[str]:
    """Plain-language statements. Only describes values; never predicts fire occurrence."""
    lines: list[str] = []
    if fwi_row and fwi_row.get("fwi") is not None:
        lines.append(
            f"Fire Weather Index {fwi_row['fwi']:.1f} ({fwi_row.get('fwi_class')}, {class_note})."
        )
        if percentiles:
            for key, label in (("ffmc", "fine-fuel dryness (FFMC)"), ("isi", "spread potential (ISI)"), ("bui", "fuel build-up (BUI)")):
                p = percentiles.get(key)
                if p is not None and p >= 75:
                    lines.append(f"{label[0].upper() + label[1:]} is higher than in {p:.0f}% of cells in this region on the same day.")
                elif p is not None and p <= 25:
                    lines.append(f"{label[0].upper() + label[1:]} is lower than in {100 - p:.0f}% of cells in this region on the same day.")
    elif fwi_row is not None:
        lines.append("Fire Weather Index unavailable for this day (incomplete weather inputs or spin-up period).")
    if composite["status"] == "ok":
        ranked = sorted((f for f in composite["factors"] if f["contribution"] is not None), key=lambda f: -f["contribution"])
        top = ranked[:2]
        parts = [f"{f['label'].lower()} ({f['contribution']:.0f} pts)" for f in top]
        lines.append(f"Largest contributions to the experimental composite: {', '.join(parts)}.")
    else:
        names = [FACTOR_RULES[k][0].lower() for k in composite["missing"]]
        lines.append(f"Experimental composite not calculated: missing {', '.join(names)}.")
    for f in composite["factors"]:
        if f.get("note"):
            lines.append(f["note"] + ".")
    return lines


def percentile_rank(values: list[float], x: float) -> float:
    if not values:
        return 50.0
    below = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return round(100.0 * (below + 0.5 * equal) / len(values), 1)
