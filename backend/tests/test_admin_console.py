"""Shared admin console: admin sign-in, the NEET | JEE switcher and the signed hand-off."""
from app import admin_bridge
from app.config import get_settings
from app.models import Role, User
from tests.conftest import Browser


def test_token_rules():
    t = admin_bridge.make("s3cret", "A@x.test", "jee", now=1000)
    assert admin_bridge.read("s3cret", t, "jee", now=1010) == "a@x.test"
    assert admin_bridge.read("s3cret", t, "jee", now=1010) is None  # single use
    t = admin_bridge.make("s3cret", "a@x.test", "jee", now=1000)
    assert admin_bridge.read("s3cret", t, "neet", now=1010) is None  # wrong site
    assert admin_bridge.read("other", t, "jee", now=1010) is None  # wrong secret
    assert admin_bridge.read("s3cret", t, "jee", now=1100) is None  # expired
    assert admin_bridge.read("", t, "jee") is None and admin_bridge.read("s3cret", "x.y", "jee") is None


def _admin(db, email="boss@x.test"):
    b = Browser()
    b.register(email=email, name="Boss")
    db.query(User).filter_by(email=email).update({"role": Role.admin})
    db.commit()
    return b


def test_admin_login_page(db):
    b = Browser()
    b.register(email="stu@x.test")
    r = b.c.post("/admin/login", data={"email": "stu@x.test", "password": "password-123"})
    assert r.status_code == 401 and "not an admin" in r.text  # students can't use the admin sign-in
    _admin(db)
    r = Browser().c.post("/admin/login", data={"email": "boss@x.test", "password": "password-123"}, follow_redirects=False)
    assert r.headers["location"] == "/admin"
    page = Browser().get("/admin/login").text
    assert "exam-switch" in page and "Admin <span" in page
    assert 'href="/admin/login"' in Browser().get("/login").text  # Student | Admin choice on the student sign-in


def test_switcher_without_secret_is_a_plain_link(db, monkeypatch):
    b = _admin(db)
    page = b.get("/admin").text
    assert 'href="/admin/switch/neet"' in page and "on-jee" in page
    monkeypatch.setattr(get_settings(), "admin_bridge_secret", "")
    r = b.get("/admin/switch/neet", follow_redirects=False)
    assert r.headers["location"] == "http://localhost:8765/admin"


def test_switcher_with_secret_signs_in_on_the_other_site(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_bridge_secret", "shared-secret")
    b = _admin(db)
    r = b.get("/admin/switch/neet", follow_redirects=False)
    token = r.headers["location"].split("?t=")[1]
    assert r.headers["location"].startswith("http://localhost:8765/admin/bridge?t=")
    assert admin_bridge.read("shared-secret", token.replace("%3D", "="), "neet") == "boss@x.test"
    # arriving from NEET: a token for this site signs the same-email admin in, once
    fresh = Browser()
    t = admin_bridge.make("shared-secret", "boss@x.test", "jee")
    r = fresh.get(f"/admin/bridge?t={t}", follow_redirects=False)
    assert r.headers["location"] == "/admin" and "Health checklist" in fresh.get("/admin").text
    assert Browser().get(f"/admin/bridge?t={t}", follow_redirects=False).headers["location"] == "/admin/login"
    # a valid token for a non-admin account here gets nothing
    s = Browser()
    s.register(email="stu@x.test")
    t = admin_bridge.make("shared-secret", "stu@x.test", "jee")
    assert Browser().get(f"/admin/bridge?t={t}", follow_redirects=False).headers["location"] == "/admin/login"


def test_neet_admin_arriving_first_time_becomes_jee_admin(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_bridge_secret", "shared-secret")
    t = admin_bridge.make("shared-secret", "neet.admin@x.test", "jee")
    b = Browser()
    assert b.get(f"/admin/bridge?t={t}", follow_redirects=False).headers["location"] == "/admin"
    u = db.query(User).filter_by(email="neet.admin@x.test").one()
    assert u.role == Role.admin and u.password_hash == "no-password"
    assert "Health checklist" in b.get("/admin").text
    # no password was set, so the form refuses it until create-admin sets one
    r = Browser().c.post("/admin/login", data={"email": "neet.admin@x.test", "password": "no-password"})
    assert r.status_code == 401


def test_create_admin_command(db, monkeypatch):
    from app import maintenance
    answers = iter(["long-enough-pass-1", "long-enough-pass-1"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert maintenance.create_admin(db, "Head@x.test") == "head@x.test is now an admin."
    r = Browser().c.post("/admin/login", data={"email": "head@x.test", "password": "long-enough-pass-1"}, follow_redirects=False)
    assert r.headers["location"] == "/admin"
