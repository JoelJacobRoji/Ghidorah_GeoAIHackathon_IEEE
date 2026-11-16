"""Command line:

    python -m incendium serve [--port 5000] [--no-jobs]
    python -m incendium check-credentials
    python -m incendium ingest firms [--backfill N]
    python -m incendium ingest weather [--region karnataka] [--warmup]
    python -m incendium ingest vegetation [--region karnataka]
    python -m incendium compute [--region karnataka]
    python -m incendium bootstrap            # first-time: detections + weather warm-up + vegetation + risk
    python -m incendium validate [--region karnataka] [--start 2026-02-01] [--end 2026-05-31]
    python -m incendium calibrate-fwi [--region karnataka] [--start 2025-02-01] [--end 2025-05-31]
    python -m incendium terrain [--region karnataka]       # per-cell elevation/slope from Mapterhorn
    python -m incendium places                             # OpenStreetMap fire stations + settlements
    python -m incendium alerts                             # evaluate alert rules now
    python -m incendium ml-train [--region karnataka]      # train on 2025, test on 2026
    python -m incendium firebreak-design [--region karnataka]  # fire-season design wind/dryness per cell
    python -m incendium alert-status                       # Fire Risk Index email alerts: config + state (no secrets)
    python -m incendium alert-test [--send] [--areas 2]    # sample alert email marked [TEST]; outbox unless --send
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import get_secret, get_settings
from .db import Database
from .ingest import Ingestor
from .log import configure_logging, register_secret

log = logging.getLogger("incendium")


def _ingestor() -> Ingestor:
    s = get_settings()
    for secret in (s.firms_map_key, s.cdse_client_id, s.cdse_client_secret, get_secret("INCENDIUM_SMTP_PASSWORD")):
        register_secret(secret)
    ing = Ingestor(Database(s.db_path), s)
    ing.sync_cells()
    return ing


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    p = argparse.ArgumentParser(prog="incendium")
    sub = p.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=5000)
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--no-jobs", action="store_true")
    sub.add_parser("check-credentials")
    ig = sub.add_parser("ingest")
    ig.add_argument("what", choices=["firms", "weather", "vegetation"])
    ig.add_argument("--region", default=None)
    ig.add_argument("--backfill", type=int, default=None)
    ig.add_argument("--warmup", action="store_true")
    cp = sub.add_parser("compute")
    cp.add_argument("--region", default=None)
    sub.add_parser("bootstrap")
    va = sub.add_parser("validate", help="retrospective comparison of indicators with archived detections")
    va.add_argument("--region", default="karnataka")
    va.add_argument("--start", default="2026-02-01")
    va.add_argument("--end", default="2026-05-31")
    ca = sub.add_parser("calibrate-fwi", help="derive regional FWI class thresholds from a validated season's FWI values")
    ca.add_argument("--region", default="karnataka")
    ca.add_argument("--start", default="2025-02-01")
    ca.add_argument("--end", default="2025-05-31")
    te = sub.add_parser("terrain")
    te.add_argument("--region", default="karnataka")
    te.add_argument("--force", action="store_true")
    sub.add_parser("places")
    sub.add_parser("alerts")
    mt = sub.add_parser("ml-train")
    mt.add_argument("--region", default="karnataka")
    fd = sub.add_parser("firebreak-design", help="fire-season design wind and dryness per cell (for firebreak planning)")
    fd.add_argument("--region", default="karnataka")
    sub.add_parser("alert-status", help="Fire Risk Index email alert configuration and state (no secrets)")
    at = sub.add_parser("alert-test", help="build a sample risk alert email marked [TEST]")
    at.add_argument("--region", default="karnataka")
    at.add_argument("--areas", type=int, default=2)
    at.add_argument("--send", action="store_true", help="really send via SMTP (default: write to instance/outbox)")
    args = p.parse_args(argv)

    if args.cmd == "calibrate-fwi":
        return _calibrate(args.region, args.start, args.end)
    if args.cmd in ("terrain", "places", "alerts", "ml-train", "firebreak-design", "alert-status", "alert-test"):
        s0 = get_settings()
        for secret in (s0.firms_map_key, s0.cdse_client_id, s0.cdse_client_secret, get_secret("INCENDIUM_SMTP_PASSWORD")):
            register_secret(secret)
        db0 = Database(s0.db_path)
        Ingestor(db0, s0).sync_cells()
        if args.cmd == "terrain":
            from .terrain import compute_cell_terrain

            _print(compute_cell_terrain(db0, args.region, force=args.force))
        elif args.cmd == "places":
            from .places import counts, refresh

            _print(refresh(db0, list(s0.risk_regions)))
            _print(counts(db0))
        elif args.cmd == "alert-status":
            from .risk_alerts import status

            _print(status(db0))
        elif args.cmd == "alert-test":
            from .risk_alerts import send_test

            _print(send_test(db0, args.region, really_send=args.send, n=max(1, args.areas)))
        elif args.cmd == "firebreak-design":
            from .firebreak_design import build

            _print(build(db0, args.region))
        elif args.cmd == "alerts":
            from .alerts import run_cycle

            _print(run_cycle(db0, list(s0.risk_regions), outlook=True))
        else:
            from .ml import train_and_test

            rep = train_and_test(db0, s0, args.region)
            _print({k: rep[k] for k in ("train", "test", "selected_for_display")})
            _print({"baselines": {k: {kk: v.get(kk) for kk in ("auc", "auc_within_month", "brier")} for k, v in rep["baselines"].items()},
                    "models": {k: {kk: v.get(kk) for kk in ("auc", "ci_low", "ci_high", "auc_within_month", "brier", "brier_skill_vs_climatology", "train_auc")}
                               for k, v in rep["models"].items()}})
            _print(rep["permutation_importance_test"][:8])
        return 0

    if args.cmd == "validate":
        from .config import BACKEND_DIR
        from .validation import ValidationConfig, Validator, to_markdown

        s = get_settings()
        for secret in (s.firms_map_key, s.cdse_client_id, s.cdse_client_secret, get_secret("INCENDIUM_SMTP_PASSWORD")):
            register_secret(secret)
        from .validation import IncompleteData

        try:
            report = Validator(Database(s.db_path), s, ValidationConfig(region=args.region, start=args.start, end=args.end)).run()
        except IncompleteData as exc:
            print(f"incomplete: {exc}")
            return 3
        season = args.start[:4]
        md_path = BACKEND_DIR.parent / "docs" / f"VALIDATION-{args.region}-{season}.md"
        md_path.write_text(to_markdown(report), encoding="utf-8")
        print(f"report: instance/validation/report-{args.region}-{season}.json  markdown: {md_path}")
        _print({k: report["meta"][k] for k in ("cell_days", "cell_days_with_detection", "base_rate")})
        _print({k: {"auc": v["auc"], "auc_within_month": v["auc_within_month"]} for k, v in report["auc"].items()})
        return 0

    if args.cmd == "serve":
        from . import create_app

        app = create_app(start_jobs=not args.no_jobs)
        app.run(host=args.host, port=args.port, debug=False, threaded=True)
        return 0

    s = get_settings()
    if args.cmd == "check-credentials":
        from .providers import cdse, firms
        from .providers.http import ProviderError, make_client

        register_secret(s.firms_map_key)
        register_secret(s.cdse_client_secret)
        res = {}
        with make_client() as c:
            if s.firms_map_key:
                try:
                    dets, _ = firms.fetch_area(c, s.firms_map_key, "VIIRS_NOAA20_NRT", (76.0, 13.0, 77.0, 14.0), 1)
                    res["FIRMS_MAP_KEY"] = f"present, valid ({len(dets)} detections in test box)"
                except ProviderError as e:
                    res["FIRMS_MAP_KEY"] = f"present, INVALID or provider error: {e}"
            else:
                res["FIRMS_MAP_KEY"] = "missing"
            if s.cdse_client_id and s.cdse_client_secret:
                try:
                    cdse.TokenProvider(s.cdse_client_id, s.cdse_client_secret).get(c)
                    res["CDSE"] = "present, valid (token issued)"
                except ProviderError as e:
                    res["CDSE"] = f"present, INVALID or provider error: {e}"
            else:
                res["CDSE"] = "missing"
        _print(res)
        return 0

    ing = _ingestor()
    regions = [args.region] if getattr(args, "region", None) else list(s.risk_regions)
    if args.cmd == "ingest":
        if args.what == "firms":
            _print(ing.ingest_firms(backfill_days=args.backfill))
        elif args.what == "weather":
            for r in regions:
                _print(ing.ingest_weather(r, force_warmup=args.warmup))
        else:
            for r in regions:
                _print(ing.ingest_vegetation(r))
        return 0
    if args.cmd == "compute":
        for r in regions:
            _print(ing.compute_fwi(r))
            _print(ing.compute_risk(r))
        return 0
    if args.cmd == "bootstrap":
        _print({"cells": ing.sync_cells()})
        _print(ing.ingest_firms(backfill_days=10))
        for r in regions:
            _print(ing.ingest_weather(r, force_warmup=True))
            _print(ing.ingest_vegetation(r))
        return 0
    return 1


def _calibrate(region: str, start: str, end: str) -> int:
    """Percentile thresholds from the cached FWI of a validated season (no detection data used)."""
    import pandas as pd

    from . import fwi as fwi_mod
    from .config import BACKEND_DIR
    from .db import iso, utcnow

    table = BACKEND_DIR / "instance" / "validation" / "cache" / f"{region}_{start}_{end}" / "cell_days.csv"
    if not table.exists():
        print(f"no cell-day table for {region} {start}..{end}; run `python -m incendium validate --region {region} --start {start} --end {end}` first")
        return 2
    df = pd.read_csv(table, usecols=["cell", "fwi"])
    vals = df["fwi"].dropna()
    th = fwi_mod.calibrate_thresholds(vals.tolist())
    entry = {
        "thresholds": th,
        "percentiles": list(fwi_mod.CALIBRATION_PERCENTILES),
        "method": "Pooled percentiles of daily FWI over every cell-day of the reference season (ERA5 inputs, GFWED day-length factors). Detection data were not used.",
        "source": {"start": start, "end": end, "cell_days": int(vals.size), "cells": int(df["cell"].nunique()), "weather": "Open-Meteo archive API, ERA5"},
        "generated_at": iso(utcnow()),
    }
    cals = fwi_mod.load_calibrations()
    cals[region] = entry
    fwi_mod.CALIBRATION_FILE.write_text(json.dumps(cals, indent=2), encoding="utf-8")
    _print({region: entry})
    return 0


if __name__ == "__main__":
    sys.exit(main())
