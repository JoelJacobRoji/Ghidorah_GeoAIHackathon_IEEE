"""Incendium — Early Wildfire Awareness & Environmental Risk Intelligence (backend)."""
from __future__ import annotations

import os

from flask import Flask
from flask_cors import CORS

from .config import Settings, get_secret, get_settings
from .db import Database
from .ingest import Ingestor
from .log import configure_logging, register_secret


def create_app(settings: Settings | None = None, db: Database | None = None, start_jobs: bool | None = None) -> Flask:
    configure_logging()
    s = settings or get_settings()
    for secret in (s.firms_map_key, s.cdse_client_id, s.cdse_client_secret):
        register_secret(secret)

    for name in ("INCENDIUM_SMTP_PASSWORD", "INCENDIUM_SMTP_USER", "INCENDIUM_ALERT_WEBHOOK_URL"):
        register_secret(get_secret(name))
    app = Flask(__name__)
    app.json.sort_keys = False  # type: ignore[attr-defined]
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    CORS(app, resources={r"/api/*": {"origins": list(s.cors_origins)}}, methods=["GET", "POST"])

    database = db or Database(s.db_path)
    ingestor = Ingestor(database, s)
    ingestor.sync_cells()
    app.extensions["incendium_settings"] = s
    app.extensions["incendium_db"] = database
    app.extensions["incendium_ingestor"] = ingestor

    from .api import bp
    from .api_ops import bp as ops_bp

    app.register_blueprint(bp)
    app.register_blueprint(ops_bp)

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    run_jobs = s.enable_scheduler if start_jobs is None else start_jobs
    # Avoid double-start under the Flask reloader parent process.
    if run_jobs and os.environ.get("WERKZEUG_RUN_MAIN", "true") == "true":
        from .scheduler import start_scheduler

        app.extensions["incendium_scheduler"] = start_scheduler(ingestor)
    return app
