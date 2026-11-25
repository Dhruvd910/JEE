"""Google and phone sign-in, guardian consent for under-18s, and the privacy notice. Email sign-up and sign-in stay
in routes.py. Same flows as NEET Compass; the keys (Google client, Tekurious) live in .env / .env.live only."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import get_settings
from app.models import Role, User
from app.security import COOKIE_NAME, NO_PASSWORD
from app.services import accounts, google_auth, phone_auth
from app.web.core import Page, page
from app.web.routes import _safe_next, _signed_in

router = APIRouter(include_in_schema=False)
PHONE_COOKIE = "jee_phone_flow"


def _login_page(p: Page, status_code: int = 400, **ctx):
    return p.render("auth/login.html", status_code=status_code, next=ctx.pop("next", "/dashboard"),
                    email=ctx.pop("email", ""), error=ctx.pop("error", None), **ctx)


def _clear_flows(resp):
    for c in (google_auth.FLOW_COOKIE, google_auth.PENDING_COOKIE, PHONE_COOKIE):
        resp.delete_cookie(c, path="/")
    return resp


# ---------------------------------------------------------------- Google

@router.get("/auth/google")
def google_start(next: str = "/dashboard", p: Page = Depends(page)):
    if not get_settings().google_enabled:
        return _login_page(p, 503, error="Google sign-in is not set up yet.")
    url, flow = google_auth.start(_safe_next(next))
    resp = RedirectResponse(url, status_code=303)
    resp.set_cookie(google_auth.FLOW_COOKIE, flow, max_age=600, httponly=True, samesite="lax",
                    secure=get_settings().cookie_secure, path="/")
    return resp


@router.get("/auth/google/callback")
def google_callback(code: str = "", state: str = "", error: str = "", p: Page = Depends(page)):
    if error or not code:
        return _login_page(p, error="Google sign-in was cancelled.")
    try:
        claims, target = google_auth.finish(code, state, p.request.cookies.get(google_auth.FLOW_COOKIE))
    except google_auth.GoogleAuthError as e:
        return _login_page(p, error=str(e))
    user = accounts.google_user(p.db, claims)
    if user is not None:
        return _clear_flows(_signed_in(p, user, _safe_next(target)))
    # New here: Google says who, not whether they are under 18. Ask before creating the account.
    pending = google_auth.sign({"purpose": "google-signup", "sub": claims["sub"], "email": claims["email"].lower(),
                                "name": claims.get("name") or ""}, 15)
    resp = RedirectResponse("/register/google", status_code=303)
    resp.set_cookie(google_auth.PENDING_COOKIE, pending, max_age=900, httponly=True, samesite="lax",
                    secure=get_settings().cookie_secure, path="/")
    resp.delete_cookie(google_auth.FLOW_COOKIE, path="/")
    return resp


def _pending(p: Page) -> dict | None:
    return google_auth.unsign(p.request.cookies.get(google_auth.PENDING_COOKIE), "google-signup")


@router.get("/register/google")
def google_signup_form(p: Page = Depends(page)):
    g = _pending(p)
    if g is None:
        return RedirectResponse("/login", status_code=303)
    return p.render("auth/register_google.html", g=g, form={"name": g.get("name", "")}, error=None)


@router.post("/register/google")
def google_signup(name: str = Form(""), minor: str = Form(""), guardian_email: str = Form(""),
                  privacy: str | None = Form(None), p: Page = Depends(page)):
    g = _pending(p)
    if g is None:
        return _login_page(p, error="That took too long. Please continue with Google again.")
    name = " ".join(name.split())[:120]
    form = {"name": name, "minor": minor, "guardian_email": guardian_email}
    error = "Please enter your name." if len(name) < 2 else accounts.age_answer(minor, guardian_email, g["email"],
                                                                                privacy is not None)
    if error is None and p.db.scalar(select(User).where((User.email == g["email"]) | (User.google_sub == g["sub"]))):
        error = "An account with this email already exists. Sign in instead."
    if error:
        return p.render("auth/register_google.html", status_code=400, g=g, form=form, error=error)
    user = User(email=g["email"], name=name, google_sub=g["sub"], password_hash=NO_PASSWORD)
    p.db.add(user)
    accounts.apply_age(p.db, user, minor == "yes", guardian_email)
    p.db.commit()
    return _clear_flows(_signed_in(p, user, "/dashboard"))


# ---------------------------------------------------------------- phone (SMS code); 18+ only, like NEET Compass

def _flow(p: Page) -> dict | None:
    return google_auth.unsign(p.request.cookies.get(PHONE_COOKIE), "phone-flow")


def _with_flow(resp, flow: dict):
    resp.set_cookie(PHONE_COOKIE, google_auth.sign({**flow, "purpose": "phone-flow"}, 10), max_age=600, httponly=True,
                    samesite="lax", secure=get_settings().cookie_secure, path="/")
    return resp


def _code_page(p: Page, flow: dict, error: str | None = None, status_code: int = 200):
    wait = max(0, int(flow.get("sent_at", 0) + get_settings().otp_resend_seconds - time.time()))
    return p.render("auth/phone_code.html", status_code=status_code, phone=flow["phone"], error=error, wait=wait,
                    test_mode=flow.get("stub", False))


def _send(p: Page, flow: dict):
    try:
        flow["req"] = phone_auth.send_code(flow["phone"], {"displayName": flow["name"]} if flow.get("name") else None)
    except phone_auth.PhoneAuthError as e:
        return _login_page(p, next=flow.get("next", "/dashboard"), phone_error=str(e), number=flow["phone"])
    flow["sent_at"], flow["stub"] = int(time.time()), flow["req"].startswith("stub-")
    return _with_flow(_code_page(p, flow), flow)


@router.post("/login/phone")
def phone_start(dial: str = Form("+91"), number: str = Form(...), next: str = Form("/dashboard"), p: Page = Depends(page)):
    if not get_settings().phone_enabled:
        return _login_page(p, 503, error="Phone sign-in is not set up yet.")
    phone = phone_auth.normalize(dial, number)
    if not phone_auth.valid(phone):
        return _login_page(p, next=next, number=number,
                           phone_error="Enter your mobile number with its country code, e.g. 98765 43210 for India.")
    flow = {"phone": phone, "next": _safe_next(next)}
    if p.db.scalar(select(User).where(User.phone == phone)) is None:
        return _with_flow(p.render("auth/phone_signup.html", phone=phone, form={}, error=None), flow)
    return _send(p, flow)


@router.post("/login/phone/signup")
def phone_signup(name: str = Form(""), adult: str | None = Form(None), terms: str | None = Form(None),
                 p: Page = Depends(page)):
    flow = _flow(p)
    if flow is None:
        return RedirectResponse("/login", status_code=303)
    name = " ".join(name.split())[:120]
    error = ("Please enter your name." if len(name) < 2 else
             "Phone sign-up is for students aged 18 or over. Under 18? Use email or Google sign-up so a parent can "
             "give consent." if adult is None else "Please accept the privacy notice." if terms is None else None)
    if error:
        return p.render("auth/phone_signup.html", status_code=400, phone=flow["phone"], error=error,
                        form={"name": name, "adult": adult, "terms": terms})
    return _send(p, {**flow, "name": name, "consented": True})


@router.post("/login/phone/resend")
def phone_resend(p: Page = Depends(page)):
    flow = _flow(p)
    if flow is None:
        return RedirectResponse("/login", status_code=303)
    if time.time() < flow.get("sent_at", 0) + get_settings().otp_resend_seconds:
        return _code_page(p, flow, error="Please wait a little before asking for another code.", status_code=429)
    return _send(p, flow)


@router.post("/login/phone/verify")
def phone_verify(otp: str = Form(...), p: Page = Depends(page)):
    flow = _flow(p)
    if flow is None or not flow.get("req"):
        return _login_page(p, phone_error="That took too long. Please enter your number again.")
    try:
        who = phone_auth.verify_code(flow["req"], "".join(ch for ch in otp if ch.isdigit()))
    except phone_auth.PhoneAuthError as e:
        if e.code == "SIGNUP_REQUIRED":
            return _with_flow(p.render("auth/phone_signup.html", phone=flow["phone"], form={}, error=None), flow)
        if e.code == "PENDING_APPROVAL":
            return _login_page(p, 403, phone_error="This number is waiting for a parent's approval.")
        return _code_page(p, flow, error=str(e) if e.code in ("RATE_LIMITED", "DISABLED")
                          else "That code didn't work. Check it, or ask for a new code.", status_code=400)
    user = p.db.scalar(select(User).where(User.tekurious_user_id == who["id"]))
    user = user or p.db.scalar(select(User).where(User.phone == who["phone"]))
    if user is not None and flow.get("stub"):
        # Test mode accepts one code for every number, so it must never open an account that already exists.
        return _login_page(p, 403, phone_error="Phone sign-in is still in testing, so it can't open an existing "
                                               "account yet. Use email or Google instead.")
    if user is None:
        if not flow.get("consented"):
            return _with_flow(p.render("auth/phone_signup.html", phone=flow["phone"], form={}, error=None), flow)
        user = User(phone=who["phone"], tekurious_user_id=who["id"], name=flow.get("name") or who.get("displayName")
                    or "Student", password_hash=NO_PASSWORD, privacy_accepted=get_settings().privacy_notice_version)
        p.db.add(user)
    user.tekurious_user_id = user.tekurious_user_id or who["id"]
    user.phone = user.phone or who["phone"]
    p.db.commit()
    return _clear_flows(_signed_in(p, user, flow.get("next") or "/dashboard"))


# ---------------------------------------------------------------- the age question and guardian consent

def _status_page(p: Page, status_code: int = 200, **ctx):
    ctx.setdefault("form", {})
    ctx.setdefault("error", None)
    ctx.setdefault("msg", None)
    return p.render("auth/consent_status.html", status_code=status_code, u=p.user,
                    masked=accounts.mask(p.user.guardian_email or ""), **ctx)


@router.get("/account/consent")
def consent_status(p: Page = Depends(page)):
    if p.user is None:
        return RedirectResponse("/login", status_code=303)
    if accounts.has_consent(p.user):
        return RedirectResponse("/dashboard", status_code=303)
    return _status_page(p)


@router.post("/account/consent")
def consent_answer(minor: str = Form(""), guardian_email: str = Form(""), privacy: str | None = Form(None),
                   p: Page = Depends(page)):
    """The age question for accounts made before it existed (status "ask")."""
    if p.user is None:
        return RedirectResponse("/login", status_code=303)
    if p.user.guardian_consent_status != "ask":
        return RedirectResponse("/account/consent", status_code=303)
    error = accounts.age_answer(minor, guardian_email, p.user.email, privacy is not None)
    if error:
        return _status_page(p, 400, error=error, form={"minor": minor, "guardian_email": guardian_email})
    accounts.apply_age(p.db, p.user, minor == "yes", guardian_email)
    p.db.commit()
    return RedirectResponse("/account/consent" if minor == "yes" else "/dashboard", status_code=303)


@router.post("/account/consent/resend")
def consent_resend(p: Page = Depends(page)):
    if p.user is None or p.user.guardian_consent_status != "pending":
        return RedirectResponse("/account/consent", status_code=303)
    accounts.request_consent(p.db, p.user)
    p.db.commit()
    return _status_page(p, msg="Sent again. Ask your parent or guardian to check their inbox (and spam folder).")


@router.post("/account/delete")
def delete_account(p: Page = Depends(page)):
    """A student deletes their account and everything in it (tests, plans, chats are removed with it)."""
    if p.user is not None and p.user.role != Role.admin:
        p.db.delete(p.user)
        p.db.commit()
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.get("/guardian-consent")
def guardian_form(token: str = "", p: Page = Depends(page)):
    user = accounts.user_for_token(p.db, token)
    if user is None:
        return p.render("auth/guardian_consent.html", status_code=404, info=None, token="", done=None, error=None)
    return p.render("auth/guardian_consent.html", info={"student": user.name}, token=token, done=None, error=None)


@router.post("/guardian-consent")
def guardian_decide(token: str = Form(""), decision: str = Form(""), guardian_name: str = Form(""),
                    relationship: str = Form(""), confirm: str | None = Form(None), p: Page = Depends(page)):
    user = accounts.user_for_token(p.db, token)
    if user is None:
        return p.render("auth/guardian_consent.html", status_code=404, info=None, token="", done=None, error=None)
    name, rel = " ".join(guardian_name.split())[:120], " ".join(relationship.split())[:40]
    error = ("Please choose approve or decline." if decision not in ("approve", "decline") else
             "Please enter your name and relationship to the student." if len(name) < 2 or len(rel) < 2 else
             "Please confirm that you are the student's parent or legal guardian." if confirm is None else None)
    if error:
        return p.render("auth/guardian_consent.html", status_code=400, info={"student": user.name}, token=token,
                        done=None, error=error)
    accounts.decide(p.db, user, decision == "approve", name, rel)
    return p.render("auth/guardian_consent.html", info=None, token="", done=user.guardian_consent_status, error=None)


@router.get("/privacy")
def privacy(p: Page = Depends(page)):
    s = get_settings()
    return p.render("privacy.html", version=s.privacy_notice_version, contact=s.contact_email,
                    days=s.unconsented_minor_retention_days)
