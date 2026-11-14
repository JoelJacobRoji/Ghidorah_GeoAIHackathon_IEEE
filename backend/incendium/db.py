"""SQLite persistence. One connection per thread; WAL mode for concurrent reads."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS detections (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    satellite     TEXT,
    instrument    TEXT,
    latitude      REAL NOT NULL,
    longitude     REAL NOT NULL,
    acq_datetime  TEXT NOT NULL,          -- ISO-8601 UTC
    confidence    TEXT,                   -- low | nominal | high (VIIRS)
    frp           REAL,                   -- MW
    bright_ti4    REAL,                   -- K
    bright_ti5    REAL,                   -- K
    scan          REAL,
    track         REAL,
    daynight      TEXT,
    version       TEXT,
    state         TEXT,                   -- Indian state name if inside a state polygon
    cell_id       TEXT,                   -- risk-grid cell if inside an enabled region grid
    retrieved_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_det_time ON detections(acq_datetime);
CREATE INDEX IF NOT EXISTS ix_det_cell ON detections(cell_id);

CREATE TABLE IF NOT EXISTS cells (
    id        TEXT PRIMARY KEY,
    region    TEXT NOT NULL,
    lat       REAL NOT NULL,
    lon       REAL NOT NULL,
    west      REAL NOT NULL,
    south     REAL NOT NULL,
    east      REAL NOT NULL,
    north     REAL NOT NULL,
    overlap   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cells_region ON cells(region);

CREATE TABLE IF NOT EXISTS weather_hourly (
    cell_id     TEXT NOT NULL,
    time_local  TEXT NOT NULL,
    t2m         REAL, rh2m REAL, ws10 REAL, wd10 REAL, gust10 REAL, precip REAL, vpd REAL,
    model_lat   REAL, model_lon REAL, model_elev REAL,
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (cell_id, time_local)
);

CREATE TABLE IF NOT EXISTS weather_daily (
    cell_id     TEXT NOT NULL,
    date        TEXT NOT NULL,            -- local date
    t_noon      REAL, rh_noon REAL, ws_noon REAL, wd_noon REAL,
    rain24_noon REAL,                     -- mm, 13:00 previous day .. 12:00
    t_max       REAL, rh_min REAL, ws_max REAL, gust_max REAL, vpd_max REAL, precip_day REAL,
    hours       INTEGER NOT NULL,         -- hourly records available for the day
    is_forecast INTEGER NOT NULL,         -- 1 if local noon of `date` is in the future at retrieval
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (cell_id, date)
);

CREATE TABLE IF NOT EXISTS fwi_daily (
    cell_id     TEXT NOT NULL,
    date        TEXT NOT NULL,
    ffmc REAL, dmc REAL, dc REAL, isi REAL, bui REAL, fwi REAL, dsr REAL,
    fwi_class   TEXT,
    is_forecast INTEGER NOT NULL,
    spinup      INTEGER NOT NULL,         -- 1 = within spin-up period, not shown as a result
    status      TEXT NOT NULL,            -- ok | missing_inputs
    computed_at TEXT NOT NULL,
    PRIMARY KEY (cell_id, date)
);

CREATE TABLE IF NOT EXISTS veg_stats (
    cell_id       TEXT NOT NULL,
    interval_from TEXT NOT NULL,
    interval_to   TEXT NOT NULL,
    ndvi_mean REAL, ndvi_std REAL, ndmi_mean REAL, ndmi_std REAL,
    valid_fraction REAL,
    sample_count   INTEGER,
    retrieved_at   TEXT NOT NULL,
    PRIMARY KEY (cell_id, interval_from)
);

CREATE TABLE IF NOT EXISTS risk_snapshot (
    cell_id      TEXT NOT NULL,
    date         TEXT NOT NULL,
    payload      TEXT NOT NULL,           -- JSON: factors, composite, completeness, explanation
    computed_at  TEXT NOT NULL,
    PRIMARY KEY (cell_id, date)
);

CREATE TABLE IF NOT EXISTS provider_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,
    job         TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,            -- running | ok | partial | error
    records     INTEGER DEFAULT 0,
    message     TEXT,
    details     TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_provider ON provider_runs(provider, started_at);

CREATE TABLE IF NOT EXISTS api_usage (
    provider TEXT NOT NULL,
    ts       TEXT NOT NULL,
    cost     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_usage ON api_usage(provider, ts);

CREATE TABLE IF NOT EXISTS cell_terrain (
    cell_id     TEXT PRIMARY KEY,
    elev_mean   REAL, elev_min REAL, elev_max REAL,   -- m
    slope_mean  REAL, slope_p90 REAL,                 -- degrees
    relief_std  REAL,                                 -- m, std of elevation within the cell
    zoom        INTEGER NOT NULL,
    sample_m    REAL NOT NULL,                        -- approx. sample spacing used
    computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS osm_features (
    id          TEXT PRIMARY KEY,                     -- "node/123" | "way/456"
    kind        TEXT NOT NULL,                        -- fire_station | settlement
    subtype     TEXT,                                 -- city | town | village | hamlet | ...
    name        TEXT,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    tags        TEXT,                                 -- JSON (selected tags only)
    scope       TEXT NOT NULL,                        -- query scope, e.g. "IN" or "IN-KA"
    retrieved_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_osm_kind ON osm_features(kind, lat, lon);

CREATE TABLE IF NOT EXISTS alerts (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    kind          TEXT NOT NULL,                      -- detection | outlook
    priority      TEXT NOT NULL,                      -- low | medium | high
    title         TEXT NOT NULL,
    message       TEXT NOT NULL,
    lat           REAL, lon REAL,
    state         TEXT, cell_id TEXT,
    payload       TEXT NOT NULL,                      -- JSON: rules met, detections, settlements, stations
    acknowledged_at TEXT,
    notified_at   TEXT,
    notify_status TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_created ON alerts(created_at);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        # Keep one shared connection for :memory: so tests see the same database.
        self._shared: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._shared = self._connect()
        with self.connect() as con:
            con.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        if self.path != ":memory:":
            con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _con(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        con = getattr(self._local, "con", None)
        if con is None:
            con = self._connect()
            self._local.con = con
        return con

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = self._con()
        with self._write_lock:
            try:
                yield con
                con.commit()
            except Exception:
                con.rollback()
                raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self._con().execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self._con().execute(sql, params).fetchone()

    # ---- provider run bookkeeping ------------------------------------------------
    def start_run(self, provider: str, job: str) -> int:
        with self.connect() as con:
            cur = con.execute(
                "INSERT INTO provider_runs(provider, job, started_at, status) VALUES (?,?,?,?)",
                (provider, job, iso(utcnow()), "running"),
            )
            return int(cur.lastrowid)

    def finish_run(
        self, run_id: int, status: str, records: int = 0, message: str | None = None, details: Any = None
    ) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE provider_runs SET finished_at=?, status=?, records=?, message=?, details=? WHERE id=?",
                (iso(utcnow()), status, records, message, json.dumps(details) if details is not None else None, run_id),
            )

    def record_usage(self, provider: str, cost: float) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO api_usage(provider, ts, cost) VALUES (?,?,?)", (provider, iso(utcnow()), cost))

    def usage_since(self, provider: str, since: datetime) -> float:
        row = self.one("SELECT COALESCE(SUM(cost),0) AS c FROM api_usage WHERE provider=? AND ts>=?", (provider, iso(since)))
        return float(row["c"]) if row else 0.0
