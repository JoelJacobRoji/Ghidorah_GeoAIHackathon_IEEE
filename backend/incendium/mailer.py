"""Outgoing email for alerts (SMTP), shared by detection alerts and Fire Risk Index alerts.

* The SMTP password is read from the environment at send time (INCENDIUM_SMTP_PASSWORD)
  and never logged or stored.
* Test mode (INCENDIUM_EMAIL_TEST_MODE=1) does not contact any mail server: the message
  is written to backend/instance/outbox/ as an .eml file (open it in any mail client)
  plus an .html preview, and the attempt is logged.
* Port 465 uses implicit TLS; any other port uses STARTTLS. Certificates are verified.
"""
from __future__ import annotations

import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .config import BACKEND_DIR, AlertSettings, get_secret
from .db import utcnow

log = logging.getLogger(__name__)

OUTBOX_DIR = BACKEND_DIR / "instance" / "outbox"
SMTP_TIMEOUT_S = 30


class MailError(RuntimeError):
    pass


def build_message(cfg: AlertSettings, subject: str, text: str, html: str | None, recipients: tuple[str, ...]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.email_sender or "incendium@localhost"
    # Recipients go in Bcc-style delivery: each sees only the sender, not the others' addresses.
    msg["To"] = cfg.email_sender or "undisclosed-recipients:;"
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="incendium.local")
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def send(cfg: AlertSettings, subject: str, text: str, html: str | None = None,
         recipients: tuple[str, ...] | None = None) -> dict:
    """Deliver one message to every recipient. Returns {"status": "sent"|"test", ...}; raises MailError on failure."""
    to = tuple(recipients if recipients is not None else cfg.alert_recipients)
    if not to:
        raise MailError("no recipients configured")
    msg = build_message(cfg, subject, text, html, to)
    if cfg.email_test_mode:
        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        stem = utcnow().strftime("%Y%m%dT%H%M%S%f") + "_" + re.sub(r"[^A-Za-z0-9]+", "-", subject)[:60].strip("-")
        eml = OUTBOX_DIR / f"{stem}.eml"
        msg["X-Incendium-Test-Recipients"] = ", ".join(to)
        eml.write_bytes(bytes(msg))
        if html:
            (OUTBOX_DIR / f"{stem}.html").write_text(html, encoding="utf-8")
        log.info("email TEST MODE: %r to %d recipient(s) written to %s (not sent)", subject, len(to), eml.name)
        return {"status": "test", "recipients": len(to), "file": str(eml)}

    pwd = get_secret("INCENDIUM_SMTP_PASSWORD")
    if not (cfg.smtp_host and cfg.smtp_user and pwd):
        raise MailError("SMTP host, user or password not configured")
    ctx = ssl.create_default_context()
    try:
        if cfg.smtp_port == 465:
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=ctx, timeout=SMTP_TIMEOUT_S) as s:
                s.login(cfg.smtp_user, pwd)
                refused = s.send_message(msg, to_addrs=list(to))
        else:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=SMTP_TIMEOUT_S) as s:
                s.starttls(context=ctx)
                s.login(cfg.smtp_user, pwd)
                refused = s.send_message(msg, to_addrs=list(to))
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(f"SMTP authentication failed (code {exc.smtp_code}); check INCENDIUM_SMTP_USER and the app password") from None
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(f"SMTP delivery failed: {type(exc).__name__}") from None
    delivered = len(to) - len(refused or {})
    if delivered == 0:
        raise MailError("all recipients were refused by the mail server")
    log.info("email sent: %r to %d of %d recipient(s)", subject, delivered, len(to))
    return {"status": "sent", "recipients": delivered, "refused": len(refused or {})}
