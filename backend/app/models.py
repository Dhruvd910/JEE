"""Database tables. Every factual record names its official source."""
import enum
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    student = "student"
    admin = "admin"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.student)
    # Used only by the predictor; all optional.
    category: Mapped[str | None] = mapped_column(String(20))
    gender: Mapped[str | None] = mapped_column(String(10))
    home_state: Mapped[str | None] = mapped_column(String(60))
    target_date: Mapped[date | None] = mapped_column(Date)  # the student's exam date, for the countdown
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    login_count: Mapped[int] = mapped_column(Integer, default=0)


class Paper(Base):
    """One official question paper (a JEE Main shift, or JEE Advanced Paper 1/2)."""
    __tablename__ = "papers"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)  # main-2026-04-02-s1, adv-2026-p1
    exam: Mapped[str] = mapped_column(String(10), index=True)  # main / advanced
    year: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(120))
    duration_min: Mapped[int] = mapped_column(Integer, default=180)
    source_url: Mapped[str] = mapped_column(String(500))
    key_url: Mapped[str] = mapped_column(String(500))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    questions: Mapped[list["Question"]] = relationship(back_populates="paper", order_by="Question.number")


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int | None] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    exam: Mapped[str] = mapped_column(String(10), index=True)
    subject: Mapped[str] = mapped_column(String(20), index=True)  # Physics / Chemistry / Mathematics
    number: Mapped[int] = mapped_column(Integer, default=0)  # position in the paper
    section: Mapped[str] = mapped_column(String(40), default="")
    # single (one option) / multi (one or more, partial marks) / match (List-I/II, one option) / num (value)
    qtype: Mapped[str] = mapped_column(String(10))
    images: Mapped[list] = mapped_column(JSON, default=list)  # official rendering, paths under live_dir
    option_images: Mapped[list] = mapped_column(JSON, default=list)
    stem: Mapped[str] = mapped_column(Text, default="")  # text form (variants; transcribed papers)
    options: Mapped[list] = mapped_column(JSON, default=list)
    # {"any": [i]} one of these option indexes; {"set": [i, ...]} exact set; {"lo": x, "hi": y} numerical range
    answer: Mapped[dict] = mapped_column(JSON)
    marks_full: Mapped[int] = mapped_column(Integer, default=4)
    marks_neg: Mapped[int] = mapped_column(Integer, default=1)
    explanation: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(10), default="pyq", index=True)  # pyq / variant
    variant_of: Mapped[int | None] = mapped_column(ForeignKey("questions.id", ondelete="SET NULL"), index=True)
    nta_qid: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(12), default="served", index=True)  # served / dropped / hidden
    source: Mapped[str] = mapped_column(String(300), default="")
    checked_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paper: Mapped[Paper | None] = relationship(back_populates="questions")


class Transcript(Base):
    """A question read into text by two independent free vision models; kept only when both readings agree."""
    __tablename__ = "transcripts"
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True)
    stem: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[list] = mapped_column(JSON, default=list)
    agreed: Mapped[bool] = mapped_column(Boolean, default=False)
    models: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(10))  # paper / mock / daily
    exam: Mapped[str] = mapped_column(String(10))
    paper_id: Mapped[int | None] = mapped_column(ForeignKey("papers.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(160))
    day: Mapped[str | None] = mapped_column(String(10), index=True)  # daily quiz date (YYYY-MM-DD)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    score: Mapped[int | None] = mapped_column(Integer)
    max_score: Mapped[int] = mapped_column(Integer, default=0)
    items: Mapped[list["AttemptItem"]] = relationship(back_populates="attempt", order_by="AttemptItem.position",
                                                      cascade="all, delete-orphan")


class AttemptItem(Base):
    __tablename__ = "attempt_items"
    __table_args__ = (UniqueConstraint("attempt_id", "position"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    response: Mapped[list | None] = mapped_column(JSON)  # option indexes, or [value] for numericals
    # Copied at submission so later bank edits never change a past result.
    answer: Mapped[dict | None] = mapped_column(JSON)
    marks: Mapped[int | None] = mapped_column(Integer)
    attempt: Mapped[Attempt] = relationship(back_populates="items")
    question: Mapped[Question] = relationship()


class Institute(Base):
    __tablename__ = "institutes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)  # as printed by JoSAA
    kind: Mapped[str] = mapped_column(String(6), index=True)  # IIT / NIT / IIIT / GFTI
    state: Mapped[str | None] = mapped_column(String(60))
    city: Mapped[str | None] = mapped_column(String(80))
    nirf_rank: Mapped[int | None] = mapped_column(Integer)
    nirf_score: Mapped[float | None] = mapped_column(Float)
    nirf_id: Mapped[str | None] = mapped_column(String(20))
    nirf_parts: Mapped[dict | None] = mapped_column(JSON)  # TLR, RPC, GO, OI, PR


class Cutoff(Base):
    """One row of JoSAA's opening/closing rank table (final round)."""
    __tablename__ = "cutoffs"
    __table_args__ = (UniqueConstraint("institute_id", "program", "year", "round", "quota", "seat_type", "gender"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    institute_id: Mapped[int] = mapped_column(ForeignKey("institutes.id", ondelete="CASCADE"), index=True)
    program: Mapped[str] = mapped_column(String(250), index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    round: Mapped[int] = mapped_column(Integer)
    quota: Mapped[str] = mapped_column(String(4))  # AI / HS / OS / GO / JK / LA
    seat_type: Mapped[str] = mapped_column(String(20))  # OPEN, EWS, OBC-NCL, SC, ST, … (PwD)
    gender: Mapped[str] = mapped_column(String(50))
    opening: Mapped[int] = mapped_column(Integer)
    closing: Mapped[int] = mapped_column(Integer)
    preparatory: Mapped[bool] = mapped_column(Boolean, default=False)  # ranks from the preparatory-course list
    source_url: Mapped[str] = mapped_column(String(300))
    institute: Mapped[Institute] = relationship()


class SyllabusUnit(Base):
    """A unit of NTA's official JEE Main syllabus."""
    __tablename__ = "syllabus_units"
    __table_args__ = (UniqueConstraint("subject", "number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(20))
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(String(300))


class UnitProgress(Base):
    __tablename__ = "unit_progress"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("syllabus_units.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(12))  # studied / revised / confident
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Bookmark(Base):
    __tablename__ = "bookmarks"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChoiceItem(Base):
    """One line of a student's JoSAA choice-filling list (their own ordering)."""
    __tablename__ = "choice_items"
    __table_args__ = (UniqueConstraint("user_id", "institute_id", "program"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    institute_id: Mapped[int] = mapped_column(ForeignKey("institutes.id", ondelete="CASCADE"))
    program: Mapped[str] = mapped_column(String(250))
    position: Mapped[int] = mapped_column(Integer)
    institute: Mapped[Institute] = relationship()
