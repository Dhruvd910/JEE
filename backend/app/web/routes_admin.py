"""Admin console. Same layout and design as NEET Compass's console; the NEET | JEE switcher moves between the two."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select

from app import admin_bridge
from app.config import get_settings
from app.models import (Attempt, AttemptItem, Cutoff, Institute, Paper, Question, Role, SyllabusUnit, Transcript,
                        User)
from app.security import NO_PASSWORD, verify_password
from app.services import exams, study
from app.web.core import Page, admin_page, page
from app.web.routes import _404, _signed_in

router = APIRouter()
SECTIONS = [("overview", "Overview", "grid"), ("students", "Students", "list"), ("questions", "Question bank", "file"),
            ("data", "Data sources", "building")]


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _daily(db, col, days: int = 14) -> list[int]:
    """Count per day over the last `days` days (oldest first), for sparklines."""
    start = date.today() - timedelta(days=days - 1)
    rows = db.execute(select(func.date(col), func.count()).where(col >= datetime.combine(start, datetime.min.time()))
                      .group_by(func.date(col))).all()
    by = {str(d): n for d, n in rows}
    return [by.get(str(start + timedelta(days=i)), 0) for i in range(days)]


def spark(values: list[int], w: int = 132, h: int = 30) -> str:
    top = max(values) or 1
    step = w / max(1, len(values) - 1)
    return " ".join(f"{i * step:.1f},{h - 2 - (h - 4) * v / top:.1f}" for i, v in enumerate(values))


def overview(db) -> dict:
    count = lambda m, *w: db.scalar(select(func.count()).select_from(m).where(*w)) or 0  # noqa: E731
    cards = [
        ("Students", count(User), f"{count(User, User.created_at >= _since(7))} new this week", _daily(db, User.created_at)),
        ("Active this week", count(User, User.last_login_at >= _since(7)), "signed in during the last 7 days",
         _daily(db, User.last_login_at)),
        ("Tests finished", count(Attempt, Attempt.submitted_at.is_not(None)),
         f"{count(Attempt, Attempt.submitted_at >= _since(1))} in the last 24 h", _daily(db, Attempt.submitted_at)),
        ("Mock versions", count(Question, Question.origin == "variant", Question.status == "served"),
         "AI-checked questions in the bank", _daily(db, Question.created_at)),
    ]
    feed = [("signup", u.created_at, u, "created an account") for u in
            db.scalars(select(User).order_by(User.created_at.desc()).limit(10))]
    for a, u in db.execute(select(Attempt, User).join(User, User.id == Attempt.user_id).where(Attempt.submitted_at.is_not(None))
                           .order_by(Attempt.submitted_at.desc()).limit(10)).all():
        feed.append(("test", a.submitted_at, u, f"finished {a.title} · {a.score}/{a.max_score}"))
    feed.sort(key=lambda e: exams._aware(e[1]), reverse=True)
    s = get_settings()
    years = sorted(db.scalars(select(Cutoff.year).distinct()))
    checks = [
        ("ok" if count(Paper) else "todo", "Official papers loaded", f"{count(Paper)} papers"),
        ("ok" if len(years) >= 3 else "warn", "JoSAA closing ranks", ", ".join(map(str, years)) or "none"),
        ("ok" if count(Institute, Institute.nirf_rank.is_not(None)) else "warn", "NIRF ranks matched",
         f"{count(Institute, Institute.nirf_rank.is_not(None))} institutes"),
        ("ok" if count(SyllabusUnit) else "warn", "Official syllabus loaded", f"{count(SyllabusUnit)} units"),
        ("ok" if s.openrouter_api_key else "warn", "Free-model key (transcripts, mock versions)",
         "set" if s.openrouter_api_key else "OPENROUTER_API_KEY missing"),
        ("ok" if not exams.mock_shortfall(db, User(id=0), "main") else "warn", "JEE Main mock bank",
         "enough for a full mock" if not exams.mock_shortfall(db, User(id=0), "main") else "still filling"),
        ("ok" if s.admin_bridge_secret else "warn", "NEET ↔ JEE admin switch", "signed hand-off on" if s.admin_bridge_secret
         else "ADMIN_BRIDGE_SECRET not set: admins sign in on each site"),
        ("ok" if s.env != "production" or s.cookie_secure else "todo", "Secure cookies in production", s.env),
    ]
    return {"cards": cards, "feed": feed[:14], "checks": checks}


def students(db, q: str) -> dict:
    stmt = (select(User, func.count(Attempt.id), func.max(Attempt.submitted_at)).outerjoin(Attempt, Attempt.user_id == User.id)
            .group_by(User.id).order_by(User.created_at.desc()).limit(300))
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(User.email).like(like), func.lower(User.name).like(like)))
    return {"rows": db.execute(stmt).all(), "q": q,
            "totals": [("Students", db.scalar(select(func.count()).select_from(User))),
                       ("New this week", db.scalar(select(func.count()).where(User.created_at >= _since(7)))),
                       ("Active today", db.scalar(select(func.count()).where(User.last_login_at >= _since(1)))),
                       ("Admins", db.scalar(select(func.count()).where(User.role == Role.admin)))]}


def question_bank(db) -> dict:
    papers = db.execute(select(Paper, func.count(Question.id)).join(Question, Question.paper_id == Paper.id)
                        .where(Question.status == "served").group_by(Paper.id).order_by(Paper.exam.desc(), Paper.code)).all()
    variants = db.execute(select(Question.exam, Question.subject, Question.qtype, func.count()).where(
        Question.origin == "variant", Question.status == "served").group_by(Question.exam, Question.subject, Question.qtype)).all()
    t = dict(db.execute(select(Transcript.agreed, func.count()).group_by(Transcript.agreed)).all())
    return {"papers": papers, "have": {(e, s, q): n for e, s, q, n in variants}, "transcripts": t, "counts": exams.counts(db),
            "mocks": exams.MOCKS, "short": {e: exams.mock_shortfall(db, User(id=0), e) for e in exams.MOCKS}}


def data_sources(db) -> dict:
    cut = db.execute(select(Cutoff.year, Cutoff.round, func.count(), func.count(func.distinct(Cutoff.institute_id)))
                     .group_by(Cutoff.year, Cutoff.round).order_by(Cutoff.year.desc())).all()
    kinds = db.execute(select(Institute.kind, func.count()).group_by(Institute.kind)).all()
    return {"cut": cut, "kinds": kinds, "nirf": db.scalar(select(func.count()).where(Institute.nirf_rank.is_not(None))),
            "units": db.execute(select(SyllabusUnit.subject, func.count()).group_by(SyllabusUnit.subject)).all(),
            "papers": db.scalars(select(Paper).order_by(Paper.code)).all(), "syllabus_url": study.SYLLABUS_URL}


def _shell(p: Page, template: str, section: str, **ctx):
    return p.render(template, SECTIONS=SECTIONS, section=section, exam="jee",
                    peer_url=get_settings().neet_url.rstrip("/") + "/admin", **ctx)


@router.get("/admin")
def admin(p: Page = Depends(admin_page), tab: str = "overview", q: str = ""):
    tab = tab if tab in dict((k, v) for k, v, _ in SECTIONS) else "overview"
    builders = {"overview": lambda: overview(p.db), "students": lambda: students(p.db, q),
                "questions": lambda: question_bank(p.db), "data": lambda: data_sources(p.db)}
    return _shell(p, "admin/index.html", tab, tab=tab, d=builders[tab](), spark=spark)


@router.get("/admin/users/{user_id}")
def admin_user(user_id: int, p: Page = Depends(admin_page)):
    u = p.db.get(User, user_id) or _404()
    return _shell(p, "admin/user.html", "students", u=u, progress=exams.progress(p.db, u),
                  history=exams.history(p.db, u, 100), streak=exams.daily_streak(p.db, u),
                  syllabus=study.syllabus_summary(p.db, u), mistakes=study.mistake_counts(p.db, u),
                  choices=study.choices(p.db, u), data=study.analytics(p.db, u))


@router.get("/admin/papers/{paper_id}")
def admin_paper(paper_id: int, p: Page = Depends(admin_page)):
    paper = p.db.get(Paper, paper_id) or _404()
    answered = dict(p.db.execute(select(AttemptItem.question_id, func.count()).join(Question)
                                 .where(Question.paper_id == paper.id, AttemptItem.response.is_not(None))
                                 .group_by(AttemptItem.question_id)).all())
    return _shell(p, "admin/paper.html", "questions", paper=paper, answered=answered)


@router.post("/admin/questions/{qid}/toggle")
def admin_toggle(qid: int, p: Page = Depends(admin_page)):
    q = p.db.get(Question, qid) or _404()
    if q.status in ("served", "hidden"):
        q.status = "hidden" if q.status == "served" else "served"
        p.db.commit()
    label = "served · hide" if q.status == "served" else "hidden · unhide"
    return HTMLResponse(f'<button class="btn-ghost-sm" hx-post="/admin/questions/{q.id}/toggle" hx-swap="outerHTML">'
                        f'{label}</button>')


# ---------------------------------------------------------------- admin sign-in and the NEET | JEE switch

@router.get("/admin/login")
def admin_login_form(p: Page = Depends(page)):
    if p.user and p.user.role == Role.admin:
        return RedirectResponse("/admin", status_code=303)
    return p.render("admin/login.html", exam="jee", error=None, email="",
                    peer_url=get_settings().neet_url.rstrip("/") + "/admin/login")


@router.post("/admin/login")
def admin_login(email: str = Form(...), password: str = Form(...), p: Page = Depends(page)):
    """Admins only; one message for every failure, so it doesn't reveal which accounts exist."""
    user = p.db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or user.role != Role.admin or not verify_password(password, user.password_hash):
        return p.render("admin/login.html", status_code=401, exam="jee", email=email,
                        error="Incorrect email or password, or this account is not an admin.",
                        peer_url=get_settings().neet_url.rstrip("/") + "/admin/login")
    return _signed_in(p, user, "/admin")


@router.get("/admin/switch/neet")
def switch_to_neet(p: Page = Depends(admin_page)):
    s = get_settings()
    target = s.neet_url.rstrip("/")
    if s.admin_bridge_secret:
        token = admin_bridge.make(s.admin_bridge_secret, p.user.email, "neet")
        return RedirectResponse(f"{target}/admin/bridge?t={quote(token)}", status_code=303)
    return RedirectResponse(f"{target}/admin", status_code=303)


@router.get("/admin/bridge")
def bridge(t: str = "", p: Page = Depends(page)):
    """Arriving from the NEET console with a signed link: sign in the admin with the same email here.

    NEET Compass's admins are JEE Workbench's admins: the first time one arrives, their admin account is created here
    (without a password; `python -m app.maintenance create-admin` sets one for the sign-in form). An existing
    non-admin account with that email is never promoted, because JEE sign-ups don't prove who owns an email."""
    email = admin_bridge.read(get_settings().admin_bridge_secret, t, "jee")
    user = p.db.scalar(select(User).where(User.email == email)) if email else None
    if email and user is None:
        user = User(email=email, name=email.split("@")[0], password_hash=NO_PASSWORD, role=Role.admin)
        p.db.add(user)
        p.db.commit()
    if user is None or user.role != Role.admin:
        return RedirectResponse("/admin/login", status_code=303)
    return _signed_in(p, user, "/admin")

