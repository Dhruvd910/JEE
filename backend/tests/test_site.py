"""Website flows: accounts, tests, mocks, daily quiz, predictor, catalogue, admin."""
from datetime import date

from sqlalchemy import select

from app.models import Attempt, AttemptItem, Institute, Paper, Question, Role, User
from app.services import exams, predictor
from tests.conftest import Browser, make_colleges, make_paper, make_variants


def test_register_login_logout(browser):
    browser.register()
    assert "Welcome back, Asha" in browser.get("/dashboard").text
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


def test_pdf_only_papers_listed_without_start(browser, db):
    from app.services import papers
    playable = make_paper(db)
    assert papers.load_library(db) == 31 and papers.load_library(db) == 0  # idempotent
    browser.register()
    page = browser.get("/practice").text
    assert f"/practice/paper/{playable.id}" in page  # a loaded paper can be started
    assert "JEE Advanced 2016 · Paper 1" in page and papers.adv_url(2016, 1) in page  # an old year: PDF link only
    pdf_only = db.scalar(select(Paper).where(Paper.code == "adv-2016-p1"))
    assert f"/practice/paper/{pdf_only.id}" not in page
    assert 'href=""' not in page  # papers without a key (or the 2015 Main key without a paper) show no empty link


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


def test_exam_room_instructions_palette_time_and_focus(browser, db):
    paper = make_paper(db)
    browser.register()
    aid = int(browser.post(f"/practice/paper/{paper.id}", follow_redirects=False).headers["location"].split("/")[-1])
    page = browser.get(f"/attempt/{aid}").text
    assert "I have read and understood the instructions" in page and 'data-begun=""' in page
    assert "Mark for Review &amp; Next" in page and 'id="pal-1"' in page
    from datetime import timedelta
    a = db.get(Attempt, aid)
    a.started_at, a.deadline = exams.utcnow() - timedelta(minutes=20), exams.utcnow() + timedelta(minutes=160)
    db.commit()  # 20 minutes on the instructions: the full 180 still start at Start
    secs = int(browser.post(f"/attempt/{aid}/begin").text)
    assert 170 * 60 < secs <= 180 * 60
    assert 'data-begun="1"' in browser.get(f"/attempt/{aid}").text
    # moving off Q1 unanswered after 40 s: red; marking Q2 for review: violet; answering + marking Q3: violet with the dot
    assert 'class="pal skip"' in browser.post(f"/attempt/{aid}/seen/1", data={"secs": "40"}).text
    assert 'class="pal rev"' in browser.post(f"/attempt/{aid}/seen/2", data={"secs": "5", "mark": "1"}).text
    browser.post(f"/attempt/{aid}/q/3", data={"r": "1"})
    assert 'class="pal rev-done"' in browser.post(f"/attempt/{aid}/seen/3", data={"secs": "99999", "mark": "1"}).text
    assert 'class="pal done"' in browser.post(f"/attempt/{aid}/seen/3", data={"secs": "0", "mark": "0"}).text
    browser.post(f"/attempt/{aid}/away")
    browser.post(f"/attempt/{aid}/away")
    db.expire_all()
    a = db.get(Attempt, aid)
    assert a.focus_lost == 2 and [i.seconds for i in a.items[:3]] == [40, 5, 3600]  # one move adds at most an hour
    browser.post(f"/attempt/{aid}/submit")
    res = browser.get(f"/attempt/{aid}/result").text
    assert "left the test window 2 times" in res and "40s" in res
    assert browser.post(f"/attempt/{aid}/seen/1", data={"secs": "5"}).headers.get("hx-redirect") == f"/attempt/{aid}/result"


def test_scorecard_notes_types_filters_and_reports(browser, db):
    paper = make_paper(db)
    browser.register()
    aid = int(browser.post(f"/practice/paper/{paper.id}", follow_redirects=False).headers["location"].split("/")[-1])
    browser.post(f"/attempt/{aid}/begin")
    browser.post(f"/attempt/{aid}/q/1", data={"r": "1"})  # Physics MCQ right
    browser.post(f"/attempt/{aid}/q/3", data={"r": "0"})  # Chemistry MCQ wrong
    browser.post(f"/attempt/{aid}/seen/4", data={"secs": "30", "mark": "1"})  # Chemistry numerical marked, left blank
    browser.post(f"/attempt/{aid}/submit")
    res = browser.get(f"/attempt/{aid}/result").text
    assert "What to work on" in res and "You lost 1 mark to wrong answers" in res
    assert "Chemistry was your lowest-scoring subject" in res and "Marked for review but never answered: Q4" in res
    assert "By question type" in res and "numerical" in res
    wrong = browser.get(f"/attempt/{aid}/result?filter=wrong").text
    review = wrong[wrong.index('id="review"'):]
    assert ">Q3<" in review and ">Q1<" not in review
    marked = browser.get(f"/attempt/{aid}/result?filter=marked").text
    assert ">Q4<" in marked[marked.index('id="review"'):]
    # report a problem: once per open question, shown to the admin, who can resolve it
    r = browser.post(f"/attempt/{aid}/report/3", data={"reason": "wrong_key", "note": "key says B, should be C"})
    assert "sent for checking" in r.text
    browser.post(f"/attempt/{aid}/report/3", data={"reason": "wrong_key"})
    from app.models import QuestionReport
    reports = db.scalars(select(QuestionReport)).all()
    assert len(reports) == 1 and reports[0].note == "key says B, should be C"
    db.query(User).filter_by(email="s@example.test").update({"role": Role.admin})
    db.commit()
    bank = browser.get("/admin?tab=questions").text
    assert "Answer key wrong" in bank and "key says B, should be C" in bank
    browser.post(f"/admin/reports/{reports[0].id}/resolve")
    assert "(0 open)" in browser.get("/admin?tab=questions").text
    other = Browser()
    other.register(email="o@example.test")
    assert other.post(f"/attempt/{aid}/report/3", data={"reason": "other"}).status_code == 404


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
    assert len(a1.items) == 5 and exams.start_daily(db, u1, d).id == a1.id
    exams.finish(db, a1)
    assert exams.daily_streak(db, u1, d) == 1 and exams.daily_streak(db, u2, d) == 0


def test_daily_quiz_game_lives_combo_xp(browser, db):
    from app.services import game
    make_paper(db, n_per_subject=4)
    browser.register()
    aid = int(browser.post("/practice/daily", follow_redirects=False).headers["location"].split("/")[-1])
    a = db.get(Attempt, aid)
    right = lambda it: {"v": "7"} if it.question.qtype == "num" else {"r": "1"}
    wrong = lambda it: {"v": "3"} if it.question.qtype == "num" else {"r": "0"}
    assert "Lock in" in browser.get(f"/attempt/{aid}").text
    items = sorted(a.items, key=lambda i: i.position)
    r = browser.post(f"/attempt/{aid}/play/1", data=right(items[0])).text
    assert "Correct!" in r and "+10 XP" in r
    r = browser.post(f"/attempt/{aid}/play/2", data=right(items[1])).text
    assert "+20 XP" in r and "combo ×2" in r  # the second in a row counts double
    assert browser.post(f"/attempt/{aid}/play/1", data=wrong(items[0])).headers.get("hx-redirect") == f"/attempt/{aid}"  # locked
    assert "Pick an answer" in browser.post(f"/attempt/{aid}/play/3", data={}).text
    for pos in (3, 4):
        assert "Not quite" in browser.post(f"/attempt/{aid}/play/{pos}", data=wrong(items[pos - 1])).text
    r = browser.post(f"/attempt/{aid}/play/5", data=wrong(items[4])).text
    assert "Game over" in r  # third wrong answer: out of lives
    db.expire_all()
    a = db.get(Attempt, aid)
    g = game.run(a)
    assert a.submitted_at and g["hearts"] == 0 and g["xp"] == 10 + 20 + game.FINISH_BONUS and a.score == 8 - 3
    page = browser.get(f"/attempt/{aid}/result").text
    assert "+50 XP" in page and "Level 1" in page and "★" in page
    assert game.level_of(100)["level"] == 2 and game.level_of(399)["level"] == 2 and game.level_of(400)["level"] == 3


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
