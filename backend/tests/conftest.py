import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="jee-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["LIVE_DIR"] = str(_tmp / "live")
os.environ["OPENROUTER_API_KEY"] = ""  # tests never call models

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cutoff, Institute, Paper, Question  # noqa: E402
from app.security import COOKIE_NAME, csrf_token_for, rate_limiter  # noqa: E402

LIVE = _tmp / "live"


@pytest.fixture(autouse=True)
def fresh_db():
    rate_limiter.hits.clear()  # each test starts with fresh per-IP limits
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


def make_paper(db, exam="main", n_per_subject=2, code="main-test-s1"):
    """A tiny official-style paper: per subject one MCQ (answer option 2) and one numerical (answer 7)."""
    (LIVE / "q" / code).mkdir(parents=True, exist_ok=True)
    img = LIVE / "q" / code / "1.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    paper = Paper(code=code, exam=exam, year=2026, title=f"Test {code}", source_url="https://example.invalid/p.pdf",
                  key_url="https://example.invalid/k.pdf")
    db.add(paper)
    n = 0
    for subject in ("Physics", "Chemistry", "Mathematics"):
        for i in range(n_per_subject):
            n += 1
            num = i % 2 == 1
            db.add(Question(paper=paper, exam=exam, subject=subject, number=n, qtype="num" if num else "single",
                            images=[f"q/{code}/1.png"], answer={"lo": 7.0, "hi": 7.0} if num else {"sets": [[1]]},
                            marks_full=4, marks_neg=1, origin="pyq", source="test"))
    db.commit()
    return paper


def make_variants(db, exam="main", per=30):
    from app.services.exams import MOCKS
    for subject in ("Physics", "Chemistry", "Mathematics"):
        for qtype, _, full, neg in MOCKS[exam]["sections"]:
            for i in range(per):
                ans = {"lo": float(i), "hi": float(i)} if qtype == "num" else {"sets": [[i % 4]]}
                db.add(Question(exam=exam, subject=subject, qtype=qtype, stem=f"{subject} {qtype} v{i}",
                                options=[] if qtype == "num" else ["a", "b", "c", "d"], answer=ans, marks_full=full,
                                marks_neg=neg, origin="variant", source="test variant"))
    db.commit()


def make_colleges(db):
    iit = Institute(name="Indian Institute of Technology Delhi", kind="IIT", state="Delhi", nirf_rank=2)
    nit = Institute(name="National Institute of Technology Delhi", kind="NIT", state="Delhi")
    nit2 = Institute(name="Malaviya National Institute of Technology Jaipur", kind="NIT", state="Rajasthan")
    db.add_all([iit, nit, nit2])
    db.flush()
    rows = []
    for year, close in ((2023, 100), (2024, 110), (2025, 120)):
        rows.append(Cutoff(institute_id=iit.id, program="CSE", year=year, round=6, quota="AI", seat_type="OPEN",
                           gender="Gender-Neutral", opening=1, closing=close, source_url="x"))
        for inst, quota, c in ((nit, "HS", 20000), (nit, "OS", 10000), (nit2, "HS", 30000), (nit2, "OS", 12000)):
            rows.append(Cutoff(institute_id=inst.id, program="CSE", year=year, round=6, quota=quota, seat_type="OPEN",
                               gender="Gender-Neutral", opening=1, closing=c, source_url="x"))
        rows.append(Cutoff(institute_id=nit.id, program="CSE", year=year, round=6, quota="OS", seat_type="OBC-NCL",
                           gender="Gender-Neutral", opening=1, closing=3000, source_url="x"))
        rows.append(Cutoff(institute_id=nit.id, program="CSE", year=year, round=6, quota="OS", seat_type="OPEN",
                           gender="Female-only (including Supernumerary)", opening=1, closing=16000, source_url="x"))
    db.add_all(rows)
    db.commit()


class Browser:
    """TestClient that signs in and sends the CSRF header like HTMX does."""

    def __init__(self):
        self.c = TestClient(app)

    def register(self, email="s@example.test", name="Asha Rao", password="password-123"):
        r = self.c.post("/register", data={"email": email, "name": name, "password": password}, follow_redirects=False)
        assert r.status_code == 303, r.text
        return r

    def headers(self):
        token = self.c.cookies.get(COOKIE_NAME)
        return {"X-CSRF-Token": csrf_token_for(token)} if token else {}

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, **kw):
        return self.c.post(url, data=data, headers={**self.headers(), **kw.pop("headers", {})}, **kw)


@pytest.fixture
def browser():
    return Browser()
