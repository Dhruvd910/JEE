"""Every page of the website. Pages call the service layer directly."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select

from app.config import get_settings
from app.models import Attempt, AttemptItem, Cutoff, Institute, Paper, InstituteReview, MockLog, Question, QuestionReport, Role, SavedInstitute, Transcript, User, utcnow
from app.security import COOKIE_NAME, NO_PASSWORD, create_token, hash_password, verify_password
from app.services import accounts, ai, exams, game, predictor, study
from app.web.core import STATES, Page, admin_page, page, student_page

router = APIRouter()


def _go(p: Page, url: str):
    if p.request.headers.get("hx-request"):
        return HTMLResponse("", headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def _safe_next(url: str | None) -> str:
    return url if url and url.startswith("/") and not url.startswith("//") else "/dashboard"


# ---------------------------------------------------------------- public

@router.get("/")
def home(p: Page = Depends(page)):
    db = p.db
    stats = {
        "pyq": db.scalar(select(func.count()).where(Question.origin == "pyq", Question.status == "served")),
        "papers": db.scalar(select(func.count()).select_from(Paper)),
        "institutes": db.scalar(select(func.count()).select_from(Institute)),
        "cutoffs": db.scalar(select(func.count()).select_from(Cutoff)),
        "years": sorted(db.scalars(select(Cutoff.year).distinct())),
        "variants": db.scalar(select(func.count()).where(Question.origin == "variant", Question.status == "served")),
    }
    return p.render("home.html", stats=stats)


@router.get("/register")
def register_form(p: Page = Depends(page)):
    return p.render("auth/register.html", error=None, form={})


@router.post("/register")
async def register(p: Page = Depends(page)):
    form = dict(await p.request.form())
    email, name, pw = form.get("email", "").strip().lower(), form.get("name", "").strip(), form.get("password", "")
    error = None
    if "@" not in email or len(email) > 254:
        error = "Enter a valid email address."
    elif not name or len(name) > 120:
        error = "Enter your name."
    elif len(pw) < 8:
        error = "Use a password of at least 8 characters."
    elif p.db.scalar(select(User).where(User.email == email)):
        error = "An account with this email already exists. Sign in instead."
    else:
        error = accounts.age_answer(form.get("minor"), form.get("guardian_email", ""), email, "privacy" in form)
    if error:
        return p.render("auth/register.html", status_code=400, error=error, form=form)
    user = User(email=email, name=name, password_hash=hash_password(pw))
    p.db.add(user)
    accounts.apply_age(p.db, user, form.get("minor") == "yes", form.get("guardian_email", ""))
    p.db.commit()
    return _signed_in(p, user, "/dashboard")


def _signed_in(p: Page, user: User, url: str):
    user.last_login_at, user.login_count = utcnow(), (user.login_count or 0) + 1
    p.db.commit()
    resp = RedirectResponse(url, status_code=303)
    resp.set_cookie(COOKIE_NAME, create_token(user), httponly=True, samesite="lax", secure=get_settings().cookie_secure,
                    max_age=get_settings().jwt_expiry_minutes * 60)
    return resp


@router.get("/login")
def login_form(p: Page = Depends(page), next: str = "/dashboard"):
    return p.render("auth/login.html", error=None, next=_safe_next(next), email="")


@router.post("/login")
async def login(p: Page = Depends(page)):
    form = await p.request.form()
    email = str(form.get("email", "")).strip().lower()
    user = p.db.scalar(select(User).where(User.email == email))
    if user is not None and user.password_hash == NO_PASSWORD and user.role != Role.admin:
        how = "Google (\"Continue with Google\")" if user.google_sub else "your phone number"
        return p.render("auth/login.html", status_code=400, error=f"This account signs in with {how}, not a password.",
                        next=_safe_next(str(form.get("next", ""))), email=email)
    if not user or not verify_password(str(form.get("password", "")), user.password_hash):
        return p.render("auth/login.html", status_code=400, error="Wrong email or password.",
                        next=_safe_next(str(form.get("next", ""))), email=email)
    return _signed_in(p, user, _safe_next(str(form.get("next", ""))))


@router.post("/logout")
def logout(p: Page = Depends(page)):
    resp = _go(p, "/")
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ---------------------------------------------------------------- student

@router.get("/dashboard")
def dashboard(p: Page = Depends(student_page)):
    db, user = p.db, p.user
    today = db.scalar(select(Attempt).where(Attempt.user_id == user.id, Attempt.mode == "daily",
                                            Attempt.day == date.today().isoformat()))
    data = study.analytics(db, user)
    days_left = (user.target_date - date.today()).days if user.target_date else None
    from app.services import plan as plans
    roadmap = plans.build(db, user)
    return p.render("student/dashboard.html", progress=exams.progress(db, user), history=exams.history(db, user, 6),
                    plan=roadmap, week=roadmap.weeks[0] if roadmap.weeks else None,
                    plan_days=(roadmap.exam_date - date.today()).days,
                    streak=exams.daily_streak(db, user), today=today, data=data, days_left=days_left,
                    points=study.chart_points(data["trend"][-12:], 520, 120, 14),
                    syllabus=study.syllabus_summary(db, user), mistakes=study.mistake_counts(db, user),
                    choices=len(study.choices(db, user)), game=game.profile(db, user),
                    mocks={e: not exams.mock_shortfall(db, user, e) for e in exams.MOCKS},
                    saved=list(db.scalars(select(SavedInstitute).where(SavedInstitute.user_id == user.id)
                                          .order_by(SavedInstitute.created_at))))


@router.get("/practice")
def practice(p: Page = Depends(student_page)):
    db = p.db
    papers = list(db.scalars(select(Paper).order_by(Paper.exam.desc(), Paper.year.desc(), Paper.code)))
    done = {pid: n for pid, n in db.execute(select(Attempt.paper_id, func.count()).where(
        Attempt.user_id == p.user.id, Attempt.submitted_at.is_not(None), Attempt.paper_id.is_not(None))
        .group_by(Attempt.paper_id))}
    served = dict(db.execute(select(Question.paper_id, func.count()).where(Question.status == "served")
                             .group_by(Question.paper_id)).all())
    short = {e: exams.mock_shortfall(db, p.user, e) for e in exams.MOCKS}
    return p.render("practice/index.html", papers=papers, done=done, served=served, short=short, mocks=exams.MOCKS)


@router.post("/practice/paper/{paper_id}")
def start_paper(paper_id: int, p: Page = Depends(student_page)):
    paper = p.db.get(Paper, paper_id) or _404()
    return _go(p, f"/attempt/{exams.start_paper(p.db, p.user, paper).id}")


@router.post("/practice/mock/{exam}")
def start_mock(exam: str, p: Page = Depends(student_page)):
    if exam not in exams.MOCKS:
        _404()
    a = exams.start_mock(p.db, p.user, exam)
    if a is None:
        short = exams.mock_shortfall(p.db, p.user, exam)
        started = ai.top_up_in_background(short, exam)
        return p.render("practice/_preparing.html", exam=exam, short=short, started=started)
    return _go(p, f"/attempt/{a.id}")


@router.post("/practice/daily")
def start_daily(p: Page = Depends(student_page)):
    return _go(p, f"/attempt/{exams.start_daily(p.db, p.user).id}")


def _404():
    raise HTTPException(404, "Not found")


def _my_attempt(p: Page, attempt_id: int) -> Attempt:
    a = p.db.get(Attempt, attempt_id)
    if not a or (a.user_id != p.user.id and p.user.role != Role.admin):
        _404()
    return exams.close_expired(p.db, a)


@router.get("/attempt/{attempt_id}")
def attempt(attempt_id: int, p: Page = Depends(student_page)):
    a = _my_attempt(p, attempt_id)
    if a.submitted_at or a.user_id != p.user.id:
        return RedirectResponse(f"/attempt/{a.id}/result", status_code=303)
    remaining = int((exams._aware(a.deadline) - utcnow()).total_seconds())
    if a.mode == "daily":  # the daily quiz is a game: one question at a time, checked as soon as it is answered
        item = next((i for i in a.items if not i.response), None)
        if item is None:
            exams.finish(p.db, a)
            return RedirectResponse(f"/attempt/{a.id}/result", status_code=303)
        return p.render("practice/game.html", a=a, item=item, g=game.run(a), remaining=remaining, HEARTS=game.HEARTS)
    subjects = {}  # in paper order (Jinja's groupby would sort them)
    for i in a.items:
        subjects.setdefault(i.question.subject, []).append(i)
    return p.render("practice/room.html", a=a, remaining=remaining, bm=study.bookmarked_ids(p.db, p.user),
                    subjects=subjects.items())


@router.post("/attempt/{attempt_id}/begin")
def begin(attempt_id: int, p: Page = Depends(student_page)):
    """Instructions read: start the clock. Returns the seconds now left (the room's script starts its timer)."""
    a = _my_attempt(p, attempt_id)
    if a.user_id != p.user.id:
        _404()
    exams.begin(p.db, a)
    return HTMLResponse(str(max(0, int((exams._aware(a.deadline) - utcnow()).total_seconds()))))


@router.post("/attempt/{attempt_id}/seen/{position}")
async def seen(attempt_id: int, position: int, p: Page = Depends(student_page)):
    """Exam room: time spent on a question, and mark / unmark for review. Returns its palette button."""
    a = _my_attempt(p, attempt_id)
    if a.user_id != p.user.id:
        _404()
    form = await p.request.form()
    next((i for i in a.items if i.position == position), None) or _404()
    mark = {"1": True, "0": False}.get(str(form.get("mark", "")))
    try:
        item = exams.note_visit(p.db, a, position, _int(form.get("secs")) or 0, mark)
    except PermissionError:
        return HTMLResponse("", headers={"HX-Redirect": f"/attempt/{a.id}/result"})
    return p.render("practice/_pal.html", item=item)


@router.post("/attempt/{attempt_id}/away")
def away(attempt_id: int, p: Page = Depends(student_page)):
    """Exam room: the student left the test window (tab switch, minimised)."""
    a = _my_attempt(p, attempt_id)
    if a.user_id == p.user.id and exams.is_open(a):
        a.focus_lost = (a.focus_lost or 0) + 1
        p.db.commit()
    return HTMLResponse("", status_code=204)


@router.post("/attempt/{attempt_id}/q/{position}")
async def save_answer(attempt_id: int, position: int, p: Page = Depends(student_page)):
    a = _my_attempt(p, attempt_id)
    if a.user_id != p.user.id:
        _404()
    form = await p.request.form()
    item = next((i for i in a.items if i.position == position), None) or _404()
    if form.get("clear"):
        resp = None
    else:
        resp = _read_response(form, item)
        if resp == "bad":
            return HTMLResponse('<span class="text-rose-400">enter a number</span>')
    try:
        item = exams.save_response(p.db, a, position, resp)
    except PermissionError:
        return HTMLResponse("", headers={"HX-Redirect": f"/attempt/{a.id}/result"})
    return p.render("practice/_saved.html", item=item, a=a)


def _read_response(form, item: AttemptItem):
    """The answer sent for one question: [value] for numerical, chosen option indexes otherwise; "bad" if unreadable."""
    if item.question.qtype == "num":
        v = str(form.get("v", "")).strip()
        try:
            return [float(v)] if v else None
        except ValueError:
            return "bad"
    return sorted({int(x) for x in form.getlist("r") if str(x) in "0123"}) or None


@router.post("/attempt/{attempt_id}/play/{position}")
async def play(attempt_id: int, position: int, p: Page = Depends(student_page)):
    """Daily-quiz game: lock in one answer, show at once whether it was right, then the next question or the result."""
    a = _my_attempt(p, attempt_id)
    if a.user_id != p.user.id or a.mode != "daily":
        _404()
    item = next((i for i in a.items if i.position == position), None) or _404()
    if a.submitted_at or item.response:  # already locked in (double tap, back button): just move on
        return HTMLResponse("", headers={"HX-Redirect": f"/attempt/{a.id}"})
    resp = _read_response(await p.request.form(), item)
    if not resp or resp == "bad":
        return HTMLResponse('<p class="text-sm text-rose-300">Pick an answer first.</p>', headers={"HX-Retarget": "#pick"})
    before = game.run(a)["xp"]
    try:
        item = exams.save_response(p.db, a, position, resp)
    except PermissionError:
        return HTMLResponse("", headers={"HX-Redirect": f"/attempt/{a.id}/result"})
    item.answer = item.question.answer  # revealed now; finish() copies it again for every question
    item.marks = exams.item_marks(item)
    p.db.commit()
    g = game.run(a)
    over = g["hearts"] == 0 or g["answered"] == g["total"]
    if over:  # out of hearts or out of questions; unanswered ones count as blank
        exams.finish(p.db, a)
        g = game.run(a)
    return p.render("practice/_game_turn.html", a=a, item=item, g=g, over=over, HEARTS=game.HEARTS,
                    right=item.marks == item.question.marks_full, gained=g["xp"] - before)


@router.post("/attempt/{attempt_id}/submit")
def submit(attempt_id: int, p: Page = Depends(student_page)):
    a = _my_attempt(p, attempt_id)
    if a.user_id == p.user.id:
        exams.finish(p.db, a)
    return _go(p, f"/attempt/{a.id}/result")


REPORT_REASONS = [("wrong_key", "The answer key is wrong"), ("unclear", "Image unclear or cut off"),
                  ("two_answers", "More than one option looks right"), ("other", "Something else")]
FILTERS = ("all", "wrong", "blank", "marked", "correct")


@router.get("/attempt/{attempt_id}/result")
def result(attempt_id: int, p: Page = Depends(student_page), filter: str = "all"):
    a = _my_attempt(p, attempt_id)
    if not a.submitted_at:
        return RedirectResponse(f"/attempt/{a.id}", status_code=303)
    flt = filter if filter in FILTERS else "all"
    keep = {"all": lambda i: True, "marked": lambda i: i.marked, "wrong": lambda i: exams.outcome(i) in ("wrong", "partial"),
            "blank": lambda i: not i.response, "correct": lambda i: exams.outcome(i) == "correct"}
    counts = {f: sum(1 for i in a.items if keep[f](i)) for f in FILTERS}
    return p.render("practice/result.html", a=a, parts=exams.breakdown(a), bm=study.bookmarked_ids(p.db, p.user),
                    g=game.run(a), stars=game.stars(a), prof=game.profile(p.db, p.user), ins=exams.insights(a),
                    QTYPES=exams.QTYPE_NAMES, flt=flt, counts=counts, shown=[i for i in a.items if keep[flt](i)],
                    REASONS=REPORT_REASONS)


@router.post("/attempt/{attempt_id}/report/{position}")
async def report(attempt_id: int, position: int, p: Page = Depends(student_page)):
    """Report a problem with one question of a submitted test; the admin sees it in the question bank."""
    a = _my_attempt(p, attempt_id)
    item = next((i for i in a.items if i.position == position), None) or _404()
    if a.user_id != p.user.id or not a.submitted_at:
        _404()
    form = await p.request.form()
    reason = form.get("reason") if form.get("reason") in dict(REPORT_REASONS) else "other"
    if not p.db.scalar(select(QuestionReport.id).where(QuestionReport.user_id == p.user.id, QuestionReport.status == "open",
                                                       QuestionReport.question_id == item.question_id)):
        p.db.add(QuestionReport(question_id=item.question_id, user_id=p.user.id, reason=reason,
                                note=str(form.get("note", "")).strip()[:1000]))
        p.db.commit()
    return HTMLResponse('<p class="text-xs text-emerald-300" role="status">Thanks, sent for checking. '
                        'If the key is wrong, the question is fixed or removed for everyone.</p>')


@router.get("/img/{path:path}")
def image(path: str, p: Page = Depends(student_page)):
    """Official paper images, only for signed-in students (they are not part of the public repo or site)."""
    root = (get_settings().live_dir / "q").resolve()
    f = (get_settings().live_dir / path).resolve()
    if root not in f.parents or not f.is_file() or f.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        _404()
    return FileResponse(f, headers={"Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------- colleges & predictor

@router.get("/colleges")
def colleges(p: Page = Depends(page), q: str = "", kind: str = "", state: str = ""):
    stmt = select(Institute)
    if q:
        stmt = stmt.where(or_(Institute.name.ilike(f"%{q}%"), Institute.city.ilike(f"%{q}%")))
    if kind:
        stmt = stmt.where(Institute.kind == kind)
    if state:
        stmt = stmt.where(Institute.state == state)
    rows = p.db.scalars(stmt.order_by(Institute.nirf_rank.is_(None), Institute.nirf_rank, Institute.kind,
                                      Institute.name)).all()
    programs = dict(p.db.execute(select(Cutoff.institute_id, func.count(func.distinct(Cutoff.program)))
                                 .group_by(Cutoff.institute_id)).all())
    ctx = dict(rows=rows, programs=programs, q=q, kind=kind, state=state)
    if p.request.headers.get("hx-request") and p.request.headers.get("hx-target") == "results":
        return p.render("colleges/_rows.html", **ctx)
    return p.render("colleges/list.html", **ctx)


@router.get("/colleges/{institute_id}")
def college(institute_id: int, p: Page = Depends(page), quota: str = "", seat: str = "OPEN",
            gender: str = "Gender-Neutral"):
    inst = p.db.get(Institute, institute_id) or _404()
    quotas = sorted(p.db.scalars(select(Cutoff.quota).where(Cutoff.institute_id == inst.id).distinct()))
    quota = quota if quota in quotas else ("AI" if "AI" in quotas else "OS" if "OS" in quotas else quotas[0])
    seats = sorted(p.db.scalars(select(Cutoff.seat_type).where(Cutoff.institute_id == inst.id).distinct()))
    years = sorted(p.db.scalars(select(Cutoff.year).where(Cutoff.institute_id == inst.id).distinct()), reverse=True)
    table = predictor.program_table(p.db, inst.id, quota, seat, gender)
    ctx = dict(inst=inst, quotas=quotas, quota=quota, seats=seats, seat=seat, gender=gender, years=years, table=table,
               source=p.db.scalar(select(Cutoff.source_url).limit(1)))
    if p.request.headers.get("hx-target") == "ranks":
        return p.render("colleges/_ranks.html", **ctx)
    reviews = list(p.db.scalars(select(InstituteReview).where(InstituteReview.institute_id == inst.id,
                                                              InstituteReview.status == "approved")
                                .order_by(InstituteReview.created_at.desc())))
    mine = p.user and p.db.scalar(select(InstituteReview).where(InstituteReview.institute_id == inst.id,
                                                                InstituteReview.user_id == p.user.id))
    return p.render("colleges/detail.html", **ctx, reviews=reviews, mine=mine, RELATIONS=RELATIONS,
                    avg=round(sum(r.rating for r in reviews) / len(reviews), 1) if reviews else None,
                    saved=bool(p.user) and inst.id in _saved_ids(p))


RELATIONS = {"aspirant": "Visited / aspirant", "student": "Current student", "alumnus": "Graduate", "parent": "Parent"}


def _saved_ids(p: Page) -> set[int]:
    return set(p.db.scalars(select(SavedInstitute.institute_id).where(SavedInstitute.user_id == p.user.id)))


@router.post("/colleges/{institute_id}/save")
def save_toggle(institute_id: int, p: Page = Depends(student_page)):
    inst = p.db.get(Institute, institute_id) or _404()
    row = p.db.scalar(select(SavedInstitute).where(SavedInstitute.user_id == p.user.id,
                                                   SavedInstitute.institute_id == inst.id))
    if row:
        p.db.delete(row)
    else:
        p.db.add(SavedInstitute(user_id=p.user.id, institute_id=inst.id))
    p.db.commit()
    return p.render("colleges/_save.html", inst=inst, saved=not row)


@router.post("/colleges/{institute_id}/review")
async def review_add(institute_id: int, p: Page = Depends(student_page)):
    inst = p.db.get(Institute, institute_id) or _404()
    f = await p.request.form()
    body, rating = str(f.get("body", "")).strip(), _int(f.get("rating"))
    if not rating or rating > 5 or not 30 <= len(body) <= 2000:
        return HTMLResponse('<p class="alert" role="alert">Pick 1–5 stars and write 30 to 2,000 characters.</p>')
    if p.db.scalar(select(InstituteReview.id).where(InstituteReview.institute_id == inst.id,
                                                    InstituteReview.user_id == p.user.id)):
        return HTMLResponse('<p class="alert" role="alert">You have already reviewed this institute.</p>')
    p.db.add(InstituteReview(institute_id=inst.id, user_id=p.user.id, rating=rating, body=body,
                             relation=f.get("relation") if f.get("relation") in RELATIONS else "aspirant"))
    p.db.commit()
    return HTMLResponse('<p class="text-sm text-emerald-300" role="status">Thanks! Your review appears here once an '
                        'admin has checked it.</p>')


def _int(v) -> int | None:
    try:
        n = int(str(v).replace(",", "").strip())
        return n if n > 0 else None
    except ValueError:
        return None


@router.get("/predictor")
def predictor_page(p: Page = Depends(page)):
    qp = p.request.query_params
    me = p.user
    form = {"category": qp.get("category") or (me.category if me else None) or "OPEN",
            "gender": qp.get("gender") or (me.gender if me else None) or "male",
            "home_state": qp.get("home_state") if "home_state" in qp else ((me.home_state if me else None) or ""),
            **{k: qp.get(k, "") for k in ("main_rank", "main_cat_rank", "adv_rank", "adv_cat_rank", "program")},
            "pwd": qp.get("pwd") == "on", "kinds": qp.getlist("kinds") or ["IIT", "NIT", "IIIT", "GFTI"]}
    results, notes = None, []
    if qp.get("go"):
        query = predictor.Query(main_rank=_int(form["main_rank"]), main_cat_rank=_int(form["main_cat_rank"]),
                                adv_rank=_int(form["adv_rank"]), adv_cat_rank=_int(form["adv_cat_rank"]),
                                category=form["category"] if form["category"] in predictor.CATEGORIES else "OPEN",
                                female=form["gender"] == "female", pwd=form["pwd"],
                                home_state=form["home_state"] or None, kinds=tuple(form["kinds"]),
                                program=form["program"][:80])
        results, notes = predictor.predict(p.db, query)
        if me:  # remember the student's choices for next time (used only here)
            me.category, me.gender, me.home_state = query.category, form["gender"], query.home_state
            p.db.commit()
    years = sorted(p.db.scalars(select(Cutoff.year).distinct()), reverse=True)[:3]
    ctx = dict(form=form, results=results, notes=notes, years=years, categories=predictor.CATEGORIES)
    if p.request.headers.get("hx-target") == "prediction":
        return p.render("_prediction.html", **ctx)
    return p.render("predictor.html", **ctx)


# ---------------------------------------------------------------- study tools

@router.post("/bookmark/{qid}")
def bookmark(qid: int, p: Page = Depends(student_page)):
    p.db.get(Question, qid) or _404()
    on = study.toggle_bookmark(p.db, p.user, qid)
    return p.render("_bookmark.html", qid=qid, on=on)


@router.get("/drill")
def drill_form(p: Page = Depends(student_page)):
    db, seen = p.db, exams.seen_ids(p.db, p.user)
    unseen = dict(db.execute(select(Question.subject, func.count()).where(
        Question.origin == "pyq", Question.status == "served", Question.id.not_in(seen)).group_by(Question.subject)).all())
    return p.render("practice/drill.html", unseen=unseen, sources=study.DRILL_SOURCES, error=None,
                    mistakes=study.mistake_counts(db, p.user), bookmarks=len(study.bookmarked_ids(db, p.user)),
                    weak=study.weak_areas(db, p.user), QTYPES=exams.QTYPE_NAMES)


@router.post("/drill")
async def drill_start(p: Page = Depends(student_page)):
    f = await p.request.form()
    pick = lambda k, allowed: f.get(k) if f.get(k) in allowed else None  # noqa: E731
    a = study.start_drill(p.db, p.user, pick("subject", exams.SUBJECTS), pick("exam", ("main", "advanced")),
                          pick("qtype", ("single", "multi", "num", "match")), _int(f.get("count")) or 10,
                          pick("source", study.DRILL_SOURCES) or "unseen")
    if a is None:
        if f.get("source") == "weak" and not study.weak_areas(p.db, p.user):
            return HTMLResponse('<p class="alert" role="alert">Weak areas come from your results: answer at least 3 questions '
                                'of a type in any test first.</p>')
        return HTMLResponse('<p class="alert" role="alert">No questions match those filters. Widen them and try again.</p>')
    return _go(p, f"/attempt/{a.id}")


@router.get("/mistakes")
def mistakes_page(p: Page = Depends(student_page), subject: str = "", tab: str = "mistakes"):
    subject = subject if subject in exams.SUBJECTS else ""
    if tab == "bookmarks":
        ids = study.bookmarked_ids(p.db, p.user)
        qs = [q for q in p.db.scalars(select(Question).where(Question.id.in_(ids))) if not subject or q.subject == subject]
        items = [SimpleNamespace(question=q, position=i + 1, answer=q.answer, response=None, marks=None)
                 for i, q in enumerate(qs)]
    else:
        items = study.mistakes(p.db, p.user, subject or None)
    return p.render("student/mistakes.html", items=items, tab=tab, subject=subject,
                    counts=study.mistake_counts(p.db, p.user), bm=study.bookmarked_ids(p.db, p.user))


@router.get("/analytics")
def analytics_page(p: Page = Depends(student_page)):
    data = study.analytics(p.db, p.user)
    return p.render("student/analytics.html", data=data, points=study.chart_points(data["trend"]),
                    progress=exams.progress(p.db, p.user))


def _mock_page(p: Page, error: str | None = None, status_code: int = 200):
    logs = study.mock_logs(p.db, p.user)
    trend = [{"title": m.name, "pct": round(100 * m.score / m.max_score), "score": m.score, "max": m.max_score,
              "date": m.test_date} for m in logs]
    return p.render("student/mock_tests.html", logs=logs, summary=study.mock_summary(logs), error=error,
                    points=study.chart_points(trend), today=date.today().isoformat(), status_code=status_code)


@router.get("/mock-tests")
def mock_tests(p: Page = Depends(student_page)):
    return _mock_page(p)


@router.post("/mock-tests")
async def mock_add(p: Page = Depends(student_page)):
    error = study.add_mock_log(p.db, p.user, await p.request.form())
    return _mock_page(p, error)


@router.post("/mock-tests/{log_id}/delete")
def mock_delete(log_id: int, p: Page = Depends(student_page)):
    m = p.db.get(MockLog, log_id)
    if not m or m.user_id != p.user.id:
        _404()
    p.db.delete(m)
    p.db.commit()
    return _mock_page(p)


@router.get("/syllabus")
def syllabus_page(p: Page = Depends(student_page)):
    return p.render("student/syllabus.html", units=study.syllabus(p.db, p.user),
                    summary=study.syllabus_summary(p.db, p.user), source=study.SYLLABUS_URL)


@router.post("/syllabus/{unit_id}")
async def syllabus_set(unit_id: int, p: Page = Depends(student_page)):
    from app.models import SyllabusUnit
    unit = p.db.get(SyllabusUnit, unit_id) or _404()
    status = str((await p.request.form()).get("status", ""))
    study.set_unit(p.db, p.user, unit.id, status)
    return p.render("student/_unit.html", u=unit, st=status if status in study.UNIT_STATES else None, oob=True,
                    summary=study.syllabus_summary(p.db, p.user))


@router.post("/target-date")
async def target_date(p: Page = Depends(student_page)):
    v = str((await p.request.form()).get("target_date", "")).strip()
    try:
        p.user.target_date = date.fromisoformat(v) if v else None
    except ValueError:
        pass
    p.db.commit()
    return _go(p, "/dashboard")


@router.get("/compare")
def compare_page(p: Page = Depends(page)):
    ids = [i for i in (_int(x) for x in p.request.query_params.getlist("ids")) if i]
    data = study.compare(p.db, ids) if ids else None
    return p.render("colleges/compare.html", data=data,
                    options=p.db.execute(select(Institute.id, Institute.name, Institute.kind)
                                         .order_by(Institute.kind, Institute.name)).all(), ids=ids)


@router.get("/choices")
def choices_page(p: Page = Depends(student_page)):
    items = study.choices(p.db, p.user)
    return p.render("colleges/choices.html", items=items, closing=study.last_closing(p.db, items, p.user))


@router.post("/choices")
async def choice_add(p: Page = Depends(student_page)):
    f = await p.request.form()
    try:
        added = study.add_choice(p.db, p.user, _int(f.get("institute_id")) or 0, str(f.get("program", ""))[:250])
    except ValueError as e:
        return HTMLResponse(f'<span class="text-rose-300">{e}</span>')
    return HTMLResponse('<span class="chip-ok">✓ in choice list</span>' if added else '<span class="chip">already listed</span>')


@router.post("/choices/{item_id}/{action}")
def choice_edit(item_id: int, action: str, p: Page = Depends(student_page)):
    if action in ("up", "down"):
        study.move_choice(p.db, p.user, item_id, -1 if action == "up" else 1)
    elif action == "delete":
        study.remove_choice(p.db, p.user, item_id)
    return _go(p, "/choices")


@router.get("/choices.csv")
def choices_csv(p: Page = Depends(student_page)):
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["preference", "institute", "type", "program"])
    for c in study.choices(p.db, p.user):
        w.writerow([c.position, c.institute.name, c.institute.kind, c.program])
    return HTMLResponse(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="josaa-choices.csv"'})


# ---------------------------------------------------------------- AI assistant and profile

SUGGESTIONS = ["Which NITs can I get with a JEE Main rank of 15000?", "What was IIT Bombay's CSE closing rank in 2025?",
               "Where am I losing the most marks?", "How is JEE Advanced marked for multiple-correct questions?",
               "Make me a revision plan for my weakest subject"]


CHAT_GROUPS = ["Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older"]


def _chat_group(v) -> str:
    """Chats are listed by when they were last used, in Indian time (like NEET Compass)."""
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    d = (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(ist).date()
    days = (datetime.now(ist).date() - d).days
    return "Today" if days <= 0 else "Yesterday" if days == 1 else "Previous 7 days" if days < 7         else "Previous 30 days" if days < 30 else "Older"


def _assistant(p: Page, chat_id: int | None, error: str | None = None, speak: bool = False, status_code: int = 200,
               q: str = ""):
    from app.models import Chat
    from app.services import assistant
    chat = p.db.get(Chat, chat_id) if chat_id else None
    if chat and chat.user_id != p.user.id:
        chat = None
    chats = [c for c in assistant.chats(p.db, p.user) if not q.strip() or q.strip().lower() in c.title.lower()]
    groups = [(g, [c for c in chats if _chat_group(c.updated_at) == g]) for g in CHAT_GROUPS]
    resp = p.render("assistant.html", status_code=status_code, chats=chats, groups=[x for x in groups if x[1]], q=q,
                    chat=chat, error=error, speak=speak, SUGGESTIONS=SUGGESTIONS)
    if chat and p.request.headers.get("hx-request"):
        resp.headers["HX-Push-Url"] = f"/assistant?chat={chat.id}"
    return resp


@router.get("/assistant")
def assistant_page(p: Page = Depends(student_page), chat: int | None = None, q: str = ""):
    return _assistant(p, chat, q=q[:80])


@router.post("/assistant/send")
async def assistant_send(p: Page = Depends(student_page)):
    from app.services import assistant
    f = await p.request.form()
    chat_id = _int(f.get("chat_id"))
    try:
        chat = assistant.send(p.db, p.user, chat_id, str(f.get("content", "")), voice=f.get("voice") == "1")
    except ValueError as e:
        return _assistant(p, chat_id, error=str(e), status_code=422)
    return _assistant(p, chat.id, speak=f.get("voice") == "1")


@router.post("/assistant/{chat_id}/delete")
def assistant_delete(chat_id: int, p: Page = Depends(student_page)):
    from app.models import Chat
    chat = p.db.get(Chat, chat_id)
    if chat and chat.user_id == p.user.id:
        p.db.delete(chat)
        p.db.commit()
    return _go(p, "/assistant")


@router.get("/profile")
def profile_page(p: Page = Depends(student_page), saved: int = 0):
    return p.render("student/profile.html", categories=predictor.CATEGORIES, saved=bool(saved))


@router.post("/profile")
async def profile_save(p: Page = Depends(student_page)):
    f = await p.request.form()
    u = p.user
    name = str(f.get("name", "")).strip()
    if name:
        u.name = name[:120]
    u.category = f.get("category") if f.get("category") in predictor.CATEGORIES else None
    u.gender = f.get("gender") if f.get("gender") in ("male", "female") else None
    u.home_state = f.get("home_state") if f.get("home_state") in STATES else None
    try:
        u.target_date = date.fromisoformat(str(f.get("target_date"))) if f.get("target_date") else None
    except ValueError:
        pass
    p.db.commit()
    return _go(p, "/profile?saved=1")


# ---------------------------------------------------------------- study plan and counselling centre

@router.get("/study-plan")
def study_plan_page(p: Page = Depends(student_page), all: int = 0):  # noqa: A002
    from app.services import plan
    return p.render("student/study_plan.html", plan=plan.build(p.db, p.user), show_all=bool(all),
                    classes=plan.CLASSES, today=date.today())


@router.post("/study-plan")
async def study_plan_settings(p: Page = Depends(student_page)):
    from app.services import plan
    f = await p.request.form()
    u = p.user
    u.class_level = f.get("class_level") if f.get("class_level") in plan.CLASSES else u.class_level
    hours = _int(f.get("study_hours"))
    u.study_hours = max(1, min(hours, 14)) if hours else u.study_hours
    try:
        u.target_date = date.fromisoformat(str(f.get("target_date"))) if f.get("target_date") else u.target_date
    except ValueError:
        pass
    p.db.commit()
    return _go(p, "/study-plan")


@router.post("/study-plan/done/{unit_id}")
async def study_plan_done(unit_id: int, p: Page = Depends(student_page)):
    from app.models import SyllabusUnit
    from app.services import plan
    p.db.get(SyllabusUnit, unit_id) or _404()
    kind = str((await p.request.form()).get("kind", "learn"))
    plan.complete(p.db, p.user, unit_id, kind if kind in ("learn", "revise") else "learn")
    return _go(p, "/study-plan")


@router.get("/jee")
def jee_info_page(p: Page = Depends(page)):
    from app.services import jee_info
    return p.render("jee_info.html", j=jee_info)


@router.get("/careers")
def careers_page(p: Page = Depends(page)):
    from app.services import jee_info
    return p.render("careers.html", j=jee_info)


@router.get("/counselling")
def counselling_page(p: Page = Depends(page)):
    from app.services import counselling
    return p.render("counselling.html", c=counselling)
