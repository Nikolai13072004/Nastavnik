"""Outbound email - dual backend (Stage 23).

Mirrors the scaling plan's guiding principle: every external dependency keeps a
local default and gains a production backend by env. Email has three:

* ``console`` (default) - log the message + any link to stdout. Zero infra, so
  dev / CI / smoke work without an SMTP server; a developer copies the reset /
  verify link straight from the logs.
* ``smtp`` - real delivery via ``config.SMTP_*`` (production).
* ``memory`` - collect into an in-process :data:`OUTBOX` (the test suite reads
  the link back out of it).

Selected by ``config.EMAIL_BACKEND``. Callers use :func:`send_email`.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

import config

logger = logging.getLogger("vedomo.email")


@dataclass
class SentEmail:
    to: str
    subject: str
    body: str


# In-process outbox for the ``memory`` backend (tests only). Never used in prod.
OUTBOX: list[SentEmail] = []


def send_email(to: str, subject: str, body: str) -> bool:
    """Deliver an email through the configured backend.

    Best-effort by contract: a delivery failure must never break the auth flow
    that triggered it (we still return 200 so we don't leak whether an account
    exists). SMTP errors are logged, not raised.
    """
    backend = config.EMAIL_BACKEND
    if backend == "memory":
        OUTBOX.append(SentEmail(to=to, subject=subject, body=body))
        return True
    if backend == "smtp":
        return _send_smtp(to, subject, body)
    if backend != "console":
        # A typo must not turn production mail into plaintext reset-link logs.
        logger.error("email_delivery_failed reason=invalid_backend")
        return False
    # console (default): print straight to stdout so the link is visible in the
    # `run_api.py` console regardless of logging config. The app doesn't call
    # logging.basicConfig(), so a plain logger.info() here would be swallowed by
    # the root logger's WARNING-level last-resort handler.
    print(f"\n[EMAIL] -> {to} | {subject}\n{body}\n", flush=True)
    return True


def _send_smtp(to: str, subject: str, body: str) -> bool:
    try:
        msg = EmailMessage()
        msg["From"] = config.EMAIL_FROM
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        # Port 465 = implicit TLS on connect (SMTP_SSL); port 587 = plain connect
        # then STARTTLS. Russian providers (Yandex/Mail.ru) use the former.
        if config.SMTP_USE_SSL:
            smtp = smtplib.SMTP_SSL(
                config.SMTP_HOST, config.SMTP_PORT, timeout=15,
                context=ssl.create_default_context(),
            )
        else:
            smtp = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
        with smtp:
            if not config.SMTP_USE_SSL and config.SMTP_USE_TLS:
                smtp.starttls(context=ssl.create_default_context())
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001 - delivery must not 500 the flow
        # SMTP responses may contain addresses, credentials or message content.
        # Keep a stable operator signal without exception text or traceback.
        logger.error("email_delivery_failed reason=%s", type(exc).__name__)
        return False
