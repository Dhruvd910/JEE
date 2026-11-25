"""AI assistant for JEE aspirants (free OpenRouter models only).

The model is never the source of truth for facts: cutoffs, colleges, marking schemes, the syllabus and the student's
own progress come from tools that read this site's database (official JoSAA, NIRF, NTA and JEE Advanced data).
Free models are tried in order; if none answers, a plain fallback says so and points to the right page.
"""
from __future__ import annotations

import json
import logging
import re

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Chat, ChatMessage, Cutoff, Institute, Paper, User
from app.services import exams, predictor, study

log = logging.getLogger(__name__)
URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM = """You are the JEE Workbench assistant for Indian students preparing for JEE Main and JEE Advanced and for
JoSAA counselling (IITs, NITs, IIITs, GFTIs).

Rules:
- Facts about closing ranks, colleges, NIRF ranks, marking schemes, the syllabus and the student's own scores must come
  from tool results. If no tool gives the fact, say you could not verify it and point to the official source (NTA,
  jeeadv.ac.in, JoSAA, NIRF). Never invent ranks, dates or numbers.
- Name the source and year for cutoffs (e.g. "JoSAA 2025 final round"). IIT ranks are JEE Advanced ranks; NIT/IIIT/GFTI
  ranks are JEE Main ranks. Predictions are past-cutoff fits ("Strong fit", "Possible", "Ambitious"), never
  probabilities or guarantees.
- Study advice (how to plan, revise, attempt papers) may come from general knowledge; keep it practical and specific to
  the student's own data when a tool gives it.
- Be warm and encouraging; never shame a student about marks, rank or category. Keep answers short and use bullet
  points for lists. Reply in the student's language (English, Hindi or Hinglish)."""

TOOLS = [
    {"name": "my_progress",
     "description": "The signed-in student's own data: subject accuracy, previous-year coverage, test scores trend, "
                    "marks lost to negatives, mistakes to fix, syllabus completion, daily streak, exam date, choice list.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "search_colleges",
     "description": "Find institutes in the JoSAA catalogue by name or city, type (IIT/NIT/IIIT/GFTI) or state. "
                    "Returns NIRF 2025 rank and state.",
     "parameters": {"type": "object", "properties": {
         "q": {"type": "string"}, "kind": {"type": "string", "enum": ["IIT", "NIT", "IIIT", "GFTI"]},
         "state": {"type": "string"}}}},
    {"name": "college_cutoffs",
     "description": "JoSAA final-round opening/closing ranks for one institute, by program and year.",
     "parameters": {"type": "object", "required": ["institute"], "properties": {
         "institute": {"type": "string", "description": "institute name, e.g. 'NIT Trichy' or 'IIT Bombay'"},
         "program": {"type": "string", "description": "words in the program name, e.g. 'Computer'"},
         "seat_type": {"type": "string", "description": "OPEN, EWS, OBC-NCL, SC, ST (default OPEN)"},
         "female": {"type": "boolean"}, "quota": {"type": "string", "description": "AI, HS or OS"}}}},
    {"name": "predict_colleges",
     "description": "Programs where students with this rank got seats in the last three JoSAA years.",
     "parameters": {"type": "object", "properties": {
         "main_rank": {"type": "integer", "description": "JEE Main CRL rank"},
         "main_category_rank": {"type": "integer"}, "advanced_rank": {"type": "integer"},
         "advanced_category_rank": {"type": "integer"},
         "category": {"type": "string", "enum": predictor.CATEGORIES}, "female": {"type": "boolean"},
         "home_state": {"type": "string"}, "program": {"type": "string"},
         "kinds": {"type": "array", "items": {"type": "string", "enum": ["IIT", "NIT", "IIIT", "GFTI"]}}}}},
    {"name": "exam_pattern",
     "description": "Official marking scheme and paper pattern for JEE Main or JEE Advanced, and the official papers "
                    "available to practise here.",
     "parameters": {"type": "object", "properties": {"exam": {"type": "string", "enum": ["main", "advanced"]}}}},
    {"name": "study_plan",
     "description": "The student's week-by-week study plan (roadmap): exam date, phases, this week's and next weeks' "
                    "tasks, units left. Use for 'what should I study', planning and time questions.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "counselling_rules",
     "description": "Official JoSAA 2026-27 Business Rules summary: process steps, Freeze/Float/Slide, seat acceptance "
                    "and partial admission fees, eligibility (75% / top 20 percentile), quotas, withdrawal, schedule.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "syllabus",
     "description": "Units of NTA's official JEE Main syllabus for a subject, with the student's status for each.",
     "parameters": {"type": "object", "properties": {
         "subject": {"type": "string", "enum": ["Physics", "Chemistry", "Mathematics"]}}}},
]
SOURCE_NAMES = {"my_progress": "Your progress on JEE Workbench", "search_colleges": "JoSAA catalogue + NIRF 2025",
                "college_cutoffs": "JoSAA opening/closing ranks", "predict_colleges": "JoSAA closing ranks (3 years)",
                "exam_pattern": "NTA bulletin / JEE Advanced paper", "syllabus": "NTA JEE Main syllabus",
                "study_plan": "Your study plan", "counselling_rules": "JoSAA Business Rules 2026-27"}
ALIASES = {"trichy": "tiruchirappalli", "nitk": "karnataka", "surathkal": "karnataka", "bhu": "bhu", "ism": "ism",
           "dhanbad": "ism", "iitb": "bombay", "iitd": "delhi", "iitm": "madras", "iitk": "kanpur", "iitkgp": "kharagpur"}


# ---------------------------------------------------------------- tools

def _find_institute(db: Session, name: str) -> Institute | None:
    words = [ALIASES.get(w, w) for w in re.findall(r"[a-z]+", name.lower())]
    kind = next((k for k in ("IIT", "NIT", "IIIT") if k.lower() in words), None)
    words = [w for w in words if w not in ("iit", "nit", "iiit", "institute", "of", "technology", "national", "indian")]
    stmt = select(Institute)
    if kind:
        stmt = stmt.where(Institute.kind == kind)
    best, score = None, 0
    for inst in db.scalars(stmt):
        hay = (inst.name + " " + (inst.city or "")).lower()
        s = sum(w in hay for w in words)
        if s > score:
            best, score = inst, s
    return best


def run_tool(db: Session, user: User, name: str, args: dict) -> dict:
    if name == "my_progress":
        a = study.analytics(db, user)
        return {"subjects": exams.progress(db, user), "tests_finished": a["tests"],
                "recent_scores_pct": [t["pct"] for t in a["trend"][-8:]], "best_pct": a["best"]["pct"] if a["best"] else None,
                "attempt_rate_pct": a["attempt_rate"], "marks_lost_to_negatives": a["negative_lost"],
                "accuracy_by_type": {f"{s} {t}": r["accuracy"] for (s, t), r in a["by_type"].items()},
                "mistakes_to_fix": study.mistake_counts(db, user), "syllabus": study.syllabus_summary(db, user),
                "daily_streak": exams.daily_streak(db, user),
                "exam_date": user.target_date.isoformat() if user.target_date else None,
                "choice_list": [f"{c.institute.name} — {c.program}" for c in study.choices(db, user)[:15]]}
    if name == "search_colleges":
        stmt = select(Institute)
        if args.get("q"):
            q = ALIASES.get(args["q"].lower(), args["q"])
            stmt = stmt.where(or_(Institute.name.ilike(f"%{q}%"), Institute.city.ilike(f"%{q}%")))
        if args.get("kind"):
            stmt = stmt.where(Institute.kind == args["kind"])
        if args.get("state"):
            stmt = stmt.where(Institute.state.ilike(args["state"]))
        rows = db.scalars(stmt.order_by(Institute.nirf_rank.is_(None), Institute.nirf_rank).limit(15)).all()
        return {"institutes": [{"name": i.name, "type": i.kind, "state": i.state, "nirf_2025_rank": i.nirf_rank} for i in rows]}
    if name == "college_cutoffs":
        inst = _find_institute(db, args.get("institute", ""))
        if inst is None:
            return {"error": "No institute with that name in the JoSAA data."}
        gender = "Female-only (including Supernumerary)" if args.get("female") else "Gender-Neutral"
        stmt = select(Cutoff).where(Cutoff.institute_id == inst.id, Cutoff.seat_type == (args.get("seat_type") or "OPEN"),
                                    Cutoff.gender == gender, Cutoff.preparatory.is_(False))
        if args.get("program"):
            stmt = stmt.where(Cutoff.program.ilike(f"%{args['program']}%"))
        if args.get("quota"):
            stmt = stmt.where(Cutoff.quota == args["quota"].upper())
        rows = {}
        for c in db.scalars(stmt.order_by(Cutoff.program, Cutoff.year.desc())):
            rows.setdefault(f"{c.program} [{c.quota}]", {})[c.year] = {"opening": c.opening, "closing": c.closing}
        return {"institute": inst.name, "type": inst.kind, "rank_used": "JEE Advanced" if inst.kind == "IIT" else "JEE Main",
                "seat_type": args.get("seat_type") or "OPEN", "gender_pool": gender, "nirf_2025_rank": inst.nirf_rank,
                "programs": dict(list(rows.items())[:25]), "source": "JoSAA final round"}
    if name == "predict_colleges":
        q = predictor.Query(main_rank=args.get("main_rank"), main_cat_rank=args.get("main_category_rank"),
                            adv_rank=args.get("advanced_rank"), adv_cat_rank=args.get("advanced_category_rank"),
                            category=args.get("category") or user.category or "OPEN",
                            female=bool(args.get("female", user.gender == "female")),
                            home_state=args.get("home_state") or user.home_state, program=args.get("program") or "",
                            kinds=tuple(args.get("kinds") or ("IIT", "NIT", "IIIT", "GFTI")))
        out, notes = predictor.predict(db, q)
        return {"notes": notes, "count": len(out), "options": [
            {"band": o.band, "institute": o.institute.name, "program": o.program, "quota": o.quota, "seat": o.seat_type,
             "closing_by_year": o.closings, "rank_used": o.rank_label} for o in out[:20]], "source": "JoSAA final rounds"}
    if name == "exam_pattern":
        exam = args.get("exam") or "main"
        papers = [p.title for p in db.scalars(select(Paper).where(Paper.exam == exam).order_by(Paper.code))]
        if exam == "main":
            rules = ("JEE Main (NTA Information Bulletin 2025/2026): 75 questions, 300 marks, 3 hours. Per subject "
                     "(Physics, Chemistry, Mathematics) 20 MCQs + 5 numerical-value questions, all compulsory. "
                     "Marking: +4 correct, −1 wrong, 0 unanswered, for MCQs and for numericals.")
        else:
            rules = ("JEE Advanced: two compulsory 3-hour papers. Sections change every year and are stated in the "
                     "paper. In 2026 Paper 1: single correct +3/−1, multiple correct +4 with partial +3/+2/+1 and −1, "
                     "numerical +4 (no negative), matching lists +4/−1.")
        return {"rules": rules, "official_papers_here": papers, "mock_format": exams.MOCKS[exam]}
    if name == "study_plan":
        from app.services import plan
        pl = plan.build(db, user)
        return {"exam_date": pl.exam_date.isoformat(), "exam_date_estimated": pl.estimated_date,
                "hours_per_day": pl.hours_per_day, "units_left": pl.remaining_units, "notes": pl.notes,
                "weeks": [{"week_of": w.start.isoformat(), "phase": w.phase, "tasks": [t.text for t in w.tasks]}
                          for w in pl.weeks[:3]], "total_weeks": len(pl.weeks)}
    if name == "counselling_rules":
        from app.services import counselling as c
        return {"source": c.SOURCE, "steps": c.STEPS, "freeze_float_slide": c.OPTIONS, "eligibility": c.ELIGIBILITY,
                "quotas": c.QUOTAS, "withdrawal": c.WITHDRAWAL, "schedule_2026": c.SCHEDULE_2026}
    if name == "syllabus":
        units = study.syllabus(db, user)
        subject = args.get("subject")
        return {s: [{"unit": u.number, "title": u.title, "your_status": st or "not started"} for u, st in rows]
                for s, rows in units.items() if not subject or s == subject}
    return {"error": f"Unknown tool {name}"}


# ---------------------------------------------------------------- model loop

def _call(c: httpx.Client, model: str, messages: list, url: str = URL) -> dict | None:
    try:
        r = c.post(url, json={"model": model, "messages": messages, "max_tokens": 1500, "temperature": 0.3,
                              "tools": [{"type": "function", "function": t} for t in TOOLS]}, timeout=90)
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("assistant %s failed: %s", model, e)
        return None
    if "choices" not in data:
        log.info("assistant %s: %s", model, str(data.get("error", data))[:160])
        return None
    return data["choices"][0]["message"]


def answer(db: Session, user: User, history: list[dict], question: str, voice: bool = False) -> tuple[str, list, str | None]:
    """(reply, sources used, model) for the student's question, given earlier turns of this chat."""
    s = get_settings()
    system = SYSTEM + ("\nThe student is listening by voice: answer in at most four short spoken sentences, no lists or "
                       "symbols." if voice else "")
    messages = [{"role": "system", "content": system}, *history[-10:], {"role": "user", "content": question}]
    used: list[str] = []
    backends = [(URL, m, {"Authorization": f"Bearer {s.openrouter_api_key}", "X-Title": "JEE Workbench"})
                for m in s.assistant_models if s.openrouter_api_key]
    local = _local_model(s.local_llm_url)
    if local:  # LM Studio on this PC: the last backup when the free pool is busy or used up for the day
        backends.append((s.local_llm_url, local, {}))
    for url, model, headers in backends:
        with httpx.Client(headers=headers) as c:
            convo = list(messages)
            for _ in range(5):  # tool rounds
                msg = _call(c, model, convo, url)
                if msg is None:
                    break
                calls = msg.get("tool_calls") or []
                if not calls:
                    text = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
                    if text:
                        return text, sorted(set(used)), model
                    break
                convo.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                for call in calls[:4]:
                    fn = call.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    try:
                        result = run_tool(db, user, fn.get("name", ""), args if isinstance(args, dict) else {})
                    except Exception as e:  # noqa: BLE001 - a bad tool call must not end the chat
                        log.warning("tool %s failed: %s", fn.get("name"), e)
                        result = {"error": "lookup failed"}
                    if fn.get("name") in SOURCE_NAMES:
                        used.append(SOURCE_NAMES[fn["name"]])
                    convo.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                  "content": json.dumps(result, default=str)[:12000]})
    return fallback(db, user, question), sorted(set(used)), None


def _local_model(chat_url: str) -> str | None:
    """The model loaded in LM Studio, or None when it is not running."""
    if not chat_url:
        return None
    try:
        data = httpx.get(chat_url.rsplit("/chat/completions", 1)[0] + "/models", timeout=2).json()
        return data["data"][0]["id"] if data.get("data") else None
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return None


def fallback(db: Session, user: User, question: str) -> str:
    """No free model answered: give what the site's own data can say, plainly."""
    # only a number written as a rank ("rank 12000", "12,000 rank", "AIR 5000"), never a year
    m = re.search(r"(?:rank|air|crl)\D{0,12}?(\d[\d,]*)|(\d[\d,]*)\s*(?:rank|air|crl)", question, re.I)
    nums = [int((m.group(1) or m.group(2)).replace(",", ""))] if m else []
    if nums:
        out, _ = predictor.predict(db, predictor.Query(main_rank=nums[0], adv_rank=nums[0] if "adv" in question.lower() else None,
                                                       category=user.category or "OPEN", home_state=user.home_state))
        if out:
            lines = [f"- {o.band}: {o.institute.name} — {o.program} ({o.quota}, closing {max(o.closings)}: "
                     f"{o.closings[max(o.closings)]:,})" for o in out[:6]]
            return ("The AI models are busy right now, so here is what JoSAA's last three years say for rank "
                    f"{nums[0]:,}:\n" + "\n".join(lines) + "\n\nSee the full list on the **Predictor** page.")
    return ("The free AI models are busy right now, so I can't answer this one. Please try again in a minute. "
            "Meanwhile: **Predictor** for colleges by rank, **Colleges** for closing ranks, **Analytics** for your scores.")


# ---------------------------------------------------------------- chats

def chats(db: Session, user: User) -> list[Chat]:
    return list(db.scalars(select(Chat).where(Chat.user_id == user.id).order_by(Chat.updated_at.desc()).limit(50)))


def send(db: Session, user: User, chat_id: int | None, text: str, voice: bool = False) -> Chat:
    text = text.strip()[:2000]
    if not text:
        raise ValueError("Type a question first.")
    chat = db.get(Chat, chat_id) if chat_id else None
    if chat is None or chat.user_id != user.id:
        chat = Chat(user_id=user.id, title=text[:80])
        db.add(chat)
        db.flush()
    history = [{"role": m.role, "content": m.content} for m in chat.messages]
    db.add(ChatMessage(chat=chat, role="user", content=text))
    db.commit()
    reply, sources, model = answer(db, user, history, text, voice)
    db.add(ChatMessage(chat=chat, role="assistant", content=reply, sources=sources, model=model))
    chat.updated_at = func.now()
    db.commit()
    db.refresh(chat)
    return chat
