"""Under-18 guardian consent, the one-time age question for older accounts, Google and phone sign-in (with the
outside services replaced by fakes), and account deletion."""
import re
from datetime import timedelta

from sqlalchemy import select

from app.models import Role, User
from app.security import NO_PASSWORD
from app.services import accounts, google_auth, phone_auth
from app.services.email import outbox
from tests.conftest import Browser


def _signup(b: Browser, email: str, **extra):
    data = {"email": email, "name": "Ravi", "password": "password-123", "privacy": "on", **extra}
    return b.c.post("/register", data=data, follow_redirects=False)


def test_under_18_waits_for_guardian_then_opens(db):
    b = Browser()
    assert "privacy notice" in _signup(b, "kid@x.test", minor="yes").text  # no guardian email, no account
    assert "different from yours" in _signup(b, "kid@x.test", minor="yes", guardian_email="kid@x.test").text
    assert _signup(b, "kid@x.test", minor="yes", guardian_email="Mum@x.test").status_code == 303
    u = db.scalar(select(User).where(User.email == "kid@x.test"))
    assert (u.is_minor, u.guardian_consent_status, u.guardian_email) == (True, "pending", "mum@x.test")
    # nothing personal opens until the guardian agrees; public pages still work
    assert b.get("/practice", follow_redirects=False).headers["location"] == "/account/consent"
    assert "mu*@x.test" in b.get("/account/consent").text and b.get("/jee").status_code == 200
    mail = outbox[-1]
    assert mail["to"] == "mum@x.test"
    token = re.search(r"token=([\w-]+)", mail["body"]).group(1)
    page = b.get(f"/guardian-consent?token={token}").text
    assert "Ravi wants to use JEE Workbench" in page
    assert "parent or legal guardian" in b.post("/guardian-consent", data={"token": token, "decision": "approve",
                                                 "guardian_name": "Asha", "relationship": "Mother"}).text  # no tick
    done = b.post("/guardian-consent", data={"token": token, "decision": "approve", "guardian_name": "Asha",
                                             "relationship": "Mother", "confirm": "on"}).text
    assert "consent recorded" in done
    assert "invalid or has expired" in b.get(f"/guardian-consent?token={token}").text  # single use
    db.expire_all()
    assert db.get(User, u.id).guardian_consent_by == "Asha (Mother)"
    assert b.get("/practice").status_code == 200


def test_declined_and_unanswered_minors_are_purged(db):
    for email, decision in (("a@x.test", "decline"), ("b@x.test", None), ("c@x.test", None)):
        _signup(Browser(), email, minor="yes", guardian_email="p@x.test")
        if decision:
            token = re.search(r"token=([\w-]+)", outbox[-1]["body"]).group(1)
            Browser().post("/guardian-consent", data={"token": token, "decision": decision, "guardian_name": "P Q",
                                                      "relationship": "Father", "confirm": "on"})
    old = db.scalar(select(User).where(User.email == "b@x.test"))
    old.created_at = accounts.now() - timedelta(days=8)
    db.commit()
    assert accounts.purge_unconsented(db) == 2  # declined + a week without an answer; c@ still has time
    assert [u.email for u in db.scalars(select(User))] == ["c@x.test"]


def test_older_accounts_answer_the_age_question_once(browser, db):
    browser.register()
    db.query(User).update({"guardian_consent_status": "ask"})
    db.commit()
    assert browser.get("/dashboard", follow_redirects=False).headers["location"] == "/account/consent"
    assert "Before you continue" in browser.get("/account/consent").text
    assert "privacy notice" in browser.post("/account/consent", data={"minor": "no"}).text
    r = browser.post("/account/consent", data={"minor": "no", "privacy": "on"}, follow_redirects=False)
    assert r.headers["location"] == "/dashboard" and browser.get("/dashboard").status_code == 200


def test_google_sign_in_new_and_linked(db, monkeypatch):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "cid")
    monkeypatch.setattr(s, "google_client_secret", "secret")
    b = Browser()
    assert "Continue with Google" in b.get("/login").text
    r = b.get("/auth/google?next=/drill", follow_redirects=False)
    assert r.headers["location"].startswith(google_auth.AUTH_URL) and google_auth.FLOW_COOKIE in r.headers["set-cookie"]
    claims = {"sub": "g-1", "email": "New@Gmail.test", "name": "Neha"}
    monkeypatch.setattr(google_auth, "finish", lambda code, state, cookie: (claims, "/drill"))
    r = b.get("/auth/google/callback?code=x&state=y", follow_redirects=False)
    assert r.headers["location"] == "/register/google"
    assert "new@gmail.test" in b.get("/register/google").text
    r = b.post("/register/google", data={"name": "Neha", "minor": "no", "privacy": "on"}, follow_redirects=False)
    assert r.headers["location"] == "/dashboard"
    u = db.scalar(select(User).where(User.google_sub == "g-1"))
    assert (u.email, u.password_hash, u.privacy_accepted) == ("new@gmail.test", NO_PASSWORD, s.privacy_notice_version)
    # an email account with the same (Google-verified) address gets linked, and its password stops working
    Browser().register(email="same@x.test")
    claims.update(sub="g-2", email="same@x.test")
    b2 = Browser()
    assert b2.get("/auth/google/callback?code=x&state=y", follow_redirects=False).headers["location"] == "/drill"
    old = db.scalar(select(User).where(User.email == "same@x.test"))
    db.refresh(old)
    assert old.google_sub == "g-2" and old.password_hash == NO_PASSWORD
    r = Browser().c.post("/login", data={"email": "same@x.test", "password": "password-123"})
    assert "signs in with Google" in r.text
    assert "cancelled" in Browser().get("/auth/google/callback?error=access_denied").text


def test_phone_sign_up_adults_only_and_stub_safety(db, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "tekurious_api_key", "k")
    monkeypatch.setattr(phone_auth, "countries", lambda: [{"dialCode": "+91", "name": "India"}])
    sent = []
    monkeypatch.setattr(phone_auth, "send_code", lambda phone, signup=None: sent.append((phone, signup)) or "req-1")
    monkeypatch.setattr(phone_auth, "verify_code", lambda req, otp: {"id": "t-1", "phone": "+919876543210",
                                                                     "displayName": "Arjun", "isNew": True})
    b = Browser()
    assert "Text me a code" in b.get("/login").text
    assert "country code" in b.post("/login/phone", data={"dial": "+91", "number": "12"}).text
    assert "I am 18 or over" in b.post("/login/phone", data={"dial": "+91", "number": "98765 43210"}).text
    assert "aged 18 or over" in b.post("/login/phone/signup", data={"name": "Arjun", "terms": "on"}).text
    assert "Enter the code" in b.post("/login/phone/signup", data={"name": "Arjun", "adult": "on", "terms": "on"}).text
    assert sent[-1] == ("+919876543210", {"displayName": "Arjun"})
    r = b.post("/login/phone/verify", data={"otp": "123 456"}, follow_redirects=False)
    assert r.headers["location"] == "/dashboard"
    u = db.scalar(select(User).where(User.phone == "+919876543210"))
    assert (u.email, u.name, u.tekurious_user_id, u.guardian_consent_status) == (None, "Arjun", "t-1", "not_required")
    assert b.get("/profile").status_code == 200 and "+919876543210" in b.get("/profile").text
    # the service's test mode must never open an existing account
    monkeypatch.setattr(phone_auth, "send_code", lambda phone, signup=None: "stub-9")
    b2 = Browser()
    b2.post("/login/phone", data={"dial": "+91", "number": "9876543210"})
    assert "still in testing" in b2.post("/login/phone/verify", data={"otp": "123456"}).text


def test_sign_in_buttons_hidden_without_keys_and_account_deletion(browser, db):
    page = Browser().get("/login").text
    assert "Continue with Google" not in page and "Text me a code" not in page
    assert "not set up" in Browser().get("/auth/google").text
    assert "What JEE Workbench keeps" in Browser().get("/privacy").text
    browser.register()
    assert "Delete my account" in browser.get("/profile").text
    browser.post("/account/delete")
    assert db.scalar(select(User)) is None
    admin = Browser()
    admin.register(email="boss@x.test")
    db.query(User).update({"role": Role.admin})
    db.commit()
    admin.post("/account/delete")
    assert db.scalar(select(User)) is not None  # admins can't delete themselves here
