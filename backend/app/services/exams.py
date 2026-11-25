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
DAILY_COUNT, DAILY_MINUTES = 5, 10  # the daily quiz is a 5-question game


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
    pools = []
    for subject in SUBJECTS[day.toordinal() % 3:] + SUBJECTS[:day.toordinal() % 3]:  # the 2-2-1 split rotates daily
        ids = sorted(db.scalars(select(Question.id).where(Question.origin == "pyq", Question.status == "served",
                                                          Question.subject == subject)))
        pools.append(rng.sample(ids, len(ids)))
    picked = []
    while len(picked) < DAILY_COUNT and any(pools):
        for pool in pools:
            if pool and len(picked) < DAILY_COUNT:
                picked.append(pool.pop())
    return [db.get(Question, i) for i in picked]


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


def begin(db: Session, a: Attempt) -> Attempt:
    """Leave the instructions screen: the full time starts now (unless answers were already given)."""
    if a.begun_at is None and is_open(a):
        now = utcnow()
        if not any(i.response for i in a.items):
            a.deadline, a.started_at = now + (_aware(a.deadline) - _aware(a.started_at)), now
        a.begun_at = now
        db.commit()
    return a


def note_visit(db: Session, a: Attempt, position: int, seconds: int, marked: bool | None) -> AttemptItem:
    """Exam room: the student moved off a question after `seconds` on it, maybe marking it for review."""
    if not is_open(a):
        finish(db, a)
        raise PermissionError("This test is closed.")
    item = next(i for i in a.items if i.position == position)
    item.visited = True
    item.seconds = (item.seconds or 0) + max(0, min(seconds, 3600))  # one call never adds more than an hour
    if marked is not None:
        item.marked = marked
    db.commit()
    return item


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


QTYPE_NAMES = {"single": "single correct", "multi": "one or more correct", "match": "matching list", "num": "numerical"}


def outcome(item: AttemptItem) -> str:
    """correct / partial / wrong / blank, for a submitted item."""
    if not item.response:
        return "blank"
    m = item.marks if item.marks is not None else item_marks(item)
    return "correct" if m == item.question.marks_full else "partial" if m > 0 else "wrong"


def insights(a: Attempt) -> dict:
    """Scorecard detail from this attempt alone: results by subject and question type, and plain notes on
    what to work on. Every note is a count from the attempt, never a guess about the real exam."""
    rows: dict[tuple[str, str], dict] = {}
    for item in a.items:
        q = item.question
        r = rows.setdefault((q.subject, q.qtype), {"subject": q.subject, "qtype": q.qtype, "n": 0, "score": 0, "max": 0,
                                                     "correct": 0, "partial": 0, "wrong": 0, "blank": 0, "secs": 0})
        r["n"] += 1
        r["max"] += q.marks_full
        r["score"] += item.marks or 0
        r["secs"] += item.seconds or 0
        r[outcome(item)] += 1
    for r in rows.values():
        tried = r["n"] - r["blank"]
        r["accuracy"] = round(100 * (r["correct"] + r["partial"]) / tried) if tried else None
        r["avg_secs"] = round(r["secs"] / r["n"]) if r["secs"] else None

    notes = []
    items = list(a.items)
    lost = -sum(i.marks for i in items if (i.marks or 0) < 0)
    if lost:
        worst = min((r for r in rows.values() if r["wrong"]), key=lambda r: r["accuracy"] if r["accuracy"] is not None else 100)
        notes.append(f"You lost {lost} mark{'s' if lost > 1 else ''} to wrong answers. Accuracy was lowest in {worst['subject']} "
                     f"{QTYPE_NAMES[worst['qtype']]} questions ({worst['accuracy']}% of those you tried): "
                     "answer these only when you can rule out the other options.")
    subj = {}
    for r in rows.values():
        s = subj.setdefault(r["subject"], [0, 0])
        s[0] += r["score"]
        s[1] += r["max"]
    pct = {k: v[0] / v[1] for k, v in subj.items() if v[1]}
    if len(pct) > 1 and max(pct.values()) - min(pct.values()) >= 0.1:  # only when one subject clearly lags
        weak = min(pct, key=pct.get)
        notes.append(f"{weak} was your lowest-scoring subject ({subj[weak][0]}/{subj[weak][1]}). "
                     "Retest your mistakes there before the next full paper.")
    blank = sum(1 for i in items if not i.response)
    if a.submitted_at and items and blank / len(items) >= 0.3:
        spare = int((_aware(a.deadline) - _aware(a.submitted_at)).total_seconds() // 60)
        notes.append(f"You left {blank} of {len(items)} questions blank" +
                     (f" and submitted with {spare} minutes still on the clock. Use spare time on marked questions."
                      if spare >= 10 else ". Practise timed sections to get through the paper."))
    marked_blank = [i.position for i in items if i.marked and not i.response]
    if marked_blank:
        notes.append(f"Marked for review but never answered: Q{', Q'.join(map(str, marked_blank[:8]))}. "
                     "Leave a few minutes at the end to go back to these.")
    timed = [i for i in items if i.seconds]
    if len(timed) >= 5:
        avg = sum(i.seconds for i in timed) / len(timed)
        slow = sorted((i for i in timed if i.seconds > 2 * avg and outcome(i) in ("wrong", "blank")),
                      key=lambda i: -i.seconds)[:3]
        if slow:
            notes.append("These took over twice your average time and still did not score: " +
                         ", ".join(f"Q{i.position} ({i.seconds // 60}m {i.seconds % 60}s)" for i in slow) +
                         ". Move on sooner and come back if time allows.")
    if not notes and items and all(outcome(i) == "correct" for i in items):
        notes.append("Every question right. Try a full mock next.")
    order = {s: k for k, s in enumerate(dict.fromkeys(i.question.subject for i in items))}
    return {"rows": sorted(rows.values(), key=lambda r: (order[r["subject"]], r["qtype"])), "notes": notes}


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
