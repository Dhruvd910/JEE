"""Website flows: accounts, tests, mocks, daily quiz, predictor, catalogue, admin."""
from datetime import date

from sqlalchemy import select

from app.models import Attempt, AttemptItem, Institute, Question, Role, User
from app.services import exams, predictor
from tests.conftest import Browser, make_colleges, make_paper, make_variants


def test_register_login_logout(browser):
    browser.register()
    assert "Hey Asha" in browser.get("/dashboard").text
    browser.post("/logout")
    assert browser.get("/dashboard", follow_redirects=False).headers["location"].startswith("/login")
    r = browser.c.post("/login", data={"email": "s@example.test", "password": "wrong"})
    assert r.status_code == 400
    r = browser.c.post("/login", data={"email": "s@example.test", "password": "password-123", "next": "//evil.example"},
                       follow_redirects=False)
    assert r.headers["location"] == "/dashboard"  # no open redirect


def test_duplicate_email_and_short_password(browser):
    browser.register()
    assert browser.c.post("/register", data={"email": "s@example.test", "name": "B", "password": "password-123"}).status_code == 400
    assert browser.c.post("/register", data={"email": "t@example.test", "name": "B", "password": "short"}).status_code == 400


def test_csrf_required_for_signed_in_posts(browser, db):
    make_paper(db)
    browser.register()
    assert browser.c.post("/practice/daily").status_code == 403
    assert browser.post("/practice/daily", follow_redirects=False).status_code == 303


def test_take_paper_save_submit_score(browser, db):
    paper = make_paper(db)
    browser.register()
    r = browser.post(f"/practice/paper/{paper.id}", follow_redirects=False)
    aid = int(r.headers["location"].split("/")[-1])
    page = browser.get(f"/attempt/{aid}").text
    assert "Q1" in page and "/img/q/main-test-s1/1.png" in page
    browser.post(f"/attempt/{aid}/q/1", data={"r": "1"})  # correct MCQ: +4
    browser.post(f"/attempt/{aid}/q/2", data={"v": "8"})  # wrong numerical: −1
    browser.post(f"/attempt/{aid}/q/3", data={"r": "0"})  # wrong MCQ: −1
    assert "enter a number" in browser.post(f"/attempt/{aid}/q/4", data={"v": "abc"}).text
    browser.post(f"/attempt/{aid}/submit")
    db.expire_all()
    a = db.get(Attempt, aid)
    assert (a.score, a.max_score) == (2, 24)
    res = browser.get(f"/attempt/{aid}/result").text
    assert ">2<span" in res and "/24</span>" in res
    # closed tests refuse changes
    r = browser.post(f"/attempt/{aid}/q/1", data={"r": "2"})
    assert r.headers.get("hx-redirect") == f"/attempt/{aid}/result"


def test_deadline_enforced_on_server(browser, db):
    paper = make_paper(db)
    browser.register()
    aid = int(browser.post(f"/practice/paper/{paper.id}", follow_redirects=False).headers["location"].split("/")[-1])
    a = db.get(Attempt, aid)
    a.deadline = a.started_at.replace(year=2000)
    db.commit()
    assert browser.get(f"/attempt/{aid}", follow_redirects=False).headers["location"].endswith("/result")


def test_other_students_cannot_open_attempts_or_images(browser, db):
    paper = make_paper(db)
    browser.register()
    aid = int(browser.post(f"/practice/paper/{paper.id}", follow_redirects=False).headers["location"].split("/")[-1])
    other = Browser()
    other.register(email="o@example.test")
    assert other.get(f"/attempt/{aid}").status_code == 404
    assert other.get("/img/q/main-test-s1/1.png").status_code == 200
    assert other.get("/img/../.env").status_code == 404
    assert other.get("/img/q/../../conftest.py").status_code == 404
    assert Browser().get("/img/q/main-test-s1/1.png", follow_redirects=False).status_code == 303  # signed-out


def test_mocks_never_repeat_questions(db):
    make_paper(db)
    make_variants(db, "main", per=50)
    user = User(email="m@example.test", name="M", password_hash="x")
    db.add(user)
    db.commit()
    seen = set()
    for _ in range(2):
        a = exams.start_mock(db, user, "main")
        ids = {i.question_id for i in a.items}
        assert len(a.items) == 75 and len(ids) == 75 and not ids & seen
        assert all(i.question.origin == "variant" for i in a.items)  # previous-year papers stay separate
        seen |= ids
    assert exams.start_mock(db, user, "main") is None  # 50 per slot: a third mock would repeat
    assert exams.mock_shortfall(db, user, "main")


def test_mock_preparing_message_when_bank_is_short(browser, db):
    make_paper(db)
    browser.register()
    r = browser.post("/practice/mock/advanced")
    assert "Not enough unseen checked questions" in r.text


def test_daily_quiz_same_for_everyone_once_per_day(db):
    make_paper(db, n_per_subject=10)
    u1, u2 = User(email="a@x.test", name="A", password_hash="x"), User(email="b@x.test", name="B", password_hash="x")
    db.add_all([u1, u2])
    db.commit()
    d = date(2026, 11, 12)
    a1, a2 = exams.start_daily(db, u1, d), exams.start_daily(db, u2, d)
    assert [i.question_id for i in a1.items] == [i.question_id for i in a2.items]
    assert len(a1.items) == 9 and exams.start_daily(db, u1, d).id == a1.id
    exams.finish(db, a1)
    assert exams.daily_streak(db, u1, d) == 1 and exams.daily_streak(db, u2, d) == 0


def test_progress_by_subject(db):
    paper = make_paper(db)
    user = User(email="p@x.test", name="P", password_hash="x")
    db.add(user)
    db.commit()
    a = exams.start_paper(db, user, paper)
    exams.save_response(db, a, 1, [1])
    exams.save_response(db, a, 3, [0])
    exams.finish(db, a)
    p = exams.progress(db, user)
    assert p["Physics"]["accuracy"] == 100 and p["Chemistry"]["accuracy"] == 0 and p["Mathematics"]["accuracy"] is None
    assert p["Physics"]["pyq_seen"] == 2 and p["Physics"]["coverage"] == 100


def test_predictor_rules(db):
    make_colleges(db)
    out, notes = predictor.predict(db, predictor.Query(main_rank=11000, adv_rank=105, home_state="Rajasthan"))
    got = {(o.institute.name.split()[-1], o.quota, o.band) for o in out}
    assert ("Delhi", "AI", "Possible") in got  # IIT on the Advanced rank
    assert ("Delhi", "OS", "Ambitious") in got  # NIT Delhi: other-state quota for a Rajasthan student
    assert ("Jaipur", "HS", "Strong fit") in got  # home-state quota at a home-state NIT
    assert not any(o.quota == "HS" and o.institute.state != "Rajasthan" for o in out)
    # female candidates also see Female-only seats; category seats need a category rank
    out, notes = predictor.predict(db, predictor.Query(main_rank=15000, female=True, category="OBC-NCL"))
    assert any(o.gender.startswith("Female") for o in out)
    assert any("category rank" in n for n in notes)
    out, _ = predictor.predict(db, predictor.Query(main_rank=15000, main_cat_rank=2500, category="OBC-NCL"))
    assert any(o.seat_type == "OBC-NCL" and o.band == "Strong fit" for o in out)


def test_predictor_and_catalogue_pages(browser, db):
    make_colleges(db)
    r = browser.get("/predictor?go=1&main_rank=11000&adv_rank=105&category=OPEN&gender=male&home_state=Rajasthan&kinds=IIT&kinds=NIT")
    assert r.status_code == 200 and "Malaviya" in r.text
    assert "Indian Institute of Technology Delhi" in browser.get("/colleges?kind=IIT").text
    assert "National Institute" not in browser.get("/colleges?kind=IIT").text
    inst = db.query(Institute).filter_by(kind="NIT", state="Delhi").one()
    page = browser.get(f"/colleges/{inst.id}?quota=OS").text
    assert "10,000" in page and "CSE" in page


def test_admin_console(browser, db):
    paper = make_paper(db)
    browser.register()
    assert browser.get("/admin", follow_redirects=False).headers["location"] == "/admin/login"
    user = db.scalar(select(User))
    user.role = Role.admin
    db.commit()
    assert "Health checklist" in browser.get("/admin").text
    assert browser.get(f"/admin/users/{user.id}").status_code == 200
    q = paper.questions[0]
    assert "Q1" in browser.get(f"/admin/papers/{paper.id}").text
    for tab, text in (("students", "Student register"), ("questions", "Official papers"), ("data", "JoSAA opening")):
        assert text in browser.get(f"/admin?tab={tab}").text
    assert "hidden" in browser.post(f"/admin/questions/{q.id}/toggle").text
    db.expire_all()
    assert db.get(Question, q.id).status == "hidden"
    assert db.query(AttemptItem).count() == 0
