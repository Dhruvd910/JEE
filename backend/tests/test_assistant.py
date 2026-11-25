"""AI assistant: lookup tools, the tool-calling loop (model faked), fallback, chats, and the sidebar toggle."""
from app.models import Chat, ChatMessage, User
from app.services import assistant
from tests.conftest import make_colleges, make_paper


def _user(db):
    u = User(email="a@x.test", name="A", password_hash="x", home_state="Rajasthan")
    db.add(u)
    db.commit()
    return u


def test_tools_read_the_sites_data(db):
    make_colleges(db)
    make_paper(db)
    u = _user(db)
    cut = assistant.run_tool(db, u, "college_cutoffs", {"institute": "NIT Delhi", "quota": "OS"})
    assert cut["institute"] == "National Institute of Technology Delhi" and cut["rank_used"] == "JEE Main"
    assert cut["programs"]["CSE [OS]"][2025]["closing"] == 10000
    assert assistant.run_tool(db, u, "college_cutoffs", {"institute": "IIT Delhi"})["rank_used"] == "JEE Advanced"
    pred = assistant.run_tool(db, u, "predict_colleges", {"main_rank": 11000, "kinds": ["NIT"]})
    assert any(o["institute"].startswith("Malaviya") and o["band"] == "Strong fit" for o in pred["options"])
    assert "−1" in assistant.run_tool(db, u, "exam_pattern", {"exam": "main"})["rules"]
    assert assistant.run_tool(db, u, "my_progress", {})["tests_finished"] == 0
    assert "error" in assistant.run_tool(db, u, "nope", {})


def test_tool_loop_with_a_faked_model(db, monkeypatch):
    make_colleges(db)
    u = _user(db)
    replies = iter([
        {"tool_calls": [{"id": "1", "function": {"name": "college_cutoffs", "arguments": '{"institute": "NIT Delhi"}'}}]},
        {"content": "<think>hidden</think>NIT Delhi CSE closed at **10,000** (JoSAA 2025)."},
    ])
    monkeypatch.setattr(assistant, "_call", lambda c, model, messages, url=assistant.URL: next(replies))
    monkeypatch.setattr(assistant, "_local_model", lambda url: None)
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "k")
    text, sources, model = assistant.answer(db, u, [], "NIT Delhi CSE cutoff?")
    assert text == "NIT Delhi CSE closed at **10,000** (JoSAA 2025)." and sources == ["JoSAA opening/closing ranks"]
    assert model == get_settings().assistant_models[0]


def test_fallback_when_no_model_answers(db, monkeypatch):
    make_colleges(db)
    u = _user(db)
    monkeypatch.setattr(assistant, "_call", lambda *a, **k: None)
    monkeypatch.setattr(assistant, "_local_model", lambda url: None)
    text, _, model = assistant.answer(db, u, [], "Which NIT for rank 11,000?")
    assert model is None and "busy" in text and "Malaviya" in text
    text, _, _ = assistant.answer(db, u, [], "What happened in 2025?")  # a year is not a rank
    assert "Malaviya" not in text


def test_chat_pages(browser, db, monkeypatch):
    monkeypatch.setattr(assistant, "answer", lambda db, user, history, q, voice=False: (f"echo: {q} ({len(history)})", ["x"], "m"))
    browser.register()
    page = browser.get("/assistant").text
    assert "How can I help you" in page and "Which NITs" in page and "mascot/idle.webp" in page
    browser.post("/assistant/send", data={"content": "first"})
    chat = db.query(Chat).one()
    browser.post("/assistant/send", data={"content": "second", "chat_id": chat.id})
    db.expire_all()
    assert [m.content for m in db.query(ChatMessage).order_by(ChatMessage.id)] == ["first", "echo: first (0)", "second", "echo: second (2)"]
    assert "echo: second (2)" in browser.get(f"/assistant?chat={chat.id}").text
    assert browser.post("/assistant/send", data={"content": "  "}).status_code == 422
    other = __import__("tests.conftest", fromlist=["Browser"]).Browser()
    other.register(email="o@x.test")
    assert "echo: first" not in other.get(f"/assistant?chat={chat.id}").text  # someone else's chat stays private
    other.post(f"/assistant/{chat.id}/delete")
    assert db.query(Chat).count() == 1
    browser.post(f"/assistant/{chat.id}/delete")
    assert db.query(Chat).count() == 0


def test_profile_and_sidebar(browser, db):
    browser.register()
    browser.post("/profile", data={"name": "Asha R", "category": "OBC-NCL", "gender": "female", "home_state": "Kerala",
                                   "target_date": "2027-01-21"})
    u = db.query(User).one()
    db.refresh(u)
    assert (u.name, u.category, u.gender, u.home_state, str(u.target_date)) == ("Asha R", "OBC-NCL", "female", "Kerala", "2027-01-21")
    page = browser.get("/dashboard").text
    assert "toggleNav()" in page and 'href="/assistant"' in page and "nav-collapsed" in page


def test_background_jobs_stop_at_their_daily_budget(monkeypatch):
    from app.config import get_settings
    from app.services import ai
    monkeypatch.setattr(get_settings(), "background_daily_calls", 2)
    (get_settings().live_dir / "free_calls.json").unlink(missing_ok=True)
    ai.spend_call()
    ai.spend_call()
    try:
        ai.spend_call()
        raise AssertionError("third call should be refused")
    except ai.DailyLimit:
        pass


def test_tables_in_answers_render_as_tables():
    from app.web.core import chat_text
    html = str(chat_text("| Year | Closing |\n|---|---|\n| 2025 | **1449** |\n> Source: JoSAA"))
    assert "<table" in html and "<td><strong>1449</strong></td>" in html and "|" not in html and "&gt;" not in html
