"""Study plan (roadmap), counselling centre, dashboard plan card, assistant chat groups and the new assistant tools."""
from datetime import date, timedelta

from app.models import SyllabusUnit, User
from app.services import assistant, plan, study


def _units(db):
    for s in ("Physics", "Chemistry", "Mathematics"):
        for n in range(1, 7):
            db.add(SyllabusUnit(subject=s, number=n, title=f"{s} unit {n}", source_url="x"))
    db.commit()


def _user(db, **kw):
    u = User(email="p@x.test", name="P", password_hash="x", **kw)
    db.add(u)
    db.commit()
    return u


def test_plan_covers_units_then_final_revision(db):
    _units(db)
    today = date(2026, 11, 16)  # a Monday
    u = _user(db, target_date=today + timedelta(weeks=20), study_hours=6, class_level="12")
    pl = plan.build(db, u, today)
    assert len(pl.weeks) == 20 and pl.remaining_units == 18 and not pl.estimated_date
    phases = [w.phase for w in pl.weeks]
    assert phases[:15] == ["Cover the syllabus"] * 15 and phases[15:] == ["Final revision and mocks"] * 5
    learn = [t for w in pl.weeks for t in w.tasks if t.kind == "learn"]
    assert len(learn) == 18 and len({t.subject for t in pl.weeks[0].tasks if t.unit_id}) == 3  # subjects interleaved
    assert any(t.kind == "mock" for t in pl.weeks[-1].tasks)


def test_ticking_tasks_moves_units_and_reshapes_the_plan(db):
    _units(db)
    u = _user(db, target_date=date.today() + timedelta(weeks=12))
    first = next(t for t in plan.build(db, u).weeks[0].tasks if t.kind == "learn")
    plan.complete(db, u, first.unit_id, "learn")
    assert dict((x.id, st) for r in study.syllabus(db, u).values() for x, st in r)[first.unit_id] == "studied"
    plan.complete(db, u, first.unit_id, "revise")
    plan.complete(db, u, first.unit_id, "revise")
    assert dict((x.id, st) for r in study.syllabus(db, u).values() for x, st in r)[first.unit_id] == "confident"
    assert plan.build(db, u).remaining_units == 17


def test_too_little_time_warns_and_date_is_estimated_when_missing(db):
    _units(db)
    u = _user(db, study_hours=1)
    pl = plan.build(db, u, date(2026, 11, 16))
    assert pl.estimated_date and pl.exam_date == date(2027, 1, 24)
    assert any("don't fit" in n for n in pl.notes)


def test_pages(browser, db):
    _units(db)
    browser.register()
    browser.post("/study-plan", data={"target_date": (date.today() + timedelta(weeks=10)).isoformat(),
                                      "class_level": "dropper", "study_hours": "8"})
    u = db.query(User).one()
    db.refresh(u)
    assert (u.class_level, u.study_hours) == ("dropper", 8)
    page = browser.get("/study-plan").text
    assert "This week" in page and "Learn:" in page
    unit = db.query(SyllabusUnit).first()
    browser.post(f"/study-plan/done/{unit.id}", data={"kind": "learn"})
    assert "This week's plan" in browser.get("/dashboard").text
    c = browser.get("/counselling").text
    assert "Freeze" in c and "₹30,000" in c and "top 20" in c and "Clause 48" in c
    nav = browser.get("/dashboard").text
    assert 'href="/study-plan"' in nav and 'href="/counselling"' in nav and 'href="/assistant"' in nav


def test_assistant_history_groups_and_tools(browser, db, monkeypatch):
    _units(db)
    monkeypatch.setattr(assistant, "answer", lambda db, user, history, q, voice=False: ("ok", [], "m"))
    browser.register()
    browser.post("/assistant/send", data={"content": "Plan my week"})
    browser.post("/assistant/send", data={"content": "JoSAA fees"})
    page = browser.get("/assistant").text
    assert "Today" in page and "Plan my week" in page
    assert "Plan my week" not in browser.get("/assistant?q=fees").text
    u = db.query(User).one()
    assert assistant.run_tool(db, u, "study_plan", {})["units_left"] == 18
    assert "Freeze" in str(assistant.run_tool(db, u, "counselling_rules", {})["freeze_float_slide"])


def test_jee_info_and_careers_pages_cite_official_sources(browser):
    from app.services import jee_info
    page = browser.get("/jee").text  # public, like the counselling centre
    assert "20, 21, 22, 23, 24 and 28 Jan 2027" in page and "23 May 2027" in page
    assert jee_info.MAIN_SRC[1] in page and jee_info.ADV_SRC[1] in page and "top 2,50,000" in page
    careers = browser.get("/careers").text
    assert "Architecture (B.Arch)" in careers and "https://josaa.nic.in/" in careers
    for s in [jee_info.MAIN_SRC, jee_info.ADV_SRC, jee_info.ADV_HOME] + [r[3] for r in jee_info.ROUTES]:
        assert s[1].startswith("https://") and ("nta.nic.in" in s[1] or "s3waas.gov.in" in s[1] or "jeeadv.ac.in" in s[1]
                                                or "josaa.nic.in" in s[1])  # official sources only
