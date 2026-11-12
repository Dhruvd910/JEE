"""Transcription and mock-version pipeline, with the free-model calls faked."""
from app.models import Question, Transcript
from app.services import ai
from tests.conftest import make_paper


def fake_models(monkeypatch, checker_answer=2):
    def ask(c, model, content, temperature=0.0):
        text = content if isinstance(content, str) else content[0]["text"]
        if text.startswith("These images"):  # both readers see the same thing (with different notation)
            return {"stem": "A ball of mass 2 kg moves at v₁ = 3 m/s" if "nano" in model else
                    "A ball of mass 2 kg moves at v_1 = 3 m/s", "options": ["1", "2", "3", "4"]} if "Option 1" in str(content) or "four options" in text \
                else {"stem": "Find x² if x = 3", "options": []}
        if text.startswith("Rewrite"):
            return {"stem": "A ball of mass 5 kg moves at 4 m/s. Find its momentum.", "options": ["10", "20", "25", "40"],
                    "answer": 2, "solution": "p = mv = 20"} if "options" not in text or "(1)" in text else \
                {"stem": "Find x² if x = 4", "options": [], "answer": 16}
        return {"answer": checker_answer if "(1)" in text else 16, "confident": True, "well_posed": True}
    monkeypatch.setattr(ai, "ask", ask)
    monkeypatch.setattr(ai, "PACE", 0)
    monkeypatch.setattr(ai, "_img", lambda path: {"type": "text", "text": "[image]"})


def test_grow_transcribes_then_keeps_checked_versions(db, monkeypatch):
    fake_models(monkeypatch)
    make_paper(db)
    out = ai.grow(db, 1, "main", "Physics", "single")
    assert out.get("transcript agreed") == 1 and out.get("kept") == 1, out
    v = db.query(Question).filter_by(origin="variant").one()
    assert (v.subject, v.qtype, v.answer, v.marks_full, v.marks_neg) == ("Physics", "single", {"sets": [[1]]}, 4, 1)
    assert v.variant_of and "Not expert-reviewed" in v.source
    assert db.query(Transcript).filter_by(agreed=True).count() == 1


def test_checker_disagreement_drops_the_version(db, monkeypatch):
    fake_models(monkeypatch, checker_answer=3)
    make_paper(db)
    out = ai.grow(db, 1, "main", "Physics", "single")
    assert out.get("checker disagreed") == 1 and not db.query(Question).filter_by(origin="variant").count()


def test_daily_limit_stops_cleanly(db, monkeypatch):
    def ask(*a, **k):
        raise ai.DailyLimit("free-models-per-day")
    monkeypatch.setattr(ai, "ask", ask)
    monkeypatch.setattr(ai, "_img", lambda path: {"type": "text", "text": "[image]"})
    make_paper(db)
    assert ai.grow(db, 2, "main") == {"stopped: free daily limit": 1}
