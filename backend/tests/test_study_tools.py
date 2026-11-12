"""Drills, mistake notebook, bookmarks, syllabus tracker, analytics, choice list, compare, countdown."""
from datetime import date, timedelta

from app.models import Attempt, ChoiceItem, Question, SyllabusUnit, User
from app.services import exams, study
from tests.conftest import make_colleges, make_paper

SYLLABUS_TEXT = """MATHEMATICS UNIT 1: SETS, RELATIONS AND FUNCTIONS:
text
UNIT3: MATRICES AND DETERMINANTS:
UNIT 8: INTEGRAL CALCULAS:
UNIT 1: Units and Measurements
UNIT 2: Kinematics
CHEMISTRY PHYSICAL CHEMISTRY
UNIT I: SOME BASIC CONCEPTS IN CHEMISTRY
UNIT 10: p- BLOCK ELEMENTS
UNITS 15: HYDROCARBONS
UNIT 1: SETS, RELATIONS AND FUNCTIONS:
"""


def user(db, email="u@x.test"):
    u = User(email=email, name="U", password_hash="x")
    db.add(u)
    db.commit()
    return u


def test_parse_official_syllabus():
    units = study.parse_syllabus(SYLLABUS_TEXT)
    assert units[:3] == [("Mathematics", 1, "Sets, Relations and Functions"), ("Mathematics", 3, "Matrices and Determinants"),
                         ("Mathematics", 8, "Integral Calculus")]
    assert ("Physics", 2, "Kinematics") in units and ("Chemistry", 1, "Some Basic Concepts in Chemistry") in units
    assert ("Chemistry", 10, "p-Block Elements") in units and ("Chemistry", 15, "Hydrocarbons") in units
    assert len(units) == 8  # the Paper 2 maths repeat is ignored


def test_syllabus_tracker(browser, db):
    db.add_all([SyllabusUnit(subject="Physics", number=n, title=f"U{n}", source_url="x") for n in (1, 2, 3)])
    db.commit()
    browser.register()
    me = db.query(User).one()
    unit = db.query(SyllabusUnit).first()
    r = browser.post(f"/syllabus/{unit.id}", data={"status": "confident"})
    assert "on-confident" in r.text and "33%" in r.text  # one of three units fully done
    assert study.syllabus_summary(db, me)["Physics"] == {"units": 3, "touched": 1, "percent": 33}
    browser.post(f"/syllabus/{unit.id}", data={"status": ""})  # pressing it again clears it
    assert study.syllabus_summary(db, me)["Physics"]["touched"] == 0
    assert "Map the" in browser.get("/syllabus").text


def test_mistakes_bookmarks_and_retest(browser, db):
    paper = make_paper(db)
    browser.register()
    aid = int(browser.post(f"/practice/paper/{paper.id}", follow_redirects=False).headers["location"].split("/")[-1])
    browser.post(f"/attempt/{aid}/q/1", data={"r": "1"})  # right
    browser.post(f"/attempt/{aid}/q/3", data={"r": "0"})  # wrong
    browser.post(f"/attempt/{aid}/submit")
    me = db.query(User).one()
    wrong = [i.question_id for i in study.mistakes(db, me)]
    assert len(wrong) == 1 and study.mistake_counts(db, me) == {"Chemistry": 1}
    assert "Turn mistakes into" in browser.get("/mistakes").text
    q = db.get(Question, wrong[0])
    assert "Bookmarked" in browser.post(f"/bookmark/{q.id}").text
    assert study.bookmarked_ids(db, me) == {q.id}
    assert "Bookmarked" in browser.get("/mistakes?tab=bookmarks").text
    r = browser.post("/drill", data={"source": "mistakes", "count": "10"}, follow_redirects=False)
    drill = db.query(Attempt).filter_by(mode="drill").one()
    assert r.headers["location"] == f"/attempt/{drill.id}" and [i.question_id for i in drill.items] == wrong


def test_drill_filters_and_unseen_first(db):
    make_paper(db, n_per_subject=4)
    u = user(db)
    a = study.start_drill(db, u, "Physics", "main", "num", 10, "unseen")
    assert {(i.question.subject, i.question.qtype) for i in a.items} == {("Physics", "num")} and len(a.items) == 2
    assert study.start_drill(db, u, "Physics", "main", "num", 10, "unseen") is None  # all seen now
    assert study.start_drill(db, u, None, None, None, 3, "bookmarks") is None


def test_analytics_and_dashboard(browser, db):
    paper = make_paper(db)
    browser.register()
    for resp in ("1", "0"):
        aid = int(browser.post(f"/practice/paper/{paper.id}", follow_redirects=False).headers["location"].split("/")[-1])
        browser.post(f"/attempt/{aid}/q/1", data={"r": resp})
        browser.post(f"/attempt/{aid}/submit")
    me = db.query(User).one()
    data = study.analytics(db, me)
    assert [t["score"] for t in data["trend"]] == [4, -1] and data["negative_lost"] == 1
    assert data["by_type"][("Physics", "single")] == {"given": 2, "attempted": 2, "correct": 1, "accuracy": 50}
    assert len(study.chart_points(data["trend"])) == 2
    assert "performance telemetry" in browser.get("/analytics").text
    browser.post("/target-date", data={"target_date": (date.today() + timedelta(days=30)).isoformat()})
    page = browser.get("/dashboard").text
    assert "days left" in page and ">30<" in page and "<polyline" in page


def test_choice_list(browser, db):
    make_colleges(db)
    browser.register()
    from app.models import Institute
    iit = db.query(Institute).filter_by(kind="IIT").one()
    nit = db.query(Institute).filter_by(name="National Institute of Technology Delhi").one()
    assert "in choice list" in browser.post("/choices", data={"institute_id": nit.id, "program": "CSE"}).text
    assert "already listed" in browser.post("/choices", data={"institute_id": nit.id, "program": "CSE"}).text
    assert "No such program" in browser.post("/choices", data={"institute_id": nit.id, "program": "Basket weaving"}).text
    browser.post("/choices", data={"institute_id": iit.id, "program": "CSE"})
    items = db.query(ChoiceItem).order_by(ChoiceItem.position).all()
    browser.post(f"/choices/{items[1].id}/up")
    db.expire_all()
    order = [c.institute.kind for c in db.query(ChoiceItem).order_by(ChoiceItem.position)]
    assert order == ["IIT", "NIT"]
    page = browser.get("/choices").text
    assert "OS</span> 10,000" in page and "AI</span> 120" in page
    csv = browser.get("/choices.csv").text.splitlines()
    assert csv[0] == "preference,institute,type,program" and csv[1].startswith("1,Indian Institute of Technology Delhi")
    browser.post(f"/choices/{items[0].id}/delete")
    assert db.query(ChoiceItem).count() == 1
    other = __import__("tests.conftest", fromlist=["Browser"]).Browser()
    other.register(email="o@x.test")
    other.post(f"/choices/{items[1].id}/delete")  # someone else's item: no effect
    assert db.query(ChoiceItem).count() == 1


def test_compare(browser, db):
    make_colleges(db)
    from app.models import Institute
    ids = [i.id for i in db.query(Institute).filter_by(kind="NIT")]
    data = study.compare(db, ids)
    assert data["year"] == 2025 and data["programs"]["CSE"] == {ids[0]: 10000, ids[1]: 12000}
    page = browser.get("/compare?" + "&".join(f"ids={i}" for i in ids)).text
    assert "Malaviya" in page and "12,000" in page


def test_new_pages_need_sign_in(browser):
    for url in ("/drill", "/mistakes", "/analytics", "/syllabus", "/choices"):
        assert browser.get(url, follow_redirects=False).status_code == 303
    assert browser.get("/compare").status_code == 200 and browser.get("/").status_code == 200
