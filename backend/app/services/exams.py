"""Timed tests: official previous-year papers, mocks that never repeat a question for a student, and a daily quiz.

Marking follows the official documents:
- JEE Main (NTA Information Bulletin 2025/2026): MCQ +4 / −1 / 0, numerical value +4 / −1 / 0.
- JEE Advanced: each section's header in the paper states its scheme; it is stored on every question
  (marks_full, marks_neg). Multiple-correct questions earn partial marks: +1 per correct option chosen when no wrong
  option is chosen and not all correct ones are (the +3 / +2 / +1 rules in the paper).
"""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Attempt, AttemptItem, Paper, Question, User

SUBJECTS = ("Physics", "Chemistry", "Mathematics")
# Mock formats. Main copies the NTA paper; Advanced copies JEE Advanced 2026 Paper 1 (sections per subject).
MOCKS = {
    "main": {"title": "JEE Main mock", "minutes": 180,
             "sections": [("single", 20, 4, 1), ("num", 5, 4, 1)]},
    "advanced": {"title": "JEE Advanced mock", "minutes": 180,
                 "sections": [("single", 4, 3, 1), ("multi", 4, 4, 1), ("num", 4, 4, 0), ("match", 4, 4, 1)]},
}
DAILY_PER_SUBJECT, DAILY_MINUTES = 3, 20


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- marking

def mark(qtype: str, answer: dict, full: int, neg: int, response: list | None) -> int:
    """Marks for one response under the question's own scheme. Empty response = 0."""
    if not response:
        return 0
    if qtype == "num":
        try:
            v = float(response[0])
        except (TypeError, ValueError):
            return -neg
        lo, hi = answer.get("lo"), answer.get("hi")
        return full if lo is not None and lo - 1e-9 <= v <= hi + 1e-9 else -neg
    chosen = sorted(set(int(i) for i in response))
    best = None
    for correct in answer.get("sets", []):
        if chosen == sorted(correct):
            m = full
        elif qtype == "multi" and set(chosen) < set(correct):
            m = len(chosen)  # partial: +3 of 4, +2 of ≥3, +1 of ≥2, as the paper's scheme states
        else:
            m = -neg
        best = m if best is None else max(best, m)
    return -neg if best is None else best


def item_marks(item: AttemptItem) -> int:
    q = item.question
    return mark(q.qtype, item.answer or q.answer, q.marks_full, q.marks_neg, item.response)


# ---------------------------------------------------------------- building attempts

def _new_attempt(db: Session, user: User, mode: str, exam: str, title: str, minutes: int, questions: list[Question],
                 paper: Paper | None = None, day: str | None = None) -> Attempt:
    a = Attempt(user_id=user.id, mode=mode, exam=exam, title=title, paper_id=paper.id if paper else None, day=day,
                deadline=utcnow() + timedelta(minutes=minutes), max_score=sum(q.marks_full for q in questions))
    a.items = [AttemptItem(question_id=q.id, position=i + 1) for i, q in enumerate(questions)]
    db.add(a)
    db.commit()
    return a


def start_paper(db: Session, user: User, paper: Paper) -> Attempt:
    qs = [q for q in paper.questions if q.status == "served"]
    return _new_attempt(db, user, "paper", paper.exam, paper.title, paper.duration_min, qs, paper=paper)


def seen_ids(db: Session, user: User):
    return select(AttemptItem.question_id).join(Attempt).where(Attempt.user_id == user.id)


def mock_shortfall(db: Session, user: User, exam: str) -> dict[tuple[str, str], int]:
    """How many more unseen checked versions each (subject, type) needs before a mock can be built."""
    seen = seen_ids(db, user)
    have = dict(((s, t), n) for s, t, n in db.execute(
        select(Question.subject, Question.qtype, func.count()).where(
            Question.origin == "variant", Question.exam == exam, Question.status == "served",
            Question.id.not_in(seen)).group_by(Question.subject, Question.qtype)).all())
    short = {}
    for subject in SUBJECTS:
        for qtype, count, _, _ in MOCKS[exam]["sections"]:
            if have.get((subject, qtype), 0) < count:
                short[(subject, qtype)] = count - have.get((subject, qtype), 0)
    return short


def start_mock(db: Session, user: User, exam: str) -> Attempt | None:
    """A full mock from checked new versions of past questions the student has never been given. None when the
    bank has too few unseen versions (the variant job tops it up)."""
    if mock_shortfall(db, user, exam):
        return None
    spec, seen, rng = MOCKS[exam], seen_ids(db, user), random.Random()
    picked = []
    for subject in SUBJECTS:
        for qtype, count, full, neg in spec["sections"]:
            pool = list(db.scalars(select(Question).where(
                Question.origin == "variant", Question.exam == exam, Question.status == "served",
                Question.subject == subject, Question.qtype == qtype, Question.id.not_in(seen))))
            # one version per source question, so a mock never shows two versions of the same original
            by_src = {}
            for q in rng.sample(pool, len(pool)):
                by_src.setdefault(q.variant_of or q.id, q)
            chosen = list(by_src.values())[:count]
            if len(chosen) < count:
                return None
            picked += chosen  # versions carry the mock's marking scheme (set when they are written)
    n = db.scalar(select(func.count()).select_from(Attempt).where(Attempt.user_id == user.id, Attempt.mode == "mock",
                                                                     Attempt.exam == exam)) or 0
    return _new_attempt(db, user, "mock", exam, f"{spec['title']} #{n + 1}", spec["minutes"], picked)


def daily_questions(db: Session, day: date) -> list[Question]:
    """The same questions for everyone on a given day: previous-year questions, picked by the date."""
    rng = random.Random(day.isoformat())
    out = []
    for subject in SUBJECTS:
        ids = sorted(db.scalars(select(Question.id).where(Question.origin == "pyq", Question.status == "served",
                                                          Question.subject == subject)))
        out += [db.get(Question, i) for i in rng.sample(ids, min(DAILY_PER_SUBJECT, len(ids)))]
    return out


def start_daily(db: Session, user: User, day: date | None = None) -> Attempt:
    day = day or date.today()
    existing = db.scalar(select(Attempt).where(Attempt.user_id == user.id, Attempt.mode == "daily",
                                               Attempt.day == day.isoformat()))
    if existing:
        return existing
    return _new_attempt(db, user, "daily", "mixed", f"Daily quiz · {day.day} {day:%b %Y}", DAILY_MINUTES,
                        daily_questions(db, day), day=day.isoformat())


# ---------------------------------------------------------------- taking and finishing

def is_open(a: Attempt) -> bool:
    return a.submitted_at is None and utcnow() < _aware(a.deadline)


def save_response(db: Session, a: Attempt, position: int, response: list | None) -> AttemptItem:
    if not is_open(a):
        finish(db, a)
        raise PermissionError("This test is closed.")
    item = next(i for i in a.items if i.position == position)
    item.response = response or None
    db.commit()
    return item


def finish(db: Session, a: Attempt) -> Attempt:
    """Submit (also used when the clock has run out). Answers are copied so later bank edits never change results."""
    if a.submitted_at is not None:
        return a
    total = 0
    for item in a.items:
        item.answer = item.question.answer
        item.marks = item_marks(item)
        total += item.marks
    a.score, a.submitted_at = total, min(utcnow(), _aware(a.deadline))
    db.commit()
    return a


def close_expired(db: Session, a: Attempt) -> Attempt:
    if a.submitted_at is None and utcnow() >= _aware(a.deadline):
        finish(db, a)
    return a


def breakdown(a: Attempt) -> dict[str, dict]:
    out = {s: {"score": 0, "max": 0, "correct": 0, "wrong": 0, "partial": 0, "blank": 0} for s in SUBJECTS}
    for item in a.items:
        q = item.question
        row = out.setdefault(q.subject, {"score": 0, "max": 0, "correct": 0, "wrong": 0, "partial": 0, "blank": 0})
        row["max"] += q.marks_full
        m = item.marks if item.marks is not None else item_marks(item)
        row["score"] += m
        key = "blank" if not item.response else "correct" if m == q.marks_full else "partial" if m > 0 else "wrong"
        row[key] += 1
    return {k: v for k, v in out.items() if v["max"]}


# ---------------------------------------------------------------- progress

def progress(db: Session, user: User) -> dict[str, dict]:
    """Subject-wise totals over every submitted test: attempted, correct, accuracy, and previous-year coverage."""
    rows = db.execute(select(Question.subject, AttemptItem.response, AttemptItem.marks, Question.marks_full,
                             Question.origin, Question.id).join(AttemptItem.question).join(AttemptItem.attempt)
                      .where(Attempt.user_id == user.id, Attempt.submitted_at.is_not(None))).all()
    pyq_total = dict(db.execute(select(Question.subject, func.count()).where(
        Question.origin == "pyq", Question.status == "served").group_by(Question.subject)).all())
    out = {s: {"attempted": 0, "correct": 0, "given": 0, "pyq_seen": set(), "pyq_total": pyq_total.get(s, 0)}
           for s in SUBJECTS}
    for subject, response, marks, full, origin, qid in rows:
        r = out.setdefault(subject, {"attempted": 0, "correct": 0, "given": 0, "pyq_seen": set(), "pyq_total": 0})
        r["given"] += 1
        if origin == "pyq":
            r["pyq_seen"].add(qid)
        if response:
            r["attempted"] += 1
            r["correct"] += marks == full
    for r in out.values():
        r["accuracy"] = round(100 * r["correct"] / r["attempted"]) if r["attempted"] else None
        r["pyq_seen"] = len(r["pyq_seen"])
        r["coverage"] = round(100 * r["pyq_seen"] / r["pyq_total"]) if r["pyq_total"] else 0
    return out


def daily_streak(db: Session, user: User, today: date | None = None) -> int:
    days = set(db.scalars(select(Attempt.day).where(Attempt.user_id == user.id, Attempt.mode == "daily",
                                                    Attempt.submitted_at.is_not(None))))
    d, n = today or date.today(), 0
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    while d.isoformat() in days:
        n, d = n + 1, d - timedelta(days=1)
    return n


def history(db: Session, user: User, limit: int = 20) -> list[Attempt]:
    return list(db.scalars(select(Attempt).where(Attempt.user_id == user.id).order_by(Attempt.started_at.desc())
                           .limit(limit)))


def counts(db: Session) -> dict:
    by = defaultdict(int)
    for exam, origin, status, n in db.execute(select(Question.exam, Question.origin, Question.status, func.count())
                                              .group_by(Question.exam, Question.origin, Question.status)):
        by[(exam, origin, status)] = n
    return by
