"""Outgoing email. "console" (default, development) saves the message under live/outbox instead of sending it."""
import logging
import smtplib
import time
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger(__name__)
outbox: list[dict] = []  # console backend keeps the last messages for tests and local debugging


def send_email(to: str, subject: str, body: str) -> None:
    s = get_settings()
    if s.email_backend == "console":
        outbox.append({"to": to, "subject": subject, "body": body})
        del outbox[:-50]
        log.info("EMAIL (console backend, not sent) to=%s subject=%s", to, subject)
        folder = s.live_dir / "outbox"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{time.time_ns()}.txt").write_text(f"To: {to}\nSubject: {subject}\n\n{body}", encoding="utf-8")
        return
    if s.email_backend != "smtp":
        raise RuntimeError(f"Unknown EMAIL_BACKEND {s.email_backend!r}")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.email_from, to, subject
    msg.set_content(body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        if s.smtp_starttls:
            smtp.starttls()
        if s.smtp_username:
            smtp.login(s.smtp_username, s.smtp_password)
        smtp.send_message(msg)
