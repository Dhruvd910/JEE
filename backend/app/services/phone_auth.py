"""Phone sign-in with an SMS code, through Tekurious AI-Auth (https://api.tekurious.si).

All calls are made by this server, so the app-client key (TEKURIOUS_API_KEY) never reaches a browser.

Flow (checked against the live API):
  POST /v1/otp/send    {phone[, displayName, isAdult, acceptTerms, acceptPrivacy]} -> {reqId}
                       New users must send the four sign-up fields, or verify fails with 403 SIGNUP_REQUIRED.
  POST /v1/otp/verify  {reqId, otp} -> {userId, isNew, phone, sessionToken, refreshToken} or {pendingSignupId}
  GET  /v1/me          (Bearer sessionToken) -> {id, displayName, phone, email, ...}

After verify we read /v1/me once and then use our own session cookie. Tekurious's tokens are not kept.
Under-18 sign-up stays on the email + guardian-consent flow until Tekurious's child login works.
"""
from __future__ import annotations

import re
import time

import httpx

from app.config import get_settings

COUNTRIES_TTL = 24 * 3600  # the list rarely changes; a stale copy is fine
TEST_CODE = "123456"  # the code the service accepts in its test ("stub") mode
_countries: tuple[float, list[dict]] = (0.0, [])


class PhoneAuthError(Exception):
    def __init__(self, message: str, *, code: str | None = None, retry_after: int | None = None):
        super().__init__(message)
        self.code, self.retry_after = code, retry_after


def _call(method: str, path: str, *, json: dict | None = None, token: str | None = None, timeout: float = 20) -> dict:
    s = get_settings()
    headers = {"x-api-key": s.tekurious_api_key}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = httpx.request(method, f"{s.tekurious_base_url.rstrip('/')}{path}", json=json, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise PhoneAuthError("The phone sign-in service is not reachable right now. Please try again shortly.") from e
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        if r.status_code == 429:
            wait = int(body.get("retryAfterSec") or r.headers.get("Retry-After") or 60)
            raise PhoneAuthError(f"Too many codes requested. Please wait {max(1, round(wait / 60))} minute(s) and try again.",
                                 code="RATE_LIMITED", retry_after=wait)
        raise PhoneAuthError(body.get("message") or f"Phone sign-in failed ({r.status_code}).",
                             code=body.get("code") or str(r.status_code))
    return body


def countries() -> list[dict]:
    """Countries whose numbers the service accepts (cached for an hour); [] if it can't be reached."""
    global _countries
    at, items = _countries
    if items and time.time() - at < COUNTRIES_TTL:
        return items
    try:
        items = _call("GET", "/v1/countries/allowed", timeout=4).get("items") or []  # never hold up the sign-in page
    except PhoneAuthError:
        return items
    _countries = (time.time(), items)
    return items


def normalize(dial_code: str, number: str) -> str:
    """E.164 (+919876543210) from a country code and what the student typed (spaces, dashes, a leading 0)."""
    digits = re.sub(r"\D", "", number or "")
    dial = "+" + re.sub(r"\D", "", dial_code or "")
    if (number or "").strip().startswith("+"):
        return "+" + digits
    if digits.startswith(dial[1:]) and len(digits) > 10:
        digits = digits[len(dial) - 1:]
    return dial + digits.lstrip("0")


def valid(phone: str) -> bool:
    return bool(re.fullmatch(r"\+[1-9]\d{7,14}", phone))


def send_code(phone: str, signup: dict | None = None) -> str:
    """Texts a code; returns reqId. `signup` = {"displayName": ...} for new users (they confirm 18+ and terms)."""
    body = {"phone": phone}
    if signup:
        body.update(displayName=signup["displayName"], isAdult=True, acceptTerms=True, acceptPrivacy=True)
    req = _call("POST", "/v1/otp/send", json=body).get("reqId")
    if not req:
        raise PhoneAuthError("The phone sign-in service did not start the check. Please try again.")
    if req.startswith("stub-") and not get_settings().phone_allow_stub:
        raise PhoneAuthError("Phone sign-in isn't switched on yet. Please use email sign-in for now.", code="STUB_MODE")
    return req


def verify_code(req_id: str, otp: str) -> dict:
    """Checks the code. Returns Tekurious's profile ({id, displayName, phone, ...}) for a signed-in user."""
    result = _call("POST", "/v1/otp/verify", json={"reqId": req_id, "otp": otp})
    if result.get("pendingSignupId"):
        raise PhoneAuthError("This number is waiting for a parent's approval.", code="PENDING_APPROVAL")
    token = result.get("sessionToken")
    if not token:
        raise PhoneAuthError("The code could not be confirmed. Please try again.")
    me = _call("GET", "/v1/me", token=token)
    if me.get("disabled"):
        raise PhoneAuthError("This account has been disabled.", code="DISABLED")
    return {"id": me.get("id") or result.get("userId"), "phone": me.get("phone") or result.get("phone"),
            "displayName": me.get("displayName"), "isNew": bool(result.get("isNew"))}
