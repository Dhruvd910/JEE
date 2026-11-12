"""Checks against the official PDFs in live/raw (git-ignored; skipped when they have not been downloaded)."""
from pathlib import Path

import pytest

from app.config import BASE_DIR
from app.services import papers

RAW = BASE_DIR / "live" / "raw"
pytestmark = pytest.mark.skipif(not (RAW / "main" / "key-s2.pdf").exists(), reason="official PDFs not downloaded")


def test_every_main_shift_matches_the_official_key():
    key = papers.parse_main_key(papers._text(RAW / "main" / "key-s2.pdf"))
    for code in papers.MAIN_2026_S2:
        rows = papers.parse_main_paper(papers._text(RAW / "main" / f"{code}.pdf"))
        assert len(rows) == 75 and {r["subject"] for r in rows} == {"Mathematics", "Physics", "Chemistry"}
        mcq = [r for r in rows if r["type"] == "MCQ"]
        assert len(mcq) == 60 and all(len(r["option_ids"]) == 4 for r in mcq)
        # nearly every question's key is one of its own option IDs (a few are dropped by NTA)
        assert sum(key.get(r["qid"]) in r["option_ids"] for r in mcq) >= 57


def test_advanced_2026_official_answers():
    for n, count in ((1, 48), (2, 54)):
        blocks = papers.split_subjects(papers._text(RAW / "adv" / f"2026_p{n}_english.pdf"))
        answers = papers.adv_answers(papers._text(RAW / "adv" / f"2026_p{n}_solutions_final.pdf"))
        assert [papers.guess_subject(b) for b in blocks] == ["Mathematics", "Physics", "Chemistry"]
        assert sum(sum(s["count"] for s in papers.parse_sections(b)) for b in blocks) == count
        assert sum(len(a) for a in answers) == count
        assert all(papers.parse_adv_answer(v) for a in answers for v in a.values())
