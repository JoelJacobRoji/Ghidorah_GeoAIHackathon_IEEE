"""Retrospective comparison of Incendium indicators with satellite fire detections.

What this is
------------
A *descriptive* association study: for every grid cell and local day in a past
period, it asks whether days with higher FWI / composite scores were also days
on which VIIRS detected a thermal anomaly in that cell.

What this is not
----------------
It is not a validation of predictive skill for wildfire ignition. VIIRS
thermal anomalies include agricultural and other non-wildfire burning, are
missed under cloud/smoke and between overpasses, and FWI is a fire-*weather*
index, not an ignition forecast. Season and geography confound the
association, so stratified results are reported next to pooled ones.

Data (all real, retrieved once and cached under instance/validation/cache):
  * weather    Open-Meteo archive API, ERA5 reanalysis (0.25°), hourly
  * detections NASA FIRMS standard-processing (SP) VIIRS NOAA-20 + NOAA-21
  * vegetation Copernicus Sentinel-2 L2A Statistical API, NDVI/NDMI, 10-day
"""
from __future__ import annotations

import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import fwi as fwi_mod
from .config import BACKEND_DIR, Settings
from .db import Database, iso, utcnow
from .geo import build_grid, cell_for_point, region_def, region_geometry
from .log import mask
from .providers import cdse, firms, openmeteo
from .providers.http import ProviderError, make_client
from .risk import FACTOR_RULES, compute_composite, days_since_rain, linear_score, select_vegetation
from .weather import derive_daily

log = logging.getLogger(__name__)

FACTORS = ["T", "H", "W", "D", "V"]


# ============================================================================ statistics
def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float | None, float | None]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return None, None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    """ROC AUC via the Mann-Whitney U statistic (ties get average ranks)."""
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=bool)
    ok = ~np.isnan(scores)
    scores, labels = scores[ok], labels[ok]
    n_pos, n_neg = int(labels.sum()), int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = pd.Series(scores).rank(method="average").to_numpy()
    u = ranks[labels].sum() - n_pos * (n_pos + 1) / 2
    return float(u / (n_pos * n_neg))


def cluster_bootstrap_auc(
    scores: np.ndarray, labels: np.ndarray, clusters: np.ndarray, n_boot: int, rng: np.random.Generator
) -> tuple[float | None, float | None]:
    """95 % interval for AUC resampling whole cells (keeps within-cell correlation)."""
    uniq = np.unique(clusters)
    idx_by = {c: np.flatnonzero(clusters == c) for c in uniq}
    vals = []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([idx_by[c] for c in pick])
        a = auc(scores[idx], labels[idx])
        if a is not None:
            vals.append(a)
    if len(vals) < max(10, n_boot // 5):
        return None, None
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def stratified_auc(scores: np.ndarray, labels: np.ndarray, strata: np.ndarray) -> float | None:
    """Weighted mean of within-stratum AUCs (weights = positive × negative pairs).

    Equals the probability that, *within the same stratum* (e.g. month), a
    detection cell-day scores higher than a non-detection cell-day.
    """
    num, den = 0.0, 0.0
    for s in np.unique(strata):
        m = strata == s
        a = auc(scores[m], labels[m])
        if a is None:
            continue
        w = float(labels[m].sum()) * float((~labels[m]).sum())
        num += a * w
        den += w
    return num / den if den else None


def class_table(classes: list[str | None], labels: np.ndarray, order: list[str]) -> list[dict]:
    base = float(labels.mean()) if len(labels) else 0.0
    out = []
    arr = np.array([c if c is not None else "No data" for c in classes], dtype=object)
    for name in order + ["No data"]:
        m = arr == name
        n = int(m.sum())
        if n == 0:
            continue
        k = int(labels[m].sum())
        lo, hi = wilson(k, n)
        rate = k / n
        out.append(
            {"class": name, "cell_days": n, "with_detection": k, "rate": rate, "ci_low": lo, "ci_high": hi,
             "lift": rate / base if base > 0 else None}
        )
    return out


def contingency(pred: np.ndarray, labels: np.ndarray) -> dict:
    pred = np.asarray(pred, dtype=bool)
    labels = np.asarray(labels, dtype=bool)
    a = int((pred & labels).sum())  # hits
    b = int((pred & ~labels).sum())  # false alarms
    c = int((~pred & labels).sum())  # misses
    d = int((~pred & ~labels).sum())  # correct negatives
    return {
        "hits": a, "false_alarms": b, "misses": c, "correct_negatives": d,
        "hit_rate": a / (a + c) if a + c else None,  # probability of detection
        "false_alarm_ratio": b / (a + b) if a + b else None,
        "false_alarm_rate": b / (b + d) if b + d else None,
        "frequency_bias": (a + b) / (a + c) if a + c else None,
        "flagged_share": (a + b) / max(a + b + c + d, 1),
    }


def composite_scores(factor_scores: np.ndarray, weights: np.ndarray) -> np.ndarray:
    w = weights / weights.sum()
    return factor_scores @ w


def weight_sensitivity(
    factor_scores: np.ndarray, labels: np.ndarray, default: np.ndarray, n_samples: int, rng: np.random.Generator
) -> dict:
    base_auc = auc(composite_scores(factor_scores, default), labels)
    samples = rng.dirichlet(np.ones(factor_scores.shape[1]), size=n_samples)
    aucs = np.array([auc(composite_scores(factor_scores, w), labels) for w in samples], dtype=float)
    best = samples[int(np.nanargmax(aucs))]
    oat = []
    for i, k in enumerate(FACTORS):
        for delta in (-0.10, 0.10):
            w = default.copy()
            w[i] = max(0.0, w[i] + delta)
            oat.append({"factor": k, "delta": delta, "auc": auc(composite_scores(factor_scores, w), labels)})
    return {
        "default_auc": base_auc,
        "random_weights": {
            "n": n_samples, "distribution": "Dirichlet(1,1,1,1,1)",
            "min": float(np.nanmin(aucs)), "p5": float(np.nanpercentile(aucs, 5)), "median": float(np.nanmedian(aucs)),
            "p95": float(np.nanpercentile(aucs, 95)), "max": float(np.nanmax(aucs)),
            "default_percentile": float((aucs < (base_auc or 0)).mean() * 100),
            "best_weights": dict(zip(FACTORS, [round(float(x), 3) for x in best])),
        },
        "one_at_a_time": oat,
        "caveat": "The best-performing random weights are reported for transparency only; selecting them on this same sample would overfit and is not done.",
    }


# ============================================================================ data acquisition
@dataclass
class ValidationConfig:
    region: str = "karnataka"
    start: str = "2026-02-01"
    end: str = "2026-05-31"
    spinup_days: int = 62
    firms_sources: tuple[str, ...] = ("VIIRS_NOAA20_SP", "VIIRS_NOAA21_SP")
    veg_res_deg: float = 0.004
    veg_interval_days: int = 10
    weather_batch: int = 10
    weather_per_minute_budget: float = 400.0
    weather_daily_budget: float = 9000.0
    bootstrap: int = 300
    weight_samples: int = 1000
    seed: int = 20260201
    composite_threshold: float = 50.0
    fwi_threshold: float = 21.3
    extra: dict = field(default_factory=dict)


class Validator:
    def __init__(self, db: Database, settings: Settings, cfg: ValidationConfig, out_dir: Path | None = None):
        self.db = db
        self.s = settings
        self.cfg = cfg
        self.out_dir = out_dir or (BACKEND_DIR / "instance" / "validation")
        self.cache = self.out_dir / "cache" / f"{cfg.region}_{cfg.start}_{cfg.end}"
        self.season = cfg.start[:4]
        self.calibration: dict | None = None
        self.calibrated = False
        self.cache.mkdir(parents=True, exist_ok=True)
        self.tz = region_def(cfg.region).get("timezone", "UTC")
        self.cells = list(build_grid(cfg.region))
        self.spin_start = (date.fromisoformat(cfg.start) - timedelta(days=cfg.spinup_days)).isoformat()

    # ---------------------------------------------------------------- cache helpers
    def _load(self, name: str):
        p = self.cache / name
        if p.exists():
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        return None

    def _save(self, name: str, obj) -> None:
        tmp = self.cache / (name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, separators=(",", ":"))
        tmp.replace(self.cache / name)

    # ---------------------------------------------------------------- weather (ERA5)
    def weather(self) -> dict:
        cached = self._load("weather_daily.json") or {"cells": {}, "retrieved_at": None, "model": "era5"}
        todo = [c for c in self.cells if c.id not in cached["cells"]]
        if not todo:
            return cached
        days = (date.fromisoformat(self.cfg.end) - date.fromisoformat(self.spin_start)).days + 1
        batches = [todo[i : i + self.cfg.weather_batch] for i in range(0, len(todo), self.cfg.weather_batch)]
        total = openmeteo.estimated_cost(len(todo), days)
        used = self.db.usage_since("open-meteo", utcnow() - timedelta(days=1))
        remaining = self.cfg.weather_daily_budget - used
        log.info("ERA5: %d cells in %d batches (~%.0f weighted calls; %.0f left in the rolling 24 h budget)", len(todo), len(batches), total, remaining)
        window: list[tuple[float, float]] = []
        far_future = datetime(2100, 1, 1)
        with make_client(timeout=120) as client:
            for i, batch in enumerate(batches, 1):
                cost = openmeteo.estimated_cost(len(batch), days)
                if cost > remaining:
                    log.warning(
                        "ERA5: Open-Meteo 24 h budget reached after %d/%d batches; progress is cached, rerun later to finish",
                        i - 1, len(batches),
                    )
                    break
                remaining -= cost
                now = time.monotonic()
                window = [(t, c) for t, c in window if now - t < 60]
                if window and sum(c for _, c in window) + cost > self.cfg.weather_per_minute_budget:
                    wait = 61 - (now - window[0][0])
                    log.info("ERA5 pacing %.0fs (batch %d/%d)", wait, i, len(batches))
                    time.sleep(max(wait, 0))
                    window = []
                series = openmeteo.fetch_archive(client, [(c.lat, c.lon) for c in batch], self.spin_start, self.cfg.end, self.tz)
                window.append((time.monotonic(), cost))
                self.db.record_usage("open-meteo", cost)
                for c, s in zip(batch, series):
                    daily = derive_daily(s.time_local, s.values, far_future)
                    cached["cells"][c.id] = {
                        "model_point": [s.model_lat, s.model_lon, s.elevation],
                        "days": [{k: d[k] for k in ("date", "t_noon", "rh_noon", "ws_noon", "rain24_noon", "t_max", "rh_min", "ws_max")} for d in daily],
                    }
                cached["retrieved_at"] = iso(utcnow())
                self._save("weather_daily.json", cached)  # checkpoint every batch
        return cached

    def weather_complete(self, wx: dict) -> bool:
        return all(c.id in wx["cells"] for c in self.cells)

    # ---------------------------------------------------------------- detections (FIRMS SP)
    def detections(self) -> dict:
        cached = self._load("detections.json")
        if cached is not None:
            return cached
        if not self.s.firms_map_key:
            raise RuntimeError("FIRMS_MAP_KEY not configured")
        w, s, e, n = region_geometry(self.cfg.region).bounds
        bbox = (round(w - 0.05, 3), round(s - 0.05, 3), round(e + 0.05, 3), round(n + 0.05, 3))
        start, end = date.fromisoformat(self.cfg.start), date.fromisoformat(self.cfg.end)
        # Also fetch 1 day either side: local (IST) dates straddle UTC midnight.
        rows: dict[str, dict] = {}
        requests = 0
        with make_client() as client:
            for source in self.cfg.firms_sources:
                d = start - timedelta(days=1)
                while d <= end + timedelta(days=1):
                    span = min(5, (end + timedelta(days=1) - d).days + 1)
                    dets, _ = firms.fetch_area(client, self.s.firms_map_key, source, bbox, span, d)
                    requests += 1
                    for x in dets:
                        rows[x.id] = {
                            "id": x.id, "source": source, "satellite": x.satellite, "lat": x.latitude, "lon": x.longitude,
                            "acq": iso(x.acq_datetime), "confidence": x.confidence, "frp": x.frp, "daynight": x.daynight,
                        }
                    d += timedelta(days=span)
        out = {"detections": list(rows.values()), "bbox": bbox, "requests": requests, "retrieved_at": iso(utcnow()), "sources": list(self.cfg.firms_sources)}
        self._save("detections.json", out)
        return out

    # ---------------------------------------------------------------- vegetation (Sentinel-2)
    def vegetation(self) -> dict:
        cached = self._load("vegetation.json") or {"cells": {}, "retrieved_at": None}
        todo = [c for c in self.cells if c.id not in cached["cells"]]
        if not todo:
            return cached
        if not (self.s.cdse_client_id and self.s.cdse_client_secret):
            raise RuntimeError("CDSE credentials not configured")
        tokens = cdse.TokenProvider(self.s.cdse_client_id, self.s.cdse_client_secret)
        limiter = cdse.RateLimiter(per_minute=60)
        v_start = datetime.fromisoformat(self.cfg.start).replace(tzinfo=timezone.utc) - timedelta(days=31)
        v_end = datetime.fromisoformat(self.cfg.end).replace(tzinfo=timezone.utc)
        errors: list[str] = []

        def work(c):
            limiter.wait()
            poly = [[c.west, c.south], [c.east, c.south], [c.east, c.north], [c.west, c.north], [c.west, c.south]]
            with make_client(timeout=120) as client:
                return c, cdse.fetch_cell_stats(client, tokens, poly, v_start, v_end, self.cfg.veg_interval_days, self.cfg.veg_res_deg)

        done = 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(work, c) for c in todo]
            for f in as_completed(futs):
                try:
                    c, intervals = f.result()
                except ProviderError as exc:
                    errors.append(mask(str(exc)))
                    continue
                cached["cells"][c.id] = [asdict(iv) for iv in intervals]
                done += 1
                if done % 20 == 0:
                    cached["retrieved_at"] = iso(utcnow())
                    self._save("vegetation.json", cached)
        cached["retrieved_at"] = iso(utcnow())
        self._save("vegetation.json", cached)
        if errors:
            log.warning("vegetation: %d cells failed (%s) — rerun to fill gaps", len(errors), sorted(set(errors))[:2])
        return cached

    # ================================================================ analysis
    def build_table(self, wx: dict, det: dict, veg: dict) -> pd.DataFrame:
        tz = ZoneInfo(self.tz)
        start, end = self.cfg.start, self.cfg.end
        # detections -> (cell, local date)
        events_any: set[tuple[str, str]] = set()
        events_nh: set[tuple[str, str]] = set()
        for x in det["detections"]:
            cid = cell_for_point(self.cfg.region, x["lon"], x["lat"])
            if not cid:
                continue
            local = datetime.fromisoformat(x["acq"].replace("Z", "+00:00")).astimezone(tz).date().isoformat()
            if not (start <= local <= end):
                continue
            events_any.add((cid, local))
            if x["confidence"] in ("nominal", "high"):
                events_nh.add((cid, local))

        weights = self.s.composite_weights
        classes, self.calibration = fwi_mod.class_scheme(self.cfg.region)
        self.calibrated = self.calibration is not None
        rows = []
        for c in self.cells:
            cw = wx["cells"].get(c.id)
            if not cw:
                continue
            days = list(cw["days"])
            # Leading day(s) lack the previous afternoon, so their 24 h rain is unknown.
            while days and days[0]["rain24_noon"] is None:
                days = days[1:]
            by_date = {d["date"]: d for d in days}
            if not days:
                continue
            d0, d1 = date.fromisoformat(days[0]["date"]), date.fromisoformat(days[-1]["date"])
            inputs, cur = [], d0
            while cur <= d1:
                r = by_date.get(cur.isoformat())
                inputs.append(
                    fwi_mod.DailyInput(cur.isoformat(), r["t_noon"], r["rh_noon"], r["ws_noon"], r["rain24_noon"])
                    if r else fwi_mod.DailyInput(cur.isoformat(), None, None, None, None)
                )
                cur += timedelta(days=1)
            spin = max(0, (date.fromisoformat(start) - d0).days)
            res = {x.date: x for x in fwi_mod.run_series(inputs, c.lat, spinup_days=spin)}
            rain = {d["date"]: d["rain24_noon"] for d in days}
            veg_rows = [
                {**v, "valid_fraction": v["valid_fraction"], "ndmi_mean": v["ndmi_mean"]} for v in veg["cells"].get(c.id, [])
            ]
            cur = date.fromisoformat(start)
            while cur <= date.fromisoformat(end):
                ds = cur.isoformat()
                f = res.get(ds)
                w = by_date.get(ds)
                v = select_vegetation(veg_rows, ds)
                ok = f is not None and f.status == "ok" and not f.spinup
                dry = days_since_rain(rain, ds)
                comp = compute_composite(
                    weights,
                    w["t_max"] if w else None, w["rh_min"] if w else None, w["ws_max"] if w else None,
                    dry, v["ndmi_mean"] if v else None, v["ndvi_mean"] if v else None,
                )
                fs = {fct["key"]: fct["score"] for fct in comp["factors"]}
                # v0.1 composite (Dryness = Drought Code, 0 at 0, 100 at 600), kept for comparison.
                v01 = np.nan
                if comp["score"] is not None and ok:
                    v01 = comp["score"] + weights["D"] * (linear_score(f.dc, 0.0, 600.0) - fs["D"])
                rows.append(
                    {
                        "cell": c.id, "date": ds, "month": ds[:7],
                        "fwi": f.fwi if ok else np.nan, "fwi_class": fwi_mod.fwi_class(f.fwi) if ok else None,
                        "fwi_class_cal": fwi_mod.fwi_class(f.fwi, classes) if ok and self.calibrated else None,
                        "dry_days": dry if dry is not None else np.nan, "composite_v01": v01,
                        "dmc": f.dmc if ok else np.nan,
                        # raw inputs (used by the ML model in ml.py)
                        "t_max": w["t_max"] if w and w["t_max"] is not None else np.nan,
                        "rh_min": w["rh_min"] if w and w["rh_min"] is not None else np.nan,
                        "ws_max": w["ws_max"] if w and w["ws_max"] is not None else np.nan,
                        "rain24": w["rain24_noon"] if w and w["rain24_noon"] is not None else np.nan,
                        "ndvi": v["ndvi_mean"] if v and v.get("ndvi_mean") is not None else np.nan,
                        "ndmi": v["ndmi_mean"] if v and v.get("ndmi_mean") is not None else np.nan,
                        "ffmc": f.ffmc if ok else np.nan, "isi": f.isi if ok else np.nan, "bui": f.bui if ok else np.nan, "dc": f.dc if ok else np.nan,
                        "composite": comp["score"] if comp["score"] is not None else np.nan, "composite_class": comp["class"],
                        **{f"s_{k}": (fs[k] if fs[k] is not None else np.nan) for k in FACTORS},
                        "det_any": (c.id, ds) in events_any, "det_nh": (c.id, ds) in events_nh,
                    }
                )
                cur += timedelta(days=1)
        return pd.DataFrame(rows)

    def analyse(self, df: pd.DataFrame, wx: dict, det: dict, veg: dict) -> dict:
        rng = np.random.default_rng(self.cfg.seed)
        y = df["det_any"].to_numpy(bool)
        y_nh = df["det_nh"].to_numpy(bool)
        clusters = df["cell"].to_numpy()
        months = df["month"].to_numpy()

        def score_block(col: str, label: np.ndarray) -> dict:
            s = df[col].to_numpy(float)
            lo, hi = cluster_bootstrap_auc(s, label, clusters, self.cfg.bootstrap, rng)
            return {"auc": auc(s, label), "ci_low": lo, "ci_high": hi, "auc_within_month": stratified_auc(s, label, months), "n": int((~np.isnan(s)).sum())}

        from .risk import COMPOSITE_CLASSES

        indicators = {
            "fwi": "Fire Weather Index", "ffmc": "FFMC (fine fuel dryness)", "isi": "ISI (spread)", "bui": "BUI (build-up)",
            "dc": "Drought Code", "dry_days": "Days since significant rain (≥ 5 mm)",
            "composite": "Experimental composite (v0.2, Dryness = days since rain)",
            "composite_v01": "Experimental composite (v0.1, Dryness = Drought Code)",
            **{f"s_{k}": f"Composite factor {k}: {FACTOR_RULES[k][0]}" for k in FACTORS},
        }
        auc_any = {k: {"label": v, **score_block(k, y)} for k, v in indicators.items()}
        auc_nh = {k: {"label": v, **score_block(k, y_nh)} for k, v in indicators.items() if k in ("fwi", "composite", "composite_v01")}

        monthly = []
        for m in sorted(df["month"].unique()):
            mm = df["month"] == m
            k, n = int(df.loc[mm, "det_any"].sum()), int(mm.sum())
            lo, hi = wilson(k, n)
            monthly.append(
                {"month": m, "cell_days": n, "with_detection": k, "rate": k / n, "ci_low": lo, "ci_high": hi,
                 "mean_fwi": float(np.nanmean(df.loc[mm, "fwi"])), "mean_composite": float(np.nanmean(df.loc[mm, "composite"]))}
            )

        comp_ok = df.dropna(subset=[f"s_{k}" for k in FACTORS])
        sens = weight_sensitivity(
            comp_ok[[f"s_{k}" for k in FACTORS]].to_numpy(float), comp_ok["det_any"].to_numpy(bool),
            np.array([self.s.composite_weights[k] for k in FACTORS]), self.cfg.weight_samples, rng,
        )

        calibrated = None
        if self.calibration:
            cal = self.calibration
            src = cal["source"]
            in_sample = src["start"] <= self.cfg.end and self.cfg.start <= src["end"]
            th = cal["thresholds"]
            high = th[1]  # lower bound of calibrated "High"
            rows_cal = class_table(df["fwi_class_cal"].tolist(), y, list(fwi_mod.CLASS_NAMES))
            n_cal = max(int(df["fwi_class_cal"].notna().sum()), 1)
            calibrated = {
                "thresholds": th, "percentiles": cal["percentiles"], "source": src, "method": cal["method"],
                "in_sample": in_sample,
                "status": ("IN-SAMPLE: these thresholds were derived from this season's FWI values (detections were not used)."
                           if in_sample else "OUT-OF-SAMPLE: thresholds were derived from a different season; this is an independent test."),
                "classes": rows_cal,
                "share_by_class": {n: float((df["fwi_class_cal"] == n).sum() / n_cal) for n in fwi_mod.CLASS_NAMES},
                "contingency": {"threshold": f"FWI ≥ {high:g} (calibrated 'High' or above)", **contingency(df["fwi"].to_numpy(float) >= high, y)},
                "monotonic": _monotonic_rates(rows_cal),
            }

        fwi_ok = ~np.isnan(df["fwi"].to_numpy(float))
        comp_mask = ~np.isnan(df["composite"].to_numpy(float))
        cells_with_det = df.groupby("cell")["det_any"].any()
        return {
            "meta": {
                "region": self.cfg.region, "start": self.cfg.start, "end": self.cfg.end, "generated_at": iso(utcnow()),
                "cells": len(self.cells), "cell_days": int(len(df)),
                "cell_days_with_fwi": int(fwi_ok.sum()), "cell_days_with_composite": int(comp_mask.sum()),
                "cell_days_with_detection": int(y.sum()), "cell_days_with_nominal_or_high_detection": int(y_nh.sum()),
                "base_rate": float(y.mean()), "cells_with_any_detection": int(cells_with_det.sum()),
                "detections_in_grid_period": int(sum(1 for x in det["detections"] if cell_for_point(self.cfg.region, x["lon"], x["lat"]))),
                "detections_downloaded": len(det["detections"]),
                "season": self.season, "composite_version": "0.2",
                "fwi_spinup_days": self.cfg.spinup_days, "weights": self.s.composite_weights,
                "bootstrap_resamples": self.cfg.bootstrap, "seed": self.cfg.seed,
            },
            "data": {
                "weather": {"provider": "Open-Meteo archive API", "model": "ERA5 reanalysis (0.25°)", "period": [self.spin_start, self.cfg.end], "retrieved_at": wx.get("retrieved_at"), "note": "Cell centres (x.125°) are served from the nearest ERA5 grid node."},
                "detections": {"provider": "NASA FIRMS", "products": det.get("sources"), "bbox": det.get("bbox"), "requests": det.get("requests"), "retrieved_at": det.get("retrieved_at"), "note": "Standard-processing (science-quality) archive; the live app uses near-real-time products."},
                "vegetation": {"provider": "Copernicus Sentinel-2 L2A (Statistical API)", "interval_days": self.cfg.veg_interval_days, "sampling_deg": self.cfg.veg_res_deg, "cells": len(veg["cells"]), "retrieved_at": veg.get("retrieved_at")},
            },
            "definitions": {
                "unit": "One grid cell (0.25°) on one local (IST) calendar day.",
                "event": "At least one VIIRS NOAA-20/21 detection (any confidence) whose pixel centre falls in the cell during that local day.",
                "auc": "Probability that a randomly chosen event cell-day has a higher indicator value than a randomly chosen non-event cell-day (0.5 = no association).",
                "auc_ci": f"95 % interval from {self.cfg.bootstrap} bootstrap resamples of whole cells.",
                "auc_within_month": "AUC computed separately within each calendar month and averaged (weighted by pair counts); removes seasonal co-variation between fire weather and burning.",
            },
            "fwi_classes": class_table(df["fwi_class"].tolist(), y, [n for n, _, _ in fwi_mod.FWI_CLASSES]),
            "fwi_classes_calibrated": calibrated,
            "composite_classes": class_table(df["composite_class"].tolist(), y, [n for n, _, _ in COMPOSITE_CLASSES]),
            "auc": auc_any,
            "auc_nominal_high": auc_nh,
            "monthly": monthly,
            "contingency": {
                "fwi": {"threshold": f"FWI ≥ {self.cfg.fwi_threshold} (prototype 'High' or above)", **contingency(df["fwi"].to_numpy(float) >= self.cfg.fwi_threshold, y)},
                "composite_v01": {
                    "threshold": f"Composite v0.1 ≥ {self.cfg.composite_threshold} (Dryness = Drought Code)",
                    **contingency(comp_ok["composite_v01"].to_numpy(float) >= self.cfg.composite_threshold, comp_ok["det_any"].to_numpy(bool)),
                    "n": int(len(comp_ok)),
                },
                "composite": {
                    "threshold": f"Composite ≥ {self.cfg.composite_threshold} (prototype 'High' or above)",
                    **contingency(comp_ok["composite"].to_numpy(float) >= self.cfg.composite_threshold, comp_ok["det_any"].to_numpy(bool)),
                    "n": int(len(comp_ok)),
                },
            },
            "weight_sensitivity": sens,
            "limitations": [
                "Detections are thermal anomalies, including agricultural and other managed burning; they are not a wildfire inventory.",
                "Undetected fires (cloud, smoke, small/cool fires, between overpasses) count as non-events.",
                "FWI describes fire-weather conditions; it does not model ignition sources, which dominate where and when burning occurs.",
                "Cell-days are spatially and temporally correlated; bootstrap intervals resample whole cells but do not remove temporal correlation.",
                "Weather is ERA5 reanalysis here, but the live app uses forecast-model analysis; results transfer only approximately.",
                "Composite weights were not tuned on this data. Exploring weights on the same sample would overfit.",
                "The v0.2 Dryness factor (days since ≥ 5 mm rain, saturating at 30 days) replaced the Drought Code after the 2026 comparison showed the Drought Code inversely associated with detections. Its parameters were fixed a priori, but the decision to replace it was informed by 2026 data, so 2026 is not an independent test of the change; other seasons are.",
                "One season in one region; results may not generalise to other years or regions.",
            ],
        }

    def run(self) -> dict:
        log.info("validation %s %s..%s: detections", self.cfg.region, self.cfg.start, self.cfg.end)
        det = self.detections()
        log.info("validation: weather (ERA5)")
        wx = self.weather()
        if not self.weather_complete(wx):
            n = sum(1 for c in self.cells if c.id in wx["cells"])
            raise IncompleteData(f"ERA5 weather cached for {n}/{len(self.cells)} cells; rerun after the Open-Meteo budget recovers")
        log.info("validation: vegetation (Sentinel-2)")
        veg = self.vegetation()
        df = self.build_table(wx, det, veg)
        report = self.analyse(df, wx, det, veg)
        path = report_path(self.cfg.region, self.season, self.out_dir)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, default=float)
        df.to_csv(self.cache / "cell_days.csv", index=False)
        log.info("validation report written: %s", path)
        return report


class IncompleteData(RuntimeError):
    """Raised when a required input could not be fully retrieved yet (progress is cached)."""


def _monotonic_rates(rows: list[dict], min_n: int = 200) -> bool:
    """True if detection rate never falls from one class to the next (classes with < min_n cell-days ignored)."""
    rates = [r["rate"] for r in rows if r["class"] != "No data" and r["cell_days"] >= min_n]
    return all(b >= a for a, b in zip(rates, rates[1:]))


def report_path(region: str, season: str, out_dir: Path | None = None) -> Path:
    return (out_dir or BACKEND_DIR / "instance" / "validation") / f"report-{region}-{season}.json"


def available_seasons(region: str) -> list[str]:
    d = BACKEND_DIR / "instance" / "validation"
    pre = f"report-{region}-"
    return sorted(p.stem[len(pre):] for p in d.glob(f"{pre}*.json")) if d.exists() else []


def load_report(region: str, season: str | None = None) -> dict | None:
    seasons = available_seasons(region)
    if not seasons:
        return None
    if season is None:
        season = seasons[-1]
    elif season not in seasons:
        return None
    with open(report_path(region, season), encoding="utf-8") as fh:
        rep = json.load(fh)
    rep.setdefault("meta", {})["available_seasons"] = seasons
    return rep


# ============================================================================ markdown
def _f(x, nd=3, pct=False):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x * 100:.{nd}f} %" if pct else f"{x:.{nd}f}"


def to_markdown(r: dict) -> str:
    m = r["meta"]
    L = [
        f"# Incendium retrospective comparison: {m['region'].title()}, {m['start']} → {m['end']}",
        "",
        f"_Generated {m['generated_at']} by `python -m incendium validate`. All numbers below are computed from real archived data; nothing is tuned to this sample._",
        "",
        "> **This is a descriptive association study, not a validation of predictive skill.** VIIRS thermal anomalies include agricultural burning, and FWI describes fire weather, not ignitions.",
        "",
        "## Data",
        "",
        f"- Weather: {r['data']['weather']['provider']}, {r['data']['weather']['model']}, {r['data']['weather']['period'][0]} → {r['data']['weather']['period'][1]} ({m['fwi_spinup_days']}-day FWI spin-up before the study period). {r['data']['weather']['note']}",
        f"- Detections: {r['data']['detections']['provider']} {', '.join(r['data']['detections']['products'])}. {r['data']['detections']['note']}",
        f"- Vegetation: {r['data']['vegetation']['provider']}, {r['data']['vegetation']['interval_days']}-day intervals, {r['data']['vegetation']['sampling_deg']}° sampling, {r['data']['vegetation']['cells']} cells.",
        f"- {m['cells']} cells × days = **{m['cell_days']:,} cell-days**; FWI available for {m['cell_days_with_fwi']:,}, composite for {m['cell_days_with_composite']:,}.",
        f"- **{m['cell_days_with_detection']:,} cell-days had ≥ 1 detection** (base rate {_f(m['base_rate'], 2, True)}); {m['cells_with_any_detection']} of {m['cells']} cells had at least one. {m['detections_in_grid_period']:,} detections fell inside the grid.",
        "",
        "## Detection rate by FWI class (prototype EFFIS thresholds)",
        "",
        "| Class | Cell-days | With detection | Rate | 95 % CI | Lift vs. base |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for c in r["fwi_classes"]:
        L.append(f"| {c['class']} | {c['cell_days']:,} | {c['with_detection']:,} | {_f(c['rate'], 2, True)} | {_f(c['ci_low'], 2, True)} – {_f(c['ci_high'], 2, True)} | {_f(c['lift'], 2)} |")
    cal = r.get("fwi_classes_calibrated")
    if cal:
        th = cal["thresholds"]
        L += [
            "",
            f"## Detection rate by Karnataka-calibrated FWI class (percentiles {', '.join(f'P{p:g}' for p in cal['percentiles'])} of season {cal['source']['start'][:4]})",
            "",
            f"**{cal['status']}** Thresholds: {', '.join(f'{t:g}' for t in th)}. Detection rate rises with every class (classes with ≥ 200 cell-days): **{'yes' if cal['monotonic'] else 'no'}**.",
            "",
            "| Class | Cell-days | Share of cell-days | With detection | Rate | 95 % CI | Lift vs. base |",
            "|---|---:|---:|---:|---:|---|---:|",
        ]
        for c in cal["classes"]:
            share = cal["share_by_class"].get(c["class"])
            L.append(f"| {c['class']} | {c['cell_days']:,} | {_f(share, 1, True)} | {c['with_detection']:,} | {_f(c['rate'], 2, True)} | {_f(c['ci_low'], 2, True)} – {_f(c['ci_high'], 2, True)} | {_f(c['lift'], 2)} |")
    L += ["", "## Detection rate by experimental composite class", "", "| Class | Cell-days | With detection | Rate | 95 % CI | Lift vs. base |", "|---|---:|---:|---:|---|---:|"]
    for c in r["composite_classes"]:
        L.append(f"| {c['class']} | {c['cell_days']:,} | {c['with_detection']:,} | {_f(c['rate'], 2, True)} | {_f(c['ci_low'], 2, True)} – {_f(c['ci_high'], 2, True)} | {_f(c['lift'], 2)} |")
    L += [
        "",
        "## Discrimination (AUC)",
        "",
        r["definitions"]["auc"] + " " + r["definitions"]["auc_ci"] + " " + r["definitions"]["auc_within_month"],
        "",
        "| Indicator | AUC (pooled) | 95 % CI | AUC within month |",
        "|---|---:|---|---:|",
    ]
    for k, a in r["auc"].items():
        L.append(f"| {a['label']} | {_f(a['auc'])} | {_f(a['ci_low'])} – {_f(a['ci_high'])} | {_f(a['auc_within_month'])} |")
    L += ["", "Restricting events to nominal/high-confidence detections:", ""]
    for k, a in r["auc_nominal_high"].items():
        L.append(f"- {a['label']}: AUC {_f(a['auc'])} ({_f(a['ci_low'])} – {_f(a['ci_high'])}), within month {_f(a['auc_within_month'])}")
    L += ["", "## Monthly context", "", "| Month | Cell-days | With detection | Rate | Mean FWI | Mean composite |", "|---|---:|---:|---:|---:|---:|"]
    for mo in r["monthly"]:
        L.append(f"| {mo['month']} | {mo['cell_days']:,} | {mo['with_detection']:,} | {_f(mo['rate'], 2, True)} | {_f(mo['mean_fwi'], 1)} | {_f(mo['mean_composite'], 1)} |")
    L += ["", "## Hits, misses and false alarms at the prototype 'High' threshold", "", "| Rule | Hits | Misses | False alarms | Correct negatives | Hit rate | False-alarm ratio | Share of cell-days flagged |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    rows_ct = list(r["contingency"].values()) + ([cal["contingency"]] if cal else [])
    for c in rows_ct:
        L.append(f"| {c['threshold']} | {c['hits']:,} | {c['misses']:,} | {c['false_alarms']:,} | {c['correct_negatives']:,} | {_f(c['hit_rate'], 1, True)} | {_f(c['false_alarm_ratio'], 1, True)} | {_f(c['flagged_share'], 1, True)} |")
    s = r["weight_sensitivity"]
    rw = s["random_weights"]
    L += [
        "",
        "## Composite weight sensitivity",
        "",
        f"- Default weights {m['weights']}: AUC {_f(s['default_auc'])}.",
        f"- {rw['n']} random weight vectors ({rw['distribution']}): AUC min {_f(rw['min'])}, 5th pct {_f(rw['p5'])}, median {_f(rw['median'])}, 95th pct {_f(rw['p95'])}, max {_f(rw['max'])}. Default weights rank at the {rw['default_percentile']:.0f}th percentile.",
        "- One-at-a-time ±0.10 changes (weights renormalised):",
        "",
        "| Factor | −0.10 | +0.10 |",
        "|---|---:|---:|",
    ]
    for k in FACTORS:
        lo = next(o["auc"] for o in s["one_at_a_time"] if o["factor"] == k and o["delta"] < 0)
        hi = next(o["auc"] for o in s["one_at_a_time"] if o["factor"] == k and o["delta"] > 0)
        L.append(f"| {k} ({FACTOR_RULES[k][0]}) | {_f(lo)} | {_f(hi)} |")
    L += ["", f"_{s['caveat']}_", "", "## Limitations", ""] + [f"- {x}" for x in r["limitations"]] + [""]
    return "\n".join(L)
