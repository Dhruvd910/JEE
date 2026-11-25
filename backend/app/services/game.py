"""Game layer over tests: XP, combos, hearts, stars and levels. Everything is worked out from the answers already
stored, so there is nothing extra to save and old tests count too.

- A fully correct answer earns 10 XP × the combo (answers right in a row, up to ×5); a partly correct one earns 5.
- The daily quiz is played with 3 hearts: every wrong answer costs one, and the game ends at zero.
- Finishing any test adds 20 XP. Level n needs 100·(n−1)² XP in total (100, 400, 900 …).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Attempt, User
from app.services.exams import daily_streak, item_marks

XP_RIGHT, XP_PARTIAL, COMBO_MAX, HEARTS, FINISH_BONUS = 10, 5, 5, 3, 20


def run(a: Attempt) -> dict:
    """Replay a test's answers in order: XP so far, the current and best combo, hearts left, right/answered."""
    xp = combo = best = right = answered = 0
    hearts = HEARTS
    for item in a.items:
        if not item.response:
            continue
        answered += 1
        m, full = (item.marks if item.marks is not None else item_marks(item)), item.question.marks_full
        if m == full:
            right += 1
            combo = min(combo + 1, COMBO_MAX)
            best = max(best, combo)
            xp += XP_RIGHT * combo
        else:
            combo = 0
            if m > 0:
                xp += XP_PARTIAL
            else:
                hearts -= 1
    if a.submitted_at is not None:
        xp += FINISH_BONUS
    return {"xp": xp, "combo": combo, "best": best, "hearts": max(hearts, 0), "right": right, "answered": answered,
            "total": len(a.items)}


def stars(a: Attempt) -> int:
    pct = 100 * a.score / a.max_score if a.max_score else 0
    return 3 if pct >= 80 else 2 if pct >= 50 else 1 if pct > 0 else 0


def level_of(xp: int) -> dict:
    n = int((xp / 100) ** 0.5) + 1
    lo, hi = 100 * (n - 1) ** 2, 100 * n ** 2
    return {"level": n, "xp": xp, "into": xp - lo, "span": hi - lo, "pct": round(100 * (xp - lo) / (hi - lo))}


def profile(db: Session, user: User) -> dict:
    """Total XP over every finished test, the level it gives, and the daily-quiz streak."""
    done = db.scalars(select(Attempt).where(Attempt.user_id == user.id, Attempt.submitted_at.is_not(None))
                      .options(selectinload(Attempt.items))).all()
    return {**level_of(sum(run(a)["xp"] for a in done)), "streak": daily_streak(db, user)}
