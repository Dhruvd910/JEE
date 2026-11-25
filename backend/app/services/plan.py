"""Week-by-week JEE roadmap from the student's exam date, class, study hours, syllabus tracker and test accuracy.

Nothing is stored except the student's settings: the plan is rebuilt on every view from the syllabus tracker, so ticking
a task (which moves that unit forward) and doing tests reshape it automatically.

- Phases: cover what is left (learn new units, revise studied ones) → final revision with full mocks (the last
  quarter of the time, at most 8 weeks, at least 2).
- Order: weakest subject first (lowest test accuracy, then least syllabus done); units not started before units to
  revise; subjects interleaved each week so no subject goes cold.
- Load: hours a day × 6 study days; a new unit is counted as ~10 hours, a revision as ~4 (NTA gives no hours per
  unit, so these are planning estimates and the page says so).
- Class 11 students in the school year get Class 11 units first (NTA's syllabus lists units in Class 11 → 12 order
  within each subject, so earlier unit numbers come first).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import User
from app.services import exams, study

NEW_HOURS, REVISE_HOURS = 10, 4
CLASSES = {"11": "Class 11", "12": "Class 12", "dropper": "Dropper / repeater"}


@dataclass
class Task:
    kind: str  # learn / revise / drill / mock / quiz
    text: str
    unit_id: int | None = None
    subject: str | None = None
    href: str | None = None


@dataclass
class Week:
    start: date
    phase: str
    tasks: list[Task] = field(default_factory=list)


@dataclass
class Plan:
    exam_date: date
    estimated_date: bool
    weeks: list[Week]
    hours_per_day: int
    remaining_units: int
    notes: list[str]


def default_exam_date(today: date) -> date:
    """When the student hasn't set one: about late January (JEE Main Session 1 has been held in the last week of
    January in recent years). Shown as an estimate; the student should set the real date from NTA's notice."""
    d = date(today.year, 1, 24)
    return d if d > today + timedelta(days=21) else date(today.year + 1, 1, 24)


def build(db: Session, user: User, today: date | None = None) -> Plan:
    today = today or date.today()
    hours = user.study_hours or 6
    exam = user.target_date if user.target_date and user.target_date > today else default_exam_date(today)
    notes = []
    if not user.target_date:
        notes.append("Exam date estimated as late January; set the date from NTA's notice for an exact plan.")
    weeks_left = max(1, (exam - today).days // 7)
    final = min(8, max(2, weeks_left // 4)) if weeks_left > 3 else weeks_left
    cover_weeks = max(0, weeks_left - final)

    # weakest subject first: lower accuracy, then less syllabus done
    progress = exams.progress(db, user)
    summary = study.syllabus_summary(db, user)
    order = sorted(summary, key=lambda s: ((progress.get(s, {}).get("accuracy") if progress.get(s, {}).get("accuracy")
                                            is not None else 50), summary[s]["percent"]))
    queues: dict[str, list[tuple[str, object]]] = {}
    for subject, rows in study.syllabus(db, user).items():
        new = [("learn", u) for u, st in rows if st is None]
        rev = [("revise", u) for u, st in rows if st in ("studied", "revised")]
        if user.class_level == "11":
            new.sort(key=lambda x: x[1].number)
        queues[subject] = new + rev
    remaining = sum(len(q) for q in queues.values())

    capacity = hours * 6  # hours a week
    weeks: list[Week] = []
    start = today - timedelta(days=today.weekday())  # Mondays
    pending = {s: list(queues.get(s, [])) for s in order}
    for i in range(cover_weeks):
        w = Week(start + timedelta(weeks=i), "Cover the syllabus")
        budget = capacity - 3  # leave ~3 hours for the weekly drill and quizzes
        while budget > 0 and any(pending.values()):
            progressed = False
            for s in order:  # interleave subjects
                if pending[s] and budget > 0:
                    kind, u = pending[s].pop(0)
                    cost = NEW_HOURS if kind == "learn" else REVISE_HOURS
                    budget -= cost
                    w.tasks.append(Task(kind, f"{'Learn' if kind == 'learn' else 'Revise'}: {s} · Unit {u.number} {u.title}",
                                        u.id, s, "/syllabus"))
                    progressed = True
            if not progressed:
                break
        weak = order[0] if order else "Physics"
        w.tasks.append(Task("drill", f"Drill: 15 previous-year questions in {weak}", subject=weak, href="/drill"))
        w.tasks.append(Task("quiz", "Daily quiz every day (9 questions, 20 min)", href="/dashboard"))
        if i % 2 == 1:
            w.tasks.append(Task("mock", "Take one full previous-year paper (3 hours) and review every mistake",
                                href="/practice"))
        weeks.append(w)
    left_over = sum(len(q) for q in pending.values())
    if left_over:
        notes.append(f"{left_over} units don't fit before the final phase at {hours} hours a day. Add study hours, "
                     "or focus on revising what you know.")
    for i in range(cover_weeks, weeks_left):
        w = Week(start + timedelta(weeks=i), "Final revision and mocks")
        w.tasks += [Task("mock", "Two full mock tests or previous-year papers under exam conditions", href="/practice"),
                    Task("drill", "Retest your mistakes from this week's mocks", href="/mistakes"),
                    Task("revise", f"Revise formulas and key results: {', '.join(order) or 'all subjects'}", href="/syllabus"),
                    Task("quiz", "Daily quiz every day", href="/dashboard")]
        weeks.append(w)
    return Plan(exam, not user.target_date, weeks, hours, remaining, notes)


def complete(db: Session, user: User, unit_id: int, kind: str) -> None:
    """Ticking a learn task marks the unit studied; a revise task moves it up one step (studied → revised → confident)."""
    current = dict((u.id, st) for rows in study.syllabus(db, user).values() for u, st in rows).get(unit_id)
    nxt = "studied" if kind == "learn" else {"studied": "revised", "revised": "confident"}.get(current, "confident")
    study.set_unit(db, user, unit_id, nxt)
