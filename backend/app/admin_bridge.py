"""Admin hand-off between the NEET Compass and JEE Workbench consoles (this file is identical in both apps).

When both sites share ADMIN_BRIDGE_SECRET, the NEET | JEE switcher sends the admin to the other console with a
signed link that names their email, the target site, and expires after 60 seconds. The target site accepts it once,
and only for an account there with the same email that is already an admin there. Without a secret the switcher
is a plain link and the admin signs in on the other site.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

TTL_SECONDS = 60
_used: dict[str, float] = {}  # nonce -> expiry; shortcut: per process, use Redis if the site runs several workers


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make(secret: str, email: str, audience: str, now: float | None = None) -> str:
    body = _b64(json.dumps({"email": email.lower(), "aud": audience, "exp": int((now or time.time()) + TTL_SECONDS),
                            "nonce": secrets.token_urlsafe(12)}).encode())
    return body + "." + _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def read(secret: str, token: str, audience: str, now: float | None = None) -> str | None:
    """The admin's email if the token is genuine, meant for this site, unexpired and unused; otherwise None."""
    if not secret or not token or token.count(".") != 1:
        return None
    body, sig = token.split(".")
    want = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, want):
        return None
    try:
        data = json.loads(_unb64(body))
    except ValueError:
        return None
    now = now or time.time()
    if data.get("aud") != audience or data.get("exp", 0) < now or data.get("nonce") in _used:
        return None
    for n, exp in list(_used.items()):
        if exp < now:
            del _used[n]
    _used[data["nonce"]] = data["exp"]
    return data.get("email")
