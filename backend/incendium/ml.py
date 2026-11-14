"""Module 1 (ML part): experimental detection-likelihood model, tested out of sample.

What it predicts
----------------
P(at least one VIIRS thermal anomaly in a 0.25° cell on a local day), from that
day's fire weather, recent rain, vegetation, terrain, the cell's fire history and
the day of the season. Detections include agricultural and other managed burning,
so this is a model of satellite-detected burning activity, NOT of wildfire ignition.

Protocol (fixed before looking at test results)
-----------------------------------------------
* Train on Karnataka, 1 Feb - 31 May 2025 (ERA5 weather, Sentinel-2, FIRMS SP).
* Test once on 1 Feb - 31 May 2026. No hyperparameter is tuned on 2026.
* Fire history = share of Feb-May days with a NOAA-20 detection in the cell over
  the two seasons BEFORE the target season (2025 -> 2023+2024, 2026 -> 2024+2025,
  live -> 2025+2026). NOAA-20 only, because it is the only sensor with a
  consistent archive over all those years.
* Models: logistic regression (standardised, median-imputed) and gradient-boosted
  trees (scikit-learn HistGradientBoostingClassifier, fixed settings below).
* Baselines on the same test set: FWI alone, and fire history alone (the spatial
  prior). The model is useful only if it beats both.
* The model shown live is the 2025-trained model that was tested, not a refit.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .config import BACKEND_DIR
from .db import iso, utcnow
from .validation import auc, cluster_bootstrap_auc, stratified_auc

log = logging.getLogger(__name__)

MODEL_DIR = BACKEND_DIR / "instance" / "models"
VALIDATION_CACHE = BACKEND_DIR / "instance" / "validation" / "cache"
MODEL_NAME = "detection-v1"
TRAIN = ("2025-02-01", "2025-05-31")
TEST = ("2026-02-01", "2026-05-31")
SEASON_MONTHS = (2, 3, 4, 5)
FEATURES = [
    "fwi", "ffmc", "dmc", "dc", "isi", "bui",
    "t_max", "rh_min", "ws_max", "rain24", "dry_days",
    "ndvi", "ndmi",
    "elev_mean", "slope_mean", "relief_std",
    "hist_rate", "doy",
]
FEATURE_LABELS = {
    "fwi": "Fire Weather Index", "ffmc": "Fine fuel moisture code", "dmc": "Duff moisture code", "dc": "Drought code",
    "isi": "Initial spread index", "bui": "Build-up index", "t_max": "Max temperature", "rh_min": "Min humidity",
    "ws_max": "Max wind speed", "rain24": "24 h rain", "dry_days": "Days since ≥ 5 mm rain", "ndvi": "NDVI", "ndmi": "NDMI",
    "elev_mean": "Mean elevation", "slope_mean": "Mean slope", "relief_std": "Terrain relief", "hist_rate": "Fire history (prior 2 seasons)",
    "doy": "Day of year",
}
HGB_PARAMS = dict(max_iter=250, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=80, l2_regularization=1.0, random_state=20250201)


# ---------------------------------------------------------------------------- fire history
def _history_path(region: str, year: int) -> Path:
    return VALIDATION_CACHE / f"history_{region}_{year}_noaa20.json"


def fetch_history(db, settings, region: str, year: int) -> dict:
    """NOAA-20 SP detections for Feb-May of `year` inside the region bbox (cached)."""
    from .geo import region_geometry
    from .providers import firms
    from .providers.http import make_client

    p = _history_path(region, year)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    # Reuse the full validation download when one exists for that season.
    full = VALIDATION_CACHE / f"{region}_{year}-02-01_{year}-05-31" / "detections.json"
    if full.exists():
        d = json.loads(full.read_text(encoding="utf-8"))
        dets = [{"lat": x["lat"], "lon": x["lon"], "acq": x["acq"]} for x in d["detections"] if x["source"] == "VIIRS_NOAA20_SP"]
        out = {"year": year, "source": "VIIRS_NOAA20_SP", "detections": dets, "retrieved_at": d.get("retrieved_at"), "from": "validation cache"}
    else:
        if not settings.firms_map_key:
            raise RuntimeError("FIRMS_MAP_KEY not configured")
        w, s, e, n = region_geometry(region).bounds
        bbox = (round(w - 0.05, 3), round(s - 0.05, 3), round(e + 0.05, 3), round(n + 0.05, 3))
        start, end = date(year, 1, 31), date(year, 6, 1)
        dets = []
        with make_client() as client:
            d0 = start
            while d0 <= end:
                span = min(5, (end - d0).days + 1)
                rows, _ = firms.fetch_area(client, settings.firms_map_key, "VIIRS_NOAA20_SP", bbox, span, d0)
                dets += [{"lat": x.latitude, "lon": x.longitude, "acq": iso(x.acq_datetime)} for x in rows]
                d0 += timedelta(days=span)
        out = {"year": year, "source": "VIIRS_NOAA20_SP", "detections": dets, "retrieved_at": iso(utcnow()), "from": "FIRMS area API"}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out), encoding="utf-8")
    return out


def history_rate(region: str, histories: list[dict], tz: str) -> dict[str, float]:
    """Share of Feb-May local days with ≥ 1 detection, averaged over the given seasons."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from .geo import build_grid, cell_for_point

    zone = ZoneInfo(tz)
    rates = {c.id: 0.0 for c in build_grid(region)}
    for h in histories:
        y = h["year"]
        n_days = (date(y, 5, 31) - date(y, 2, 1)).days + 1
        seen: set[tuple[str, str]] = set()
        for x in h["detections"]:
            local = datetime.fromisoformat(x["acq"].replace("Z", "+00:00")).astimezone(zone).date()
            if local.month not in SEASON_MONTHS or local.year != y:
                continue
            cid = cell_for_point(region, x["lon"], x["lat"])
            if cid:
                seen.add((cid, local.isoformat()))
        counts: dict[str, int] = {}
        for cid, _ in seen:
            counts[cid] = counts.get(cid, 0) + 1
        for cid in rates:
            rates[cid] += counts.get(cid, 0) / n_days / len(histories)
    return rates


# ---------------------------------------------------------------------------- features
def season_table(region: str, start: str, end: str) -> pd.DataFrame:
    p = VALIDATION_CACHE / f"{region}_{start}_{end}" / "cell_days.csv"
    if not p.exists():
        raise FileNotFoundError(f"run `python -m incendium validate --start {start} --end {end}` first ({p.name} missing)")
    df = pd.read_csv(p)
    missing = [c for c in ("t_max", "ndvi", "dmc") if c not in df.columns]
    if missing:
        raise ValueError(f"{p} predates the raw-feature columns {missing}; re-run the validation for that season")
    return df


def add_static(df: pd.DataFrame, terrain: dict[str, dict], hist: dict[str, float]) -> pd.DataFrame:
    df = df.copy()
    for k in ("elev_mean", "slope_mean", "relief_std"):
        df[k] = df["cell"].map(lambda c, k=k: (terrain.get(c) or {}).get(k, np.nan))
    df["hist_rate"] = df["cell"].map(lambda c: hist.get(c, np.nan))
    df["doy"] = pd.to_datetime(df["date"]).dt.dayofyear
    return df


# ---------------------------------------------------------------------------- models
def _models():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return {
        "logistic": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=2000, C=1.0)),
        "gbt": HistGradientBoostingClassifier(**HGB_PARAMS),
    }


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    out = []
    for i in range(bins):
        m = (p > edges[i]) & (p <= edges[i + 1])
        if m.sum() == 0:
            continue
        out.append({"bin": i + 1, "n": int(m.sum()), "mean_predicted": float(p[m].mean()), "observed_rate": float(y[m].mean())})
    return out


def train_and_test(db, settings, region: str = "karnataka") -> dict:
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import brier_score_loss, roc_auc_score

    from .geo import region_def
    from .terrain import cell_terrain_map

    tz = region_def(region).get("timezone", "UTC")
    terrain = cell_terrain_map(db)
    if not terrain:
        raise RuntimeError("terrain statistics missing; run `python -m incendium terrain` first")
    years = {y: fetch_history(db, settings, region, y) for y in (2023, 2024, 2025)}
    hist_train = history_rate(region, [years[2023], years[2024]], tz)
    hist_test = history_rate(region, [years[2024], years[2025]], tz)

    tr = add_static(season_table(region, *TRAIN), terrain, hist_train)
    te = add_static(season_table(region, *TEST), terrain, hist_test)
    Xtr, ytr = tr[FEATURES].to_numpy(float), tr["det_any"].astype(bool).to_numpy()
    Xte, yte = te[FEATURES].to_numpy(float), te["det_any"].astype(bool).to_numpy()
    clusters, months = te["cell"].to_numpy(), te["month"].to_numpy()
    rng = np.random.default_rng(20260201)

    def block(scores: np.ndarray) -> dict:
        lo, hi = cluster_bootstrap_auc(scores, yte, clusters, 200, rng)
        return {"auc": auc(scores, yte), "ci_low": lo, "ci_high": hi, "auc_within_month": stratified_auc(scores, yte, months)}

    results: dict = {"baselines": {
        "fwi": {"label": "FWI alone", **block(te["fwi"].to_numpy(float))},
        "hist_rate": {"label": "Fire history alone (spatial prior)", **block(te["hist_rate"].to_numpy(float))},
        "climatology": {"label": "Constant (2025 base rate)", "brier": float(brier_score_loss(yte, np.full(len(yte), ytr.mean())))},
    }, "models": {}}
    fitted = {}
    for name, model in _models().items():
        model.fit(Xtr, ytr)
        p = model.predict_proba(Xte)[:, 1]
        p_tr = model.predict_proba(Xtr)[:, 1]
        fitted[name] = model
        results["models"][name] = {
            **block(p), "train_auc": float(roc_auc_score(ytr, p_tr)), "brier": float(brier_score_loss(yte, p)),
            "brier_skill_vs_climatology": 1.0 - float(brier_score_loss(yte, p)) / results["baselines"]["climatology"]["brier"],
            "mean_predicted": float(p.mean()), "observed_rate": float(yte.mean()), "reliability": reliability(p, yte),
        }
    gbt = fitted["gbt"]
    imp = permutation_importance(gbt, Xte, yte, scoring="roc_auc", n_repeats=5, random_state=7, n_jobs=1)
    importance = sorted(
        [{"feature": f, "label": FEATURE_LABELS[f], "auc_drop": float(m), "std": float(s)} for f, m, s in zip(FEATURES, imp.importances_mean, imp.importances_std)],
        key=lambda x: -x["auc_drop"])
    # Same model without fire history: how much does the weather/vegetation part add on its own?
    from sklearn.ensemble import HistGradientBoostingClassifier

    no_hist = [f for f in FEATURES if f != "hist_rate"]
    m2 = HistGradientBoostingClassifier(**HGB_PARAMS).fit(tr[no_hist].to_numpy(float), ytr)
    results["models"]["gbt_without_history"] = {**block(m2.predict_proba(te[no_hist].to_numpy(float))[:, 1])}

    best = max(("logistic", "gbt"), key=lambda k: results["models"][k]["auc"] or 0)
    report = {
        "name": MODEL_NAME, "region": region, "generated_at": iso(utcnow()),
        "target": "≥ 1 VIIRS NOAA-20/21 detection (any confidence) in the 0.25° cell on the local day",
        "train": {"period": list(TRAIN), "cell_days": int(len(tr)), "positives": int(ytr.sum()), "base_rate": float(ytr.mean())},
        "test": {"period": list(TEST), "cell_days": int(len(te)), "positives": int(yte.sum()), "base_rate": float(yte.mean())},
        "features": [{"key": f, "label": FEATURE_LABELS[f]} for f in FEATURES],
        "hyperparameters": {"gbt": HGB_PARAMS, "logistic": {"C": 1.0, "imputation": "median", "scaling": "standard"}},
        "history": {str(y): {"detections": len(h["detections"]), "source": h["source"], "retrieved_at": h.get("retrieved_at")} for y, h in years.items()},
        **results,
        "permutation_importance_test": importance,
        "selected_for_display": best,
        "selection_rule": "Higher 2026 AUC of the two pre-specified models (reported transparently; this is the only choice made after seeing test data).",
        "limitations": [
            "Predicts satellite-detected burning (including agricultural and managed fires), not wildfire ignition.",
            "Trained on one season (2025) and tested on one season (2026) in one region; skill may differ in other years and places.",
            "Trained on February-May only; outside that window predictions are out of distribution and are flagged.",
            "The live model uses forecast-model weather, whereas training used ERA5 reanalysis.",
            "Cell-days are spatially and temporally correlated; intervals resample whole cells but do not remove temporal correlation.",
        ],
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    import joblib

    joblib.dump({"model": fitted[best], "features": FEATURES, "kind": best, "name": MODEL_NAME, "trained": list(TRAIN)},
                MODEL_DIR / f"{MODEL_NAME}.joblib")
    (MODEL_DIR / f"{MODEL_NAME}.json").write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------- live scoring
_cache: dict = {}


def load_model() -> dict | None:
    p = MODEL_DIR / f"{MODEL_NAME}.joblib"
    if not p.exists():
        return None
    mtime = p.stat().st_mtime
    if _cache.get("mtime") != mtime:
        import joblib

        _cache.update(joblib.load(p), mtime=mtime)
    return _cache


def load_report() -> dict | None:
    p = MODEL_DIR / f"{MODEL_NAME}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def live_history(db, settings, region: str, today: date) -> dict[str, float]:
    """History for live use: the two most recent complete Feb-May seasons."""
    from .geo import region_def

    last = today.year if today >= date(today.year, 6, 1) else today.year - 1
    hs = [fetch_history(db, settings, region, y) for y in (last - 1, last)]
    return history_rate(region, hs, region_def(region).get("timezone", "UTC"))


def score_rows(rows: list[dict], terrain: dict[str, dict], hist: dict[str, float]) -> list[float | None]:
    """Probability per row (dict with FEATURES except static ones, plus cell_id and date)."""
    m = load_model()
    if m is None or not rows:
        return [None] * len(rows)
    X = []
    for r in rows:
        t = terrain.get(r["cell_id"]) or {}
        feat = {**r, "elev_mean": t.get("elev_mean"), "slope_mean": t.get("slope_mean"), "relief_std": t.get("relief_std"),
                "hist_rate": hist.get(r["cell_id"]), "doy": date.fromisoformat(r["date"]).timetuple().tm_yday}
        X.append([np.nan if feat.get(f) is None else float(feat[f]) for f in m["features"]])
    p = m["model"].predict_proba(np.array(X, dtype=float))[:, 1]
    return [None if math.isnan(v) else round(float(v), 4) for v in p]


def in_training_season(d: str) -> bool:
    return date.fromisoformat(d).month in SEASON_MONTHS
