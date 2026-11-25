"""Sign in with Google (OpenID Connect, authorization-code flow with PKCE).

Flow: /auth/google sends the visitor to Google with a random `state`, `nonce` and PKCE challenge, kept in a short,
signed cookie. Google returns to /auth/google/callback with a code, which the server exchanges for an ID token.

The ID token comes straight from Google's token endpoint over HTTPS, authenticated with our client secret, so
OpenID Connect (Core 1.0, §3.1.3.7) lets us rely on TLS instead of checking its signature. We still check the
audience, issuer, expiry, nonce and that Google has verified the email address.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse

import httpx
import jwt

from app.config import get_settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
FLOW_COOKIE = "jee_google_flow"
PENDING_COOKIE = "jee_google_signup"


class GoogleAuthError(Exception):
    pass


def redirect_uri() -> str:
    s = get_settings()
    return s.google_redirect_uri or s.public_app_url.rstrip("/") + "/auth/google/callback"


def sign(payload: dict, minutes: int) -> str:
    """Short-lived, tamper-proof cookie value (HS256 with the app's secret)."""
    return jwt.encode({**payload, "exp": int(time.time()) + minutes * 60}, get_settings().jwt_secret, algorithm="HS256")


def unsign(token: str | None, purpose: str) -> dict | None:
    if not token:
        return None
    try:
        data = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return data if data.get("purpose") == purpose else None


def start(next_url: str) -> tuple[str, str]:
    """Returns (Google URL to send the visitor to, signed flow cookie)."""
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {"client_id": get_settings().google_client_id, "redirect_uri": redirect_uri(), "response_type": "code",
              "scope": "openid email profile", "state": state, "nonce": nonce, "code_challenge": challenge,
              "code_challenge_method": "S256", "prompt": "select_account"}
    flow = sign({"purpose": "google-flow", "state": state, "nonce": nonce, "verifier": verifier, "next": next_url}, 10)
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}", flow


def finish(code: str, state: str, flow_cookie: str | None) -> tuple[dict, str]:
    """Checks the callback and returns (verified Google claims, the page to continue to)."""
    flow = unsign(flow_cookie, "google-flow")
    if flow is None or not secrets.compare_digest(flow["state"], state or ""):
        raise GoogleAuthError("The sign-in link expired or was opened in another browser. Please try again.")
    s = get_settings()
    try:
        r = httpx.post(TOKEN_URL, data={"code": code, "client_id": s.google_client_id,
                                        "client_secret": s.google_client_secret, "redirect_uri": redirect_uri(),
                                        "grant_type": "authorization_code", "code_verifier": flow["verifier"]},
                       timeout=20)
    except httpx.HTTPError as e:
        raise GoogleAuthError("Could not reach Google. Please try again.") from e
    if r.status_code != 200 or "id_token" not in r.json():
        raise GoogleAuthError("Google did not accept the sign-in. Please try again.")
    claims = jwt.decode(r.json()["id_token"], options={"verify_signature": False})  # see module docstring
    if claims.get("aud") != s.google_client_id or claims.get("iss") not in ISSUERS:
        raise GoogleAuthError("This sign-in was not meant for JEE Workbench.")
    if claims.get("exp", 0) < time.time() or claims.get("nonce") != flow["nonce"]:
        raise GoogleAuthError("The sign-in expired. Please try again.")
    if not claims.get("email") or not claims.get("email_verified"):
        raise GoogleAuthError("Your Google account's email address is not verified.")
    return claims, flow.get("next") or "/dashboard"
