"""Rank → college matcher on JoSAA final-round closing ranks. No probabilities: past closing ranks only.

Rules applied (JoSAA business rules):
- IIT seats are compared with the JEE Advanced rank; every other institute with the JEE Main rank.
- Category seats (EWS, OBC-NCL, SC, ST) are compared with the category rank; anyone may hold an OPEN seat.
- Female candidates are eligible for both Gender-Neutral and Female-only seats.
- NITs: Home State quota only at institutes in the student's home state, Other State quota elsewhere.
  GO / JK / LA quotas only for Goa / Jammu and Kashmir / Ladakh domiciles.
- Preparatory-course ranks (marked "P") are left out.
Bands use the closing ranks of the last `years` years: within 90% of the toughest year is a stronger fit;
within the range seen is possible; up to 15% beyond the most lenient year is ambitious.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Cutoff, Institute

CATEGORIES = ["OPEN", "EWS", "OBC-NCL", "SC", "ST"]
SPECIAL_QUOTA = {"GO": "Goa", "JK": "Jammu and Kashmir", "LA": "Ladakh"}
BANDS = ("Strong fit", "Possible", "Ambitious")


@dataclass
class Query:
    main_rank: int | None = None  # JEE Main CRL (common rank list)
    main_cat_rank: int | None = None
    adv_rank: int | None = None  # JEE Advanced CRL
    adv_cat_rank: int | None = None
    category: str = "OPEN"
    female: bool = False
    pwd: bool = False
    home_state: str | None = None
    kinds: tuple[str, ...] = ("IIT", "NIT", "IIIT", "GFTI")
    program: str = ""
    years: int = 3


@dataclass
class Option:
    institute: Institute
    program: str
    quota: str
    seat_type: str
    gender: str
    closings: dict[int, int] = field(default_factory=dict)
    rank_used: int = 0
    rank_label: str = ""
    band: str = ""


def _quota_ok(q: Query, inst: Institute, quota: str) -> bool:
    if quota == "AI":
        return True
    if quota in SPECIAL_QUOTA:
        return q.home_state == SPECIAL_QUOTA[quota]
    in_state = bool(q.home_state) and inst.state == q.home_state
    return in_state if quota == "HS" else not in_state  # OS


def predict(db: Session, q: Query) -> tuple[list[Option], list[str]]:
    notes = []
    seat_types = {"OPEN"} | ({q.category} if q.category in CATEGORIES else set())
    if q.pwd:
        seat_types |= {f"{s} (PwD)" for s in seat_types}
    genders = {"Gender-Neutral"} | ({"Female-only (including Supernumerary)"} if q.female else set())
    years = sorted(db.scalars(select(Cutoff.year).distinct()), reverse=True)[: q.years]
    if not years:
        return [], ["No JoSAA data is loaded yet."]
    stmt = (select(Cutoff).options(joinedload(Cutoff.institute)).join(Cutoff.institute)
            .where(Cutoff.year.in_(years), Cutoff.preparatory.is_(False), Cutoff.seat_type.in_(seat_types),
                   Cutoff.gender.in_(genders), Institute.kind.in_(q.kinds)))
    if q.program:
        stmt = stmt.where(Cutoff.program.ilike(f"%{q.program}%"))
    series: dict[tuple, Option] = {}
    for c in db.scalars(stmt):
        inst = c.institute
        if not _quota_ok(q, inst, c.quota):
            continue
        key = (inst.id, c.program, c.quota, c.seat_type, c.gender)
        if key not in series:
            series[key] = Option(inst, c.program, c.quota, c.seat_type, c.gender)
        series[key].closings[c.year] = c.closing
    out = []
    for o in series.values():
        iit = o.institute.kind == "IIT"
        open_seat = o.seat_type.startswith("OPEN")
        rank = (q.adv_rank if open_seat else q.adv_cat_rank) if iit else (q.main_rank if open_seat else q.main_cat_rank)
        if not rank:
            continue
        o.rank_used = rank
        o.rank_label = ("Advanced " if iit else "Main ") + ("CRL" if open_seat else "category rank")
        toughest, lenient = min(o.closings.values()), max(o.closings.values())
        if rank <= 0.9 * toughest:
            o.band = BANDS[0]
        elif rank <= lenient:
            o.band = BANDS[1]
        elif rank <= 1.15 * lenient:
            o.band = BANDS[2]
        else:
            continue
        out.append(o)
    if "IIT" in q.kinds and not q.adv_rank:
        notes.append("IITs need a JEE Advanced rank; enter it to see IIT seats.")
    if q.category != "OPEN" and not (q.main_cat_rank or q.adv_cat_rank):
        notes.append(f"Enter your {q.category} category rank to compare against {q.category} seats.")
    if not q.home_state:
        notes.append("Without a home state, NIT seats are compared on the Other State quota only.")
    order = {b: i for i, b in enumerate(BANDS)}
    out.sort(key=lambda o: (order[o.band], o.institute.nirf_rank or 999, -o.closings[max(o.closings)]))
    return out, notes


def program_table(db: Session, institute_id: int, quota: str | None, seat_type: str, gender: str) -> dict:
    """Closing ranks by program and year for one institute (catalogue detail page)."""
    stmt = select(Cutoff).where(Cutoff.institute_id == institute_id, Cutoff.seat_type == seat_type,
                                Cutoff.gender == gender)
    if quota:
        stmt = stmt.where(Cutoff.quota == quota)
    table: dict[str, dict] = defaultdict(dict)
    for c in db.scalars(stmt.order_by(Cutoff.program)):
        table[c.program][c.year] = (c.opening, c.closing, c.preparatory)
    return dict(table)
