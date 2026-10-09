"""E-mail. ``outbox`` stores messages in the database (development and tests); ``smtp`` sends them via
the configured SMTP server (STARTTLS). Production refuses to start without SMTP (``Settings.check``)."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy.orm import Session

from emcloud.config import Settings
from emcloud.db import Outbox

log = logging.getLogger(__name__)


class Mailer:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def send(self, db: Session, now: float, to: str, subject: str, body: str) -> None:
        if self.s.mail_mode != "smtp":
            db.add(Outbox(to=to, subject=subject, body=body, ts=now))
            return
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.s.smtp_from, to, subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=20) as smtp:
                if self.s.smtp_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if self.s.smtp_user:
                    smtp.login(self.s.smtp_user, self.s.smtp_password)
                smtp.send_message(msg)
        except (OSError, smtplib.SMTPException) as exc:     # never leak the token in logs
            log.error("mail failed", extra={"to_domain": to.split("@")[-1], "error": type(exc).__name__})
            raise
