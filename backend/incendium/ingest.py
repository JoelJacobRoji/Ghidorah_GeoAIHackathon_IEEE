"""Ingestion and derived-product jobs.

Each job records a row in ``provider_runs`` so the API can report freshness,
failures and partial results honestly. Jobs never write fabricated values:
anything that cannot be retrieved is simply absent and reported as such.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import fwi as fwi_mod
from .config import Settings, load_region_config
from .db import Database, iso, utcnow
from .geo import build_grid, cell_for_point, region_def, state_for_point
from .log import mask
from .providers import cdse, firms, openmeteo
from .providers.http import ProviderError, make_client
from .risk import EFFIS_NOTE, compute_composite, days_since_rain, explain, percentile_rank, select_vegetation

log = logging.getLogger(__name__)

DETECTION_RETENTION_DAYS = 30
WEATHER_DAILY_RETENTION_DAYS = 130
FWI_SPINUP_DAYS = 45
RISK_HISTORY_DAYS = 30
OPEN_METEO_BATCH = 25
OPEN_METEO_PER_MINUTE_BUDGET = 450.0  # weighted calls; free limit is 600/min
OPEN_METEO_DAILY_BUDGET = 9000.0  # free limit is 10 000/day


class Ingestor:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.s = settings
        self._cdse_tokens: cdse.TokenProvider | None = None

    # ------------------------------------------------------------------ cells
    def sync_cells(self) -> int:
        n = 0
        with self.db.connect() as con:
            for region in self.s.risk_regions:
                for c in build_grid(region):
                    con.execute(
                        "INSERT OR REPLACE INTO cells(id, region, lat, lon, west, south, east, north, overlap) VALUES (?,?,?,?,?,?,?,?,?)",
                        (c.id, c.region, c.lat, c.lon, c.west, c.south, c.east, c.north, c.overlap),
                    )
                    n += 1
        return n

    def cells(self, region: str) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM cells WHERE region=? ORDER BY lat, lon", (region,))]

    # ------------------------------------------------------------------ FIRMS
    def ingest_firms(self, backfill_days: int | None = None) -> dict:
        run = self.db.start_run("firms", "backfill" if backfill_days else "refresh")
        if not self.s.firms_map_key:
            self.db.finish_run(run, "error", message="FIRMS_MAP_KEY not configured")
            return {"status": "error", "message": "FIRMS_MAP_KEY not configured"}
        window = load_region_config()["detection_window"]
        bbox = tuple(window["bbox"])
        retrieved = iso(utcnow())
        total_new, total_rows, rejected, errors = 0, 0, 0, []
        requests: list[tuple[str, int, date | None]] = []
        for source in self.s.firms_sources:
            if backfill_days:
                today = utcnow().date()
                start = today - timedelta(days=backfill_days - 1)
                d = start
                while d <= today:
                    span = min(5, (today - d).days + 1)
                    requests.append((source, span, d))
                    d += timedelta(days=span)
            else:
                requests.append((source, 2, None))
        with make_client() as client:
            for source, span, start in requests:
                try:
                    dets, rej = firms.fetch_area(client, self.s.firms_map_key, source, bbox, span, start)  # type: ignore[arg-type]
                except ProviderError as exc:
                    errors.append(str(exc))
                    log.warning("FIRMS %s failed: %s", source, exc)
                    continue
                rejected += rej
                total_rows += len(dets)
                total_new += self._store_detections(dets, retrieved)
        cutoff = iso(utcnow() - timedelta(days=DETECTION_RETENTION_DAYS))
        with self.db.connect() as con:
            con.execute("DELETE FROM detections WHERE acq_datetime < ?", (cutoff,))
        status = "ok" if not errors else ("partial" if total_rows else "error")
        details = {"requests": len(requests), "rows": total_rows, "new": total_new, "rejected_rows": rejected, "errors": errors}
        self.db.finish_run(run, status, total_new, "; ".join(errors)[:500] or None, details)
        return {"status": status, **details}

    def _store_detections(self, dets: list[firms.Detection], retrieved: str) -> int:
        regions = list(self.s.risk_regions)
        rows = []
        for d in dets:
            state = state_for_point(d.longitude, d.latitude)
            cell = None
            for r in regions:
                cell = cell_for_point(r, d.longitude, d.latitude)
                if cell:
                    break
            rows.append(
                (
                    d.id, d.source, d.satellite, d.instrument, d.latitude, d.longitude, iso(d.acq_datetime), d.confidence,
                    d.frp, d.bright_ti4, d.bright_ti5, d.scan, d.track, d.daynight, d.version, state, cell, retrieved,
                )
            )
        with self.db.connect() as con:
            before = con.total_changes
            con.executemany(
                """INSERT OR IGNORE INTO detections(id, source, satellite, instrument, latitude, longitude, acq_datetime,
                   confidence, frp, bright_ti4, bright_ti5, scan, track, daynight, version, state, cell_id, retrieved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            return con.total_changes - before

    # ------------------------------------------------------------------ Weather
    def _needs_warmup(self, region: str) -> bool:
        row = self.db.one(
            """SELECT COUNT(DISTINCT w.date) AS n, MAX(w.date) AS last FROM weather_daily w
               JOIN cells c ON c.id=w.cell_id WHERE c.region=? AND w.is_forecast=0""",
            (region,),
        )
        if not row or not row["n"] or row["n"] < FWI_SPINUP_DAYS + 5:
            return True
        last = date.fromisoformat(row["last"])
        return (utcnow().date() - last).days > 5

    def ingest_weather(self, region: str, force_warmup: bool = False) -> dict:
        run = self.db.start_run("open-meteo", f"weather:{region}")
        rdef = region_def(region)
        tz = rdef.get("timezone", "UTC")
        cells = self.cells(region)
        if not cells:
            self.db.finish_run(run, "error", message="no grid cells for region")
            return {"status": "error", "message": "no grid cells"}
        warmup = force_warmup or self._needs_warmup(region)
        past_days = 92 if warmup else 4
        forecast_days = 3
        batch_cost = openmeteo.estimated_cost(OPEN_METEO_BATCH, past_days + forecast_days)
        total_cost = openmeteo.estimated_cost(len(cells), past_days + forecast_days)
        used_today = self.db.usage_since("open-meteo", utcnow() - timedelta(days=1))
        if used_today + total_cost > OPEN_METEO_DAILY_BUDGET:
            msg = f"skipped: daily Open-Meteo budget would be exceeded ({used_today:.0f}+{total_cost:.0f} > {OPEN_METEO_DAILY_BUDGET:.0f})"
            self.db.finish_run(run, "error", message=msg)
            return {"status": "error", "message": msg}

        retrieved = iso(utcnow())
        now_local = datetime.now(ZoneInfo(tz)).replace(tzinfo=None)
        hourly_keep_from = (now_local - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M")
        ok_cells, errors = 0, []
        batches = [cells[i : i + OPEN_METEO_BATCH] for i in range(0, len(cells), OPEN_METEO_BATCH)]
        spent_window: list[tuple[float, float]] = []
        with make_client(timeout=90) as client:
            for batch in batches:
                cost = openmeteo.estimated_cost(len(batch), past_days + forecast_days)
                # Pace to stay under the per-minute budget.
                now_m = time.monotonic()
                spent_window = [(t, c) for t, c in spent_window if now_m - t < 60]
                if sum(c for _, c in spent_window) + cost > OPEN_METEO_PER_MINUTE_BUDGET and spent_window:
                    wait = 60 - (now_m - spent_window[0][0]) + 1
                    log.info("Open-Meteo pacing: waiting %.0fs", wait)
                    time.sleep(max(wait, 0))
                    spent_window = []
                try:
                    series = openmeteo.fetch_hourly(client, [(c["lat"], c["lon"]) for c in batch], past_days, forecast_days, tz)
                except (ProviderError, ValueError) as exc:
                    errors.append(mask(str(exc)))
                    log.warning("Open-Meteo batch failed: %s", exc)
                    continue
                finally:
                    spent_window.append((time.monotonic(), cost))
                    self.db.record_usage("open-meteo", cost)
                self._store_weather(batch, series, now_local, hourly_keep_from, retrieved)
                ok_cells += len(batch)

        status = "ok" if not errors else ("partial" if ok_cells else "error")
        details = {
            "cells": len(cells), "cells_ok": ok_cells, "past_days": past_days, "forecast_days": forecast_days,
            "batches": len(batches), "estimated_weighted_calls": round(total_cost, 1), "batch_cost": round(batch_cost, 1),
            "errors": errors[:5],
        }
        self.db.finish_run(run, status, ok_cells, "; ".join(errors)[:500] or None, details)
        if ok_cells:
            self.compute_fwi(region)
            self.compute_risk(region)
        return {"status": status, **details}

    def _store_weather(self, batch, series, now_local, hourly_keep_from, retrieved) -> None:
        from .weather import derive_daily

        with self.db.connect() as con:
            for cell, s in zip(batch, series):
                v = s.values
                rows = []
                for i, t in enumerate(s.time_local):
                    if t < hourly_keep_from:
                        continue
                    rows.append(
                        (
                            cell["id"], t, v["temperature_2m"][i], v["relative_humidity_2m"][i], v["wind_speed_10m"][i],
                            v["wind_direction_10m"][i], v["wind_gusts_10m"][i], v["precipitation"][i],
                            v["vapour_pressure_deficit"][i], s.model_lat, s.model_lon, s.elevation, retrieved,
                        )
                    )
                con.execute("DELETE FROM weather_hourly WHERE cell_id=? AND time_local < ?", (cell["id"], hourly_keep_from))
                con.executemany(
                    """INSERT OR REPLACE INTO weather_hourly(cell_id, time_local, t2m, rh2m, ws10, wd10, gust10, precip, vpd,
                       model_lat, model_lon, model_elev, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
                # The first day of every response lacks the previous afternoon, so its
                # 24 h rain window is incomplete; never let it overwrite a stored day.
                first_day = s.time_local[0][:10]
                daily = [d for d in derive_daily(s.time_local, v, now_local) if d["date"] != first_day]
                con.executemany(
                    """INSERT OR REPLACE INTO weather_daily(cell_id, date, t_noon, rh_noon, ws_noon, wd_noon, rain24_noon,
                       t_max, rh_min, ws_max, gust_max, vpd_max, precip_day, hours, is_forecast, retrieved_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [
                        (
                            cell["id"], d["date"], d["t_noon"], d["rh_noon"], d["ws_noon"], d["wd_noon"], d["rain24_noon"],
                            d["t_max"], d["rh_min"], d["ws_max"], d["gust_max"], d["vpd_max"], d["precip_day"], d["hours"],
                            d["is_forecast"], retrieved,
                        )
                        for d in daily
                    ],
                )
                cutoff = (now_local.date() - timedelta(days=WEATHER_DAILY_RETENTION_DAYS)).isoformat()
                con.execute("DELETE FROM weather_daily WHERE cell_id=? AND date < ?", (cell["id"], cutoff))

    # ------------------------------------------------------------------ FWI
    def compute_fwi(self, region: str) -> dict:
        run = self.db.start_run("risk-engine", f"fwi:{region}")
        computed = iso(utcnow())
        n = 0
        cells = self.cells(region)
        classes, _cal = fwi_mod.class_scheme(region)
        with self.db.connect() as con:
            for cell in cells:
                rows = con.execute(
                    "SELECT date, t_noon, rh_noon, ws_noon, rain24_noon, is_forecast FROM weather_daily WHERE cell_id=? ORDER BY date",
                    (cell["id"],),
                ).fetchall()
                # Drop leading days whose 24 h rain window is incomplete (series start).
                while rows and rows[0]["rain24_noon"] is None:
                    rows = rows[1:]
                if not rows:
                    continue
                by_date = {r["date"]: r for r in rows}
                start, end = date.fromisoformat(rows[0]["date"]), date.fromisoformat(rows[-1]["date"])
                inputs = []
                d = start
                while d <= end:
                    r = by_date.get(d.isoformat())
                    if r is None:
                        inputs.append(fwi_mod.DailyInput(d.isoformat(), None, None, None, None, False))
                    else:
                        inputs.append(
                            fwi_mod.DailyInput(d.isoformat(), r["t_noon"], r["rh_noon"], r["ws_noon"], r["rain24_noon"], bool(r["is_forecast"]))
                        )
                    d += timedelta(days=1)
                results = fwi_mod.run_series(inputs, cell["lat"], spinup_days=FWI_SPINUP_DAYS)
                con.execute("DELETE FROM fwi_daily WHERE cell_id=?", (cell["id"],))
                con.executemany(
                    """INSERT INTO fwi_daily(cell_id, date, ffmc, dmc, dc, isi, bui, fwi, dsr, fwi_class, is_forecast, spinup, status, computed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [
                        (
                            cell["id"], x.date, _r(x.ffmc), _r(x.dmc), _r(x.dc), _r(x.isi), _r(x.bui), _r(x.fwi), _r(x.dsr),
                            fwi_mod.fwi_class(x.fwi, classes), int(x.is_forecast), int(x.spinup), x.status, computed,
                        )
                        for x in results
                    ],
                )
                n += len(results)
        self.db.finish_run(run, "ok", n, details={"cells": len(cells)})
        return {"status": "ok", "rows": n}

    # ------------------------------------------------------------------ Vegetation
    def ingest_vegetation(self, region: str, lookback_days: int = 40, interval_days: int = 10) -> dict:
        run = self.db.start_run("cdse", f"vegetation:{region}")
        if not (self.s.cdse_client_id and self.s.cdse_client_secret):
            self.db.finish_run(run, "error", message="CDSE credentials not configured")
            return {"status": "error", "message": "CDSE credentials not configured"}
        if self._cdse_tokens is None:
            self._cdse_tokens = cdse.TokenProvider(self.s.cdse_client_id, self.s.cdse_client_secret)
        cells = self.cells(region)
        end = utcnow()
        start = end - timedelta(days=lookback_days - 1)
        retrieved = iso(utcnow())
        # Measured cost: ~0.8 PU per cell request (0.002° sampling, 4 × 10-day intervals),
        # i.e. ~225 PU per weekly Karnataka run vs. 10 000 PU/month free. 60 req/min keeps
        # well below the per-minute PU limit that produced 429s during testing.
        limiter = cdse.RateLimiter(per_minute=60)
        errors: list[str] = []
        ok = 0
        fatal: list[str] = []

        def work(cell: dict) -> tuple[dict, list[cdse.VegInterval] | None, str | None]:
            if fatal:
                return cell, None, "aborted after fatal error"
            limiter.wait()
            polygon = [
                [cell["west"], cell["south"]], [cell["east"], cell["south"]], [cell["east"], cell["north"]],
                [cell["west"], cell["north"]], [cell["west"], cell["south"]],
            ]
            try:
                with make_client(timeout=120) as client:
                    return cell, cdse.fetch_cell_stats(client, self._cdse_tokens, polygon, start, end, interval_days), None  # type: ignore[arg-type]
            except ProviderError as exc:
                if exc.status in (401, 403):
                    fatal.append(str(exc))
                return cell, None, str(exc)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(work, c) for c in cells]
            for fut in as_completed(futures):
                cell, intervals, err = fut.result()
                if err or intervals is None:
                    errors.append(err or "no data")
                    continue
                with self.db.connect() as con:
                    con.executemany(
                        """INSERT OR REPLACE INTO veg_stats(cell_id, interval_from, interval_to, ndvi_mean, ndvi_std, ndmi_mean, ndmi_std,
                           valid_fraction, sample_count, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        [
                            (
                                cell["id"], iv.interval_from, iv.interval_to, _r(iv.ndvi_mean, 4), _r(iv.ndvi_std, 4),
                                _r(iv.ndmi_mean, 4), _r(iv.ndmi_std, 4), _r(iv.valid_fraction, 4), iv.sample_count, retrieved,
                            )
                            for iv in intervals
                        ],
                    )
                ok += 1
        status = "ok" if not errors else ("partial" if ok else "error")
        uniq = sorted(set(errors))[:5]
        details = {"cells": len(cells), "cells_ok": ok, "lookback_days": lookback_days, "interval_days": interval_days, "errors": uniq}
        self.db.finish_run(run, status, ok, "; ".join(uniq)[:500] or None, details)
        if ok:
            self.compute_risk(region)
        return {"status": status, **details}

    # ------------------------------------------------------------------ Composite risk
    def _veg_for(self, cell_id: str, on: str, veg_rows: dict[str, list[dict]]) -> dict | None:
        return select_vegetation(veg_rows.get(cell_id, []), on)

    def compute_risk(self, region: str) -> dict:
        run = self.db.start_run("risk-engine", f"composite:{region}")
        computed = iso(utcnow())
        weights = self.s.composite_weights
        cells = {c["id"]: c for c in self.cells(region)}
        if not cells:
            self.db.finish_run(run, "error", message="no cells")
            return {"status": "error"}
        tz = region_def(region).get("timezone", "UTC")
        today = datetime.now(ZoneInfo(tz)).date()
        first = (today - timedelta(days=RISK_HISTORY_DAYS)).isoformat()
        q = """SELECT f.*, w.t_max, w.rh_min, w.ws_max, w.t_noon, w.rh_noon, w.ws_noon, w.rain24_noon
               FROM fwi_daily f JOIN cells c ON c.id=f.cell_id
               LEFT JOIN weather_daily w ON w.cell_id=f.cell_id AND w.date=f.date
               WHERE c.region=? AND f.date>=? AND f.spinup=0"""
        rows = [dict(r) for r in self.db.query(q, (region, first))]
        veg_rows: dict[str, list[dict]] = {}
        for v in self.db.query(
            "SELECT v.* FROM veg_stats v JOIN cells c ON c.id=v.cell_id WHERE c.region=?", (region,)
        ):
            veg_rows.setdefault(v["cell_id"], []).append(dict(v))
        _, cal = fwi_mod.class_scheme(region)
        class_note = (
            f"class relative to the region's Feb–May {cal['source']['start'][:4]} fire season (percentile thresholds)" if cal else EFFIS_NOTE
        )
        rain: dict[str, dict[str, float | None]] = {}
        for w in self.db.query(
            "SELECT w.cell_id, w.date, w.rain24_noon FROM weather_daily w JOIN cells c ON c.id=w.cell_id WHERE c.region=?", (region,)
        ):
            rain.setdefault(w["cell_id"], {})[w["date"]] = w["rain24_noon"]
        by_date: dict[str, list[dict]] = {}
        for r in rows:
            by_date.setdefault(r["date"], []).append(r)
        out = []
        ml_rows: list[dict] = []
        for d, day_rows in by_date.items():
            dist = {k: [r[k] for r in day_rows if r[k] is not None] for k in ("ffmc", "isi", "bui", "fwi")}
            for r in day_rows:
                veg = self._veg_for(r["cell_id"], d, veg_rows)
                comp = compute_composite(
                    weights,
                    r["t_max"], r["rh_min"], r["ws_max"],
                    dry := days_since_rain(rain.get(r["cell_id"], {}), d),
                    veg["ndmi_mean"] if veg else None,
                    veg["ndvi_mean"] if veg else None,
                )
                pct = {k: percentile_rank(dist[k], r[k]) for k in dist if r[k] is not None} if r["status"] == "ok" else None
                payload = {
                    "composite": comp,
                    "percentiles": pct,
                    "vegetation": veg and {
                        "interval_from": veg["interval_from"], "interval_to": veg["interval_to"], "ndvi": veg["ndvi_mean"],
                        "ndmi": veg["ndmi_mean"], "valid_fraction": veg["valid_fraction"], "retrieved_at": veg["retrieved_at"],
                    },
                    "explanation": explain(comp, r, pct, class_note),
                    "weights": weights,
                }
                out.append([r["cell_id"], d, payload, computed])
                ok = r["status"] == "ok"
                ml_rows.append({
                    "cell_id": r["cell_id"], "date": d,
                    **{k: (r[k] if ok else None) for k in ("fwi", "ffmc", "dmc", "dc", "isi", "bui")},
                    "t_max": r["t_max"], "rh_min": r["rh_min"], "ws_max": r["ws_max"], "rain24": r["rain24_noon"], "dry_days": dry,
                    "ndvi": veg["ndvi_mean"] if veg else None, "ndmi": veg["ndmi_mean"] if veg else None,
                })
        self._attach_ml(region, out, ml_rows, today)
        out = [(c, d, json.dumps(p), t) for c, d, p, t in out]
        with self.db.connect() as con:
            con.execute(
                "DELETE FROM risk_snapshot WHERE cell_id IN (SELECT id FROM cells WHERE region=?)", (region,)
            )
            con.executemany("INSERT INTO risk_snapshot(cell_id, date, payload, computed_at) VALUES (?,?,?,?)", out)
        self.db.finish_run(run, "ok", len(out), details={"dates": len(by_date)})
        res: dict = {"status": "ok", "rows": len(out), "dates": len(by_date)}
        try:  # Fire Risk Index email alerts: watch the new values; never break the risk engine
            from . import risk_alerts

            res["risk_alerts"] = risk_alerts.evaluate(self.db, region)
        except Exception:  # noqa: BLE001
            log.exception("risk alert evaluation failed for %s", region)
        return res

    def _attach_ml(self, region: str, out: list, ml_rows: list[dict], today) -> None:
        """Add the experimental ML detection likelihood (if a trained model exists) to each payload."""
        from . import ml
        from .terrain import cell_terrain_map

        if ml.load_model() is None:
            return
        try:
            hist = ml.live_history(self.db, self.s, region, today)
            probs = ml.score_rows(ml_rows, cell_terrain_map(self.db), hist)
        except Exception as exc:  # noqa: BLE001 - the experimental layer must never break the risk engine
            log.warning("ml scoring skipped: %s", mask(str(exc)))
            return
        for row, p in zip(out, probs):
            row[2]["ml"] = {"probability": p, "model": ml.MODEL_NAME, "in_training_season": ml.in_training_season(row[1])}

    # ------------------------------------------------------------------ freshness helpers
    def last_ok(self, provider: str, job_prefix: str = "") -> datetime | None:
        row = self.db.one(
            "SELECT MAX(finished_at) AS t FROM provider_runs WHERE provider=? AND status IN ('ok','partial') AND job LIKE ?",
            (provider, job_prefix + "%"),
        )
        if row and row["t"]:
            return datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
        return None


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def is_older_than(t: datetime | None, delta: timedelta) -> bool:
    return t is None or datetime.now(timezone.utc) - t > delta
