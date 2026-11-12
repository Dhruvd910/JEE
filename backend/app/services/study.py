"""Study tools: syllabus tracker, bookmarks and mistake notebook, custom drills, analytics, JoSAA choice list,
college comparison."""
from __future__ import annotations

import random
import re
from collections import defaultdict

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (Attempt, AttemptItem, Bookmark, ChoiceItem, Cutoff, Institute, Question, SyllabusUnit,
                        UnitProgress, User)
from app.services import exams

SYLLABUS_URL = "https://cdnbbsr.s3waas.gov.in/s3f8e59f4b2fe7c5705bf878bbd494ccdf/uploads/2026/10/20261024683680768.pdf"
UNIT_STATES = ("studied", "revised", "confident")
SMALL = {"and", "of", "in", "its", "to", "the", "for", "with", "related", "containing"}


# ---------------------------------------------------------------- syllabus

def parse_syllabus(text: str) -> list[tuple[str, int, str]]:
    """(subject, unit number, title) from NTA's syllabus PDF text: Mathematics, Physics, Chemistry in order;
    numbering restarts for each subject. The Paper 2 repeat of Mathematics at the end is ignored."""
    out, subjects, prev = [], ["Mathematics", "Physics", "Chemistry"], None
    s = -1
    for m in re.finditer(r"UNITS?\s*(\d+|I)\s*:\s*([^\n]+)", text):
        n = 1 if m.group(1) == "I" else int(m.group(1))
        if prev is None or n < prev:
            s += 1
            if s == len(subjects):
                break
        prev = n
        title = m.group(2).strip().rstrip(":").strip()
        title = " ".join(w if w in ("p-", "d", "f-") else (w.lower() if w.lower() in SMALL and i else w.capitalize())
                         for i, w in enumerate(title.lower().split()))
        title = (title.replace("Calculas", "Calculus").replace("Diffrential", "Differential")
                 .replace("p- Block", "p-Block").replace("d - and f- Block", "d- and f-Block"))
        out.append((subjects[s], n, title))
    return out


def load_syllabus(db: Session) -> int:
    import pypdf
    pdf = get_settings().live_dir / "raw" / "syllabus" / "main.pdf"
    units = parse_syllabus("\n".join(p.extract_text() or "" for p in pypdf.PdfReader(pdf).pages))
    have = {(u.subject, u.number): u for u in db.scalars(select(SyllabusUnit))}
    for subject, n, title in units:
        u = have.get((subject, n))
        if u:
            u.title = title
        else:
            db.add(SyllabusUnit(subject=subject, number=n, title=title, source_url=SYLLABUS_URL))
    db.commit()
    return len(units)


def syllabus(db: Session, user: User) -> dict[str, list[tuple[SyllabusUnit, str | None]]]:
    status = dict(db.execute(select(UnitProgress.unit_id, UnitProgress.status).where(UnitProgress.user_id == user.id)).all())
    out = defaultdict(list)
    for u in db.scalars(select(SyllabusUnit).order_by(SyllabusUnit.subject, SyllabusUnit.number)):
        out[u.subject].append((u, status.get(u.id)))
    return {s: out[s] for s in exams.SUBJECTS if s in out}


def set_unit(db: Session, user: User, unit_id: int, status: str | None) -> None:
    db.execute(delete(UnitProgress).where(UnitProgress.user_id == user.id, UnitProgress.unit_id == unit_id))
    if status in UNIT_STATES:
        db.add(UnitProgress(user_id=user.id, unit_id=unit_id, status=status))
    db.commit()


def syllabus_summary(db: Session, user: User) -> dict[str, dict]:
    """Per subject: units total and weighted completion (studied ⅓, revised ⅔, confident 1)."""
    weight = {"studied": 1, "revised": 2, "confident": 3}
    out = {}
    for subject, units in syllabus(db, user).items():
        done = sum(weight.get(st, 0) for _, st in units)
        out[subject] = {"units": len(units), "touched": sum(1 for _, st in units if st),
                        "percent": round(100 * done / (3 * len(units))) if units else 0}
    return out


# ---------------------------------------------------------------- bookmarks & mistakes

def toggle_bookmark(db: Session, user: User, question_id: int) -> bool:
    b = db.get(Bookmark, (user.id, question_id))
    if b:
        db.delete(b)
    else:
        db.add(Bookmark(user_id=user.id, question_id=question_id))
    db.commit()
    return b is None


def bookmarked_ids(db: Session, user: User) -> set[int]:
    return set(db.scalars(select(Bookmark.question_id).where(Bookmark.user_id == user.id)))


def mistakes(db: Session, user: User, subject: str | None = None, limit: int = 200) -> list[AttemptItem]:
    """The latest answer to every question the student got wrong or only partly right."""
    stmt = (select(AttemptItem).join(AttemptItem.attempt).join(AttemptItem.question)
            .where(Attempt.user_id == user.id, Attempt.submitted_at.is_not(None), AttemptItem.response.is_not(None),
                   AttemptItem.marks < Question.marks_full, Question.status == "served")
            .order_by(Attempt.submitted_at.desc()))
    if subject:
        stmt = stmt.where(Question.subject == subject)
    seen, out = set(), []
    for item in db.scalars(stmt):
        if item.question_id not in seen:
            seen.add(item.question_id)
            out.append(item)
    return out[:limit]


def mistake_counts(db: Session, user: User) -> dict[str, int]:
    out = defaultdict(int)
    for item in mistakes(db, user, limit=10_000):
        out[item.question.subject] += 1
    return dict(out)


# ---------------------------------------------------------------- custom drill

DRILL_SOURCES = {"unseen": "Unseen previous-year questions", "mistakes": "My mistakes (retest)",
                 "bookmarks": "My bookmarks"}


def start_drill(db: Session, user: User, subject: str | None, exam: str | None, qtype: str | None, count: int,
                source: str) -> Attempt | None:
    """A short timed set from official previous-year questions (or the student's mistakes / bookmarks)."""
    count = max(3, min(count, 30))
    if source == "mistakes":
        ids = [i.question_id for i in mistakes(db, user, subject, 500)]
    elif source == "bookmarks":
        ids = list(bookmarked_ids(db, user))
    else:
        ids = None
    stmt = select(Question).where(Question.status == "served", Question.origin == "pyq")
    if ids is not None:
        stmt = select(Question).where(Question.status == "served", Question.id.in_(ids))
    else:
        stmt = stmt.where(Question.id.not_in(exams.seen_ids(db, user)))
    for col, val in ((Question.subject, subject), (Question.exam, exam), (Question.qtype, qtype)):
        if val:
            stmt = stmt.where(col == val)
    pool = list(db.scalars(stmt))
    if not pool:
        return None
    picked = random.sample(pool, min(count, len(pool)))
    picked.sort(key=lambda q: exams.SUBJECTS.index(q.subject) if q.subject in exams.SUBJECTS else 9)
    label = " · ".join(x for x in (subject, {"main": "Main", "advanced": "Advanced"}.get(exam or ""), qtype) if x)
    title = f"Drill · {DRILL_SOURCES[source].split(' (')[0]}" + (f" · {label}" if label else "")
    minutes = sum(3 if q.exam == "advanced" else 2 for q in picked) + 2
    return exams._new_attempt(db, user, "drill", exam or "mixed", title, minutes, picked)


# ---------------------------------------------------------------- analytics

def analytics(db: Session, user: User) -> dict:
    attempts = list(db.scalars(select(Attempt).where(Attempt.user_id == user.id, Attempt.submitted_at.is_not(None))
                               .order_by(Attempt.submitted_at)))
    trend = [{"title": a.title, "mode": a.mode, "pct": round(100 * a.score / a.max_score) if a.max_score else 0,
              "score": a.score, "max": a.max_score, "id": a.id} for a in attempts]
    by_type = defaultdict(lambda: {"given": 0, "attempted": 0, "correct": 0})
    lost, answered, given = 0, 0, 0
    for item, q in db.execute(select(AttemptItem, Question).join(AttemptItem.question).join(AttemptItem.attempt)
                              .where(Attempt.user_id == user.id, Attempt.submitted_at.is_not(None))).all():
        r = by_type[(q.subject, q.qtype)]
        r["given"] += 1
        given += 1
        if item.response:
            r["attempted"] += 1
            answered += 1
            r["correct"] += item.marks == q.marks_full
            lost += -item.marks if (item.marks or 0) < 0 else 0
    for r in by_type.values():
        r["accuracy"] = round(100 * r["correct"] / r["attempted"]) if r["attempted"] else None
    best = max(trend, key=lambda t: t["pct"]) if trend else None
    return {"trend": trend, "by_type": dict(sorted(by_type.items())), "negative_lost": lost,
            "attempt_rate": round(100 * answered / given) if given else None, "tests": len(attempts), "best": best}


def chart_points(trend: list[dict], w: int = 640, h: int = 180, pad: int = 24) -> list[tuple[float, float, dict]]:
    """SVG coordinates for the score-percentage trend line."""
    if not trend:
        return []
    n = len(trend)
    step = (w - 2 * pad) / max(1, n - 1)
    return [(pad + i * step if n > 1 else w / 2, h - pad - (h - 2 * pad) * max(0, t["pct"]) / 100, t)
            for i, t in enumerate(trend)]


# ---------------------------------------------------------------- JoSAA choice list

def choices(db: Session, user: User) -> list[ChoiceItem]:
    return list(db.scalars(select(ChoiceItem).where(ChoiceItem.user_id == user.id).order_by(ChoiceItem.position)))


def add_choice(db: Session, user: User, institute_id: int, program: str) -> bool:
    if not db.scalar(select(Cutoff.id).where(Cutoff.institute_id == institute_id, Cutoff.program == program).limit(1)):
        raise ValueError("No such program at this institute in the JoSAA data.")
    if db.scalar(select(ChoiceItem.id).where(ChoiceItem.user_id == user.id, ChoiceItem.institute_id == institute_id,
                                             ChoiceItem.program == program)):
        return False
    last = db.scalar(select(func.max(ChoiceItem.position)).where(ChoiceItem.user_id == user.id)) or 0
    db.add(ChoiceItem(user_id=user.id, institute_id=institute_id, program=program, position=last + 1))
    db.commit()
    return True


def move_choice(db: Session, user: User, item_id: int, delta: int) -> None:
    items = choices(db, user)
    i = next((k for k, c in enumerate(items) if c.id == item_id), None)
    if i is None:
        return
    j = max(0, min(len(items) - 1, i + delta))
    items.insert(j, items.pop(i))
    for k, c in enumerate(items):
        c.position = k + 1
    db.commit()


def remove_choice(db: Session, user: User, item_id: int) -> None:
    db.execute(delete(ChoiceItem).where(ChoiceItem.id == item_id, ChoiceItem.user_id == user.id))
    db.commit()
    for k, c in enumerate(choices(db, user)):
        c.position = k + 1
    db.commit()


def last_closing(db: Session, items: list[ChoiceItem], user: User) -> dict[int, dict]:
    """Latest-year closing rank for each choice in the student's own pool (OPEN or their category, their gender)."""
    seat = user.category or "OPEN"
    gender = "Female-only (including Supernumerary)" if user.gender == "female" else "Gender-Neutral"
    out = {}
    for c in items:
        rows = db.execute(select(Cutoff.year, Cutoff.quota, Cutoff.closing).where(
            Cutoff.institute_id == c.institute_id, Cutoff.program == c.program, Cutoff.seat_type == seat,
            Cutoff.gender == gender).order_by(Cutoff.year.desc())).all()
        if rows:
            year = rows[0][0]
            out[c.id] = {"year": year, "by_quota": {q: cl for y, q, cl in rows if y == year}}
    return out


# ---------------------------------------------------------------- compare

def compare(db: Session, ids: list[int]) -> dict:
    insts = [i for i in (db.get(Institute, x) for x in ids[:3]) if i]
    year = db.scalar(select(func.max(Cutoff.year)))
    programs: dict[str, dict[int, int]] = defaultdict(dict)
    for inst in insts:
        quota = "AI" if inst.kind in ("IIT", "IIIT", "GFTI") else "OS"
        for program, closing, q in db.execute(select(Cutoff.program, Cutoff.closing, Cutoff.quota).where(
                Cutoff.institute_id == inst.id, Cutoff.year == year, Cutoff.seat_type == "OPEN",
                Cutoff.gender == "Gender-Neutral", Cutoff.quota.in_(("AI", quota)))).all():
            short = re.sub(r"\s*\(.*", "", program)
            programs[short][inst.id] = min(closing, programs[short].get(inst.id, closing))
    shared = {p: v for p, v in programs.items() if len(v) > 1} or dict(programs)
    return {"institutes": insts, "year": year, "programs": dict(sorted(shared.items(), key=lambda kv: min(kv[1].values())))}


def program_trend(db: Session, institute_id: int, program: str, quota: str, seat: str, gender: str) -> list[tuple[int, int]]:
    return list(db.execute(select(Cutoff.year, Cutoff.closing).where(
        Cutoff.institute_id == institute_id, Cutoff.program == program, Cutoff.quota == quota, Cutoff.seat_type == seat,
        Cutoff.gender == gender).order_by(Cutoff.year)).tuples())
