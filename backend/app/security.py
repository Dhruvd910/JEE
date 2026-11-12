"""Password hashing (stdlib scrypt), signed session cookie, CSRF, rate limiting. Same design as NEET Compass."""
import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User

COOKIE_NAME = "jee_session"
CSRF_HEADER = "x-csrf-token"
_SCRYPT = dict(n=2**14, r=8, p=1, dklen=64)
NO_PASSWORD = "no-password"  # accounts created by the NEET | JEE admin switch: never matches any password


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


def create_token(user: User) -> str:
    s = get_settings()
    payload = {"sub": str(user.id), "exp": datetime.now(timezone.utc) + timedelta(minutes=s.jwt_expiry_minutes)}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def user_from_request(request: Request, db: Session) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return db.get(User, int(payload["sub"]))


def csrf_token_for(session_token: str) -> str:
    """Signed double-submit token bound to the session, so a forged cookie is useless."""
    return hmac.new(get_settings().jwt_secret.encode(), b"csrf:" + session_token.encode(), hashlib.sha256).hexdigest()


def csrf_ok(request: Request, form_token: str | None = None) -> bool:
    """Unsafe requests made with a session cookie must echo the CSRF token (HTMX header or form field)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    session = request.cookies.get(COOKIE_NAME)
    if not session:
        return True
    sent = request.headers.get(CSRF_HEADER) or form_token or ""
    return bool(sent) and hmac.compare_digest(sent, csrf_token_for(session))


class RateLimiter:
    """In-memory sliding-window limiter, per process."""

    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


rate_limiter = RateLimiter()
