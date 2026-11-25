"""Account rules shared by every way of signing up (email, Google, phone): the under-18 question, guardian consent by
an emailed link, linking a Google identity, and removing under-18 accounts nobody consented to. Same rules as
NEET Compass."""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Role, User
from app.security import NO_PASSWORD
from app.services.email import send_email

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


def mask(email: str) -> str:
    name, _, domain = (email or "").partition("@")
    return f"{name[:2]}{'*' * max(1, len(name) - 2)}@{domain}"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def has_consent(user: User) -> bool:
    """May this account use the site (store progress, tests, chats)?"""
    return user.role == Role.admin or (user.guardian_consent_status in ("not_required", "verified"))


def age_answer(minor: str | None, guardian_email: str, own_email: str | None, privacy: bool) -> str | None:
    """Checks the sign-up questions; returns an error message or None."""
    if not privacy:
        return "Please read and accept the privacy notice."
    if minor not in ("yes", "no"):
        return "Please say whether you are under 18."
    if minor == "yes":
        st = get_settings()
        if st.email_backend == "console" and st.env != "development":
            # Without real email the parent never gets the link; say so instead of creating an account that can't open.
            return "Under-18 sign-up needs email to be set up on this site, which isn't done yet. Please try again later."
        g = (guardian_email or "").strip().lower()
        if not EMAIL.match(g) or len(g) > 254:
            return "Students under 18 need a parent/guardian's email so they can give consent."
        if own_email and g == own_email.lower():
            return "The parent/guardian's email must be different from yours."
    return None


def apply_age(db: Session, user: User, minor: bool, guardian_email: str) -> None:
    """Record the answers on a new (or older, "ask") account and email the guardian when needed."""
    user.privacy_accepted = get_settings().privacy_notice_version
    user.is_minor = minor
    user.guardian_email = guardian_email.strip().lower() if minor else None
    user.guardian_consent_status = "pending" if minor else "not_required"
    db.flush()
    if minor:
        request_consent(db, user)


def request_consent(db: Session, user: User) -> None:
    s = get_settings()
    token = secrets.token_urlsafe(32)
    user.guardian_token_hash = _hash(token)
    user.guardian_token_expires_at = now() + timedelta(hours=s.guardian_link_hours)
    who = user.email or user.phone or user.name
    send_email(user.guardian_email or "", "Consent needed: your child's JEE Workbench account", (
        f"A student account was created on JEE Workbench ({who}), and you were named as their parent or guardian.\n\n"
        "Because the student is under 18, the site will not store anything about them (test answers, scores, study "
        "plan, chats) until you review what it keeps and give consent:\n\n"
        f"{s.link('/guardian-consent?token=' + token)}\n\n"
        f"This link expires in {s.guardian_link_hours} hours. If you do not recognise this request, ignore this "
        f"email: the account will be deleted automatically after {s.unconsented_minor_retention_days} days."))


def user_for_token(db: Session, token: str) -> User | None:
    user = db.scalar(select(User).where(User.guardian_token_hash == _hash(token))) if token else None
    exp = _aware(user.guardian_token_expires_at) if user else None
    return user if user is not None and exp is not None and exp > now() else None


def decide(db: Session, user: User, approve: bool, guardian_name: str, relationship: str) -> None:
    user.guardian_consent_status = "verified" if approve else "declined"
    user.guardian_consent_at = now()
    user.guardian_consent_by = f"{guardian_name} ({relationship})"[:200]
    user.guardian_token_hash = user.guardian_token_expires_at = None  # single use
    db.commit()


def google_user(db: Session, claims: dict) -> User | None:
    """The account for a verified Google identity: by Google id, or an existing account with the same (verified)
    email, which is then linked so the student can sign in either way."""
    user = db.scalar(select(User).where(User.google_sub == claims["sub"]))
    if user is None:
        user = db.scalar(select(User).where(User.email == claims["email"].lower()))
        if user is not None:
            user.google_sub = claims["sub"]
            # Email sign-up never proved who owns the address; Google just did. Drop a password set by whoever
            # registered it first, so a planted account can't keep reading the owner's data. Admins keep theirs.
            if user.role != Role.admin:
                user.password_hash = NO_PASSWORD
            db.commit()
    return user


def purge_unconsented(db: Session) -> int:
    """Delete under-18 accounts whose guardian declined, or never answered within the retention period."""
    cutoff = now() - timedelta(days=get_settings().unconsented_minor_retention_days)
    gone = 0
    for u in db.scalars(select(User).where(User.is_minor.is_(True),
                                           User.guardian_consent_status.in_(("pending", "declined")))):
        if u.guardian_consent_status == "declined" or _aware(u.created_at) < cutoff:
            db.delete(u)
            gone += 1
    db.commit()
    return gone
