"""Background ingestion schedule (APScheduler, in-process)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from .ingest import Ingestor, is_older_than

OSM_EVERY = timedelta(days=7)

log = logging.getLogger(__name__)

FIRMS_EVERY = timedelta(minutes=30)
WEATHER_EVERY = timedelta(hours=3)
VEG_EVERY = timedelta(days=7)


def _safe(name, fn, *args, **kwargs):
    def run():
        try:
            log.info("job %s starting", name)
            result = fn(*args, **kwargs)
            log.info("job %s finished: %s", name, {k: v for k, v in (result or {}).items() if k != "errors"})
        except Exception:
            log.exception("job %s crashed", name)

    return run


def start_scheduler(ing: Ingestor) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600})
    now = datetime.now(timezone.utc)
    has_detections = ing.db.one("SELECT COUNT(*) AS n FROM detections")["n"] > 0

    firms_first = now + timedelta(seconds=5) if is_older_than(ing.last_ok("firms"), FIRMS_EVERY) else now + FIRMS_EVERY
    firms_fn = _safe("firms", ing.ingest_firms) if has_detections else _safe("firms-backfill", ing.ingest_firms, backfill_days=7)
    from . import alerts, places, terrain

    regions = list(ing.s.risk_regions)

    def firms_then_alerts(fn=firms_fn):
        fn()
        _safe("alerts", alerts.run_cycle, ing.db, regions)()

    def firms_regular():
        _safe("firms", ing.ingest_firms)()
        _safe("alerts", alerts.run_cycle, ing.db, regions)()

    sched.add_job(firms_then_alerts, "date", run_date=firms_first, id="firms-initial")
    sched.add_job(firms_regular, "interval", minutes=30, id="firms", next_run_time=firms_first + FIRMS_EVERY)

    def osm_check():
        if is_older_than(ing.last_ok("osm", "places"), OSM_EVERY):
            _safe("osm-places", places.refresh, ing.db, regions)()

    sched.add_job(osm_check, "interval", hours=24, id="osm", next_run_time=now + timedelta(minutes=5))
    for region in regions:
        sched.add_job(_safe(f"terrain:{region}", terrain.compute_cell_terrain, ing.db, region), "date",
                      run_date=now + timedelta(minutes=6), id=f"terrain-{region}")

    for i, region in enumerate(ing.s.risk_regions):
        last_wx = ing.last_ok("open-meteo", f"weather:{region}")
        wx_first = now + timedelta(seconds=20 + 30 * i) if is_older_than(last_wx, WEATHER_EVERY) else last_wx + WEATHER_EVERY  # type: ignore[operator]
        def weather_then_outlook(region=region):
            _safe(f"weather:{region}", ing.ingest_weather, region)()
            _safe("alerts-outlook", alerts.run_cycle, ing.db, [region], True)()

        sched.add_job(weather_then_outlook, "interval", hours=3, id=f"weather-{region}", next_run_time=wx_first)

        def veg_check(region=region):
            if is_older_than(ing.last_ok("cdse", f"vegetation:{region}"), VEG_EVERY):
                _safe(f"vegetation:{region}", ing.ingest_vegetation, region)()

        sched.add_job(veg_check, "interval", hours=12, id=f"veg-{region}", next_run_time=now + timedelta(minutes=2 + i))

    sched.start()
    log.info("scheduler started with jobs: %s", [j.id for j in sched.get_jobs()])
    return sched
