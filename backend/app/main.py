import logging
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.security import client_ip, csrf_ok, rate_limiter
from app.web import core
from app.web.routes import router
from app.web.routes_admin import router as admin_router

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="JEE Workbench", docs_url=None, redoc_url=None, openapi_url=None)
CSRF_EXEMPT = {"/login", "/register", "/admin/login"}  # they start a new session; a stale cookie must not block them


@app.middleware("http")
async def security(request: Request, call_next):
    auth = request.method == "POST" and request.url.path in CSRF_EXEMPT
    bucket, limit = ("auth", settings.rate_limit_auth) if auth else ("default", settings.rate_limit_default)
    if not rate_limiter.check(f"{bucket}:{client_ip(request)}", limit):
        return JSONResponse({"detail": "Too many requests — wait a minute and try again."}, status_code=429)
    if request.url.path not in CSRF_EXEMPT and not csrf_ok(request):
        return HTMLResponse("Security check failed (CSRF). Reload the page and try again.", status_code=403)
    response = await call_next(request)
    response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                             "Referrer-Policy": "same-origin"})
    return response


@app.exception_handler(core.LoginRequired)
async def login_required(request: Request, exc: core.LoginRequired):
    # Admin pages send people to the admin sign-in; everything else to the student sign-in.
    url = "/admin/login" if exc.next_url.startswith("/admin") else "/login?next=" + quote(exc.next_url)
    if request.headers.get("hx-request"):
        return HTMLResponse("", headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


@app.exception_handler(core.AdminRequired)
async def admin_required(request: Request, exc: core.AdminRequired):
    # Signed in, but not as an admin: offer the admin sign-in (a student account stays signed in).
    if request.headers.get("hx-request"):
        return HTMLResponse("", headers={"HX-Redirect": "/admin/login"})
    return RedirectResponse("/admin/login", status_code=303)


app.mount("/static", StaticFiles(directory=core.WEB_DIR / "static"), name="static")
app.include_router(admin_router)
app.include_router(router)
