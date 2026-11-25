"""Jinja2 + HTMX website helpers, served by the same FastAPI app (pattern from NEET Compass)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import re

from fastapi import Depends, Request
from markupsafe import Markup, escape
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Role, User
from app.security import COOKIE_NAME, csrf_token_for, user_from_request

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")
SUBJECT_CODE = {"Physics": "PHY", "Chemistry": "CHE", "Mathematics": "MAT"}
# (url, label, icon); the first group is study, the second counselling
NAV = [[("/dashboard", "Dashboard", "grid"), ("/practice", "Practice", "target"), ("/drill", "Drill", "zap"),
        ("/mistakes", "Mistakes", "alert"), ("/syllabus", "Syllabus", "book"), ("/analytics", "Analytics", "chart"), ("/mock-tests", "Mock log", "list"),
        ("/study-plan", "Study plan", "calendar"), ("/assistant", "AI Assistant", "chat")],
       [("/jee", "JEE info", "file"), ("/counselling", "Counselling", "route"), ("/colleges", "Colleges", "building"), ("/compare", "Compare", "scale"), ("/predictor", "Predictor", "compass"),
        ("/choices", "Choice list", "list"), ("/careers", "Careers", "star")]]
STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Delhi", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand", "Karnataka", "Kerala", "Ladakh", "Madhya Pradesh",
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Puducherry", "Punjab", "Rajasthan",
    "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Lakshadweep",
]


_INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\n]+\*)")
_BULLET = re.compile(r"^\s*[-•*]\s+")


def chat_text(s: str) -> Markup:
    """Small, safe markdown for assistant replies: **bold**, *italic*, bullet lines, line breaks. The rest is escaped."""
    def inline(t: str) -> str:
        out = []
        for part in _INLINE.split(t):
            if part.startswith("**") and part.endswith("**") and len(part) > 4:
                out.append(f"<strong>{escape(part[2:-2])}</strong>")
            elif part.startswith("*") and part.endswith("*") and len(part) > 2:
                out.append(f"<em>{escape(part[1:-1])}</em>")
            else:
                out.append(str(escape(part)))
        return "".join(out)

    html, table = [], []

    def flush_table():
        """Markdown table rows (| a | b |) as a real table; the |---| separator row is dropped."""
        if not table:
            return
        rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in table
                if not re.fullmatch(r"[\s|:\-]+", r)]
        head, body = rows[0], rows[1:]
        html.append('<div class="my-1 overflow-x-auto"><table class="data !text-xs"><thead><tr>'
                    + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                    + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body)
                    + "</tbody></table></div>")
        table.clear()

    for line in s.split("\n"):
        if line.strip().startswith("|"):
            table.append(line)
            continue
        flush_table()
        line = re.sub(r"^\s*>\s?", "", line)  # quoted notes read as plain text
        if not line.strip():
            html.append('<span class="block h-2"></span>')
        elif _BULLET.match(line):
            html.append('<p class="flex gap-2 pl-1"><span class="mt-[0.55em] h-1.5 w-1.5 shrink-0 rounded-full bg-cyan-300"></span>'
                        f"<span>{inline(_BULLET.sub('', line))}</span></p>")
        else:
            html.append(f"<p>{inline(line.lstrip('#').strip())}</p>")
    flush_table()
    return Markup("".join(html))


def when(v: datetime | None) -> str:
    if not v:
        return "—"
    return f"{v:%Y-%m-%d %H:%M} UTC"


def num(v) -> str:
    return "—" if v is None else f"{v:,}"


def letters(idx: list | None, main: bool = False) -> str:
    """Option indexes as the paper prints them: (1)-(4) for JEE Main, A-D for Advanced."""
    if not idx:
        return "—"
    return ", ".join(str(i + 1) for i in idx) if main else "".join("ABCD"[i] for i in idx)


def answer_text(answer: dict | None, main: bool) -> str:
    if not answer:
        return "—"
    if "lo" in answer:
        lo, hi = answer["lo"], answer["hi"]
        f = lambda x: f"{x:g}"  # noqa: E731
        return "—" if lo is None else f(lo) if lo == hi else f"{f(lo)} to {f(hi)}"
    return " or ".join(letters(s, main) for s in answer.get("sets", [])) or "dropped"


def response_text(resp: list | None, qtype: str, main: bool) -> str:
    if not resp:
        return "not answered"
    return str(resp[0]) if qtype == "num" else letters(sorted(int(i) for i in resp), main)


templates.env.filters.update(chat_text=chat_text, when=when, num=num, answer_text=answer_text, response_text=response_text)
def signin_options() -> dict:
    """Which extra sign-in buttons to show: only those whose keys are set."""
    from app.config import get_settings
    from app.services import phone_auth
    s = get_settings()
    return {"google": s.google_enabled, "phone": s.phone_enabled, "countries": phone_auth.countries() if s.phone_enabled else []}


templates.env.globals.update(signin_options=signin_options, SUBJECT_CODE=SUBJECT_CODE, NAV=NAV, STATES=STATES, now=lambda: datetime.now(timezone.utc),
                             CONSOLE_CSS="/static/app.css", HTMX_JS="/static/vendor/htmx-2.0.4.min.js")


class LoginRequired(Exception):
    def __init__(self, next_url: str):
        self.next_url = next_url


class AdminRequired(Exception):
    pass


class ConsentRequired(Exception):
    pass


@dataclass
class Page:
    request: Request
    db: Session
    user: User | None

    @property
    def csrf(self) -> str:
        session = self.request.cookies.get(COOKIE_NAME)
        return csrf_token_for(session) if session else ""

    def render(self, template: str, status_code: int = 200, **ctx):
        return templates.TemplateResponse(self.request, template, {"page": self, "me": self.user, **ctx},
                                          status_code=status_code)


def page(request: Request, db: Session = Depends(get_db)) -> Page:
    return Page(request, db, user_from_request(request, db))


def student_page(p: Page = Depends(page)) -> Page:
    if p.user is None:
        q = p.request.url.query
        raise LoginRequired(p.request.url.path + (f"?{q}" if q else ""))
    from app.services.accounts import has_consent
    if not has_consent(p.user):  # under 18 without a guardian's consent yet, or never asked
        raise ConsentRequired()
    return p


def admin_page(p: Page = Depends(student_page)) -> Page:
    if p.user.role != Role.admin:
        raise AdminRequired()
    return p
