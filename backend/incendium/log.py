"""Logging with secret masking."""
from __future__ import annotations

import logging
import threading

_secrets: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str | None) -> None:
    if value and len(value) >= 6:
        with _lock:
            _secrets.add(value)


def mask(text: str) -> str:
    with _lock:
        secrets = list(_secrets)
    for s in secrets:
        if s in text:
            text = text.replace(s, "***")
    return text


class _MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        masked = mask(msg)
        if masked != msg:
            record.msg = masked
            record.args = ()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if getattr(root, "_incendium_configured", False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(_MaskingFilter())
    root.addHandler(handler)
    root.setLevel(level)
    # httpx logs full request URLs at INFO; FIRMS puts the key in the URL path.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    root._incendium_configured = True  # type: ignore[attr-defined]
