"""Runtime configuration.

Secrets are resolved in this order, without ever being written to disk by Incendium:
  1. process environment
  2. backend/.env (git-ignored), loaded with python-dotenv (does not override 1)
  3. Windows only: the user's persistent environment (HKCU\\Environment)

Values are never logged. Use :func:`incendium.log.register_secret` so that any
accidental occurrence in a log line is masked.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(__file__).resolve().parent / "data"

load_dotenv(BACKEND_DIR / ".env", override=False)


def _windows_user_env(name: str) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value) if value else None
    except OSError:
        return None


def get_secret(name: str) -> str | None:
    value = os.environ.get(name) or _windows_user_env(name)
    if value:
        value = value.strip()
    return value or None


def _env(name: str, default: str | None = None) -> str | None:
    """INCENDIUM_* setting, falling back to the pre-rename EMBER_* name."""
    v = os.environ.get(name)
    if v is None and name.startswith("INCENDIUM_"):
        v = os.environ.get("EMBER_" + name[len("INCENDIUM_"):])
    return default if v is None else v


def _bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_WEIGHTS = {"T": 0.25, "H": 0.25, "W": 0.20, "D": 0.15, "V": 0.15}


def _weights() -> dict[str, float]:
    raw = _env("INCENDIUM_COMPOSITE_WEIGHTS")
    if not raw:
        return dict(DEFAULT_WEIGHTS)
    parts = [float(p) for p in raw.split(",")]
    if len(parts) != 5 or any(p < 0 for p in parts) or abs(sum(parts) - 1.0) > 1e-6:
        raise ValueError("INCENDIUM_COMPOSITE_WEIGHTS must be 5 non-negative numbers summing to 1.0")
    return dict(zip(["T", "H", "W", "D", "V"], parts))


@dataclass(frozen=True)
class Settings:
    db_path: Path
    firms_map_key: str | None
    cdse_client_id: str | None
    cdse_client_secret: str | None
    cors_origins: tuple[str, ...]
    risk_regions: tuple[str, ...]
    enable_scheduler: bool
    allow_manual_refresh: bool
    composite_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    firms_sources: tuple[str, ...] = ("VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")
    user_agent: str = "Incendium-wildfire-awareness/0.1 (non-commercial research prototype)"
    http_timeout_s: float = 45.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    db_raw = _env("INCENDIUM_DB_PATH", "instance/incendium.sqlite3")
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = BACKEND_DIR / db_path
    origins = _env("INCENDIUM_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    regions = _env("INCENDIUM_RISK_REGIONS", "karnataka")
    return Settings(
        db_path=db_path,
        firms_map_key=get_secret("FIRMS_MAP_KEY"),
        cdse_client_id=get_secret("CDSE_CLIENT_ID"),
        cdse_client_secret=get_secret("CDSE_CLIENT_SECRET"),
        cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
        risk_regions=tuple(r.strip() for r in regions.split(",") if r.strip()),
        enable_scheduler=_bool("INCENDIUM_SCHEDULER", True),
        allow_manual_refresh=_bool("INCENDIUM_ALLOW_MANUAL_REFRESH", True),
        composite_weights=_weights(),
    )


@lru_cache(maxsize=1)
def load_region_config() -> dict:
    with open(DATA_DIR / "regions.json", encoding="utf-8") as fh:
        return json.load(fh)
