"""Free-model jobs (OpenRouter ':free' models only; no paid calls).

1. transcribe: two independent vision models read each official question image into text. The reading is kept
   only when both agree (stem and every option nearly identical) and neither says a diagram is needed.
2. variants: a writer model makes a new version of an agreed question (new numbers or wording), solving it;
   a different checker model solves it without seeing the key. Only versions where both answers agree are kept.
   Versions feed the mocks, so mocks never show the exact paper and never repeat for a student.

Free models allow about 20 calls a minute and ~1000 a day, so jobs pace themselves and stop at the daily limit.
"""
from __future__ import annotations

import base64
import difflib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.config import get_settings
from app.models import Question, Transcript
from app.services.exams import MOCKS

log = logging.getLogger(__name__)
URL = "https://openrouter.ai/api/v1/chat/completions"
PACE = 3.2  # seconds between calls (free pool ~20 a minute)


class DailyLimit(Exception):
    pass


def client() -> httpx.Client:
    return httpx.Client(timeout=180, headers={"Authorization": f"Bearer {get_settings().openrouter_api_key}",
                                              "X-Title": "JEE Workbench"})


def ask(c: httpx.Client, model: str, content, temperature: float = 0.0) -> dict | None:
    """One call; returns the first JSON object in the reply, or None. Retries once when the free pool is busy."""
    for wait in (0, 30):
        time.sleep(wait)
        try:
            r = c.post(URL, json={"model": model, "temperature": temperature, "max_tokens": 6000,
                                  "messages": [{"role": "user", "content": content}]})
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("%s failed: %s", model, e)
            continue
        if "per-day" in str(data.get("error", "")):
            raise DailyLimit(str(data["error"])[:160])
        if "choices" not in data:
            log.info("%s: %s", model, str(data.get("error", data))[:160])
            continue
        text = data["choices"][0]["message"].get("content") or ""
        m = re.search(r"\{.*\}", text, re.S)
        try:
            return json.loads(m.group(0)) if m else None
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- 1. transcription

READ_PROMPT = """These images are one question from an official JEE {exam} paper{opts}.
Transcribe it exactly into plain text. Write mathematics with Unicode symbols (², ³, √, π, θ, ∫, →, ≤) and
^ or _ only where needed, e.g. x^(n+1). Keep every number, unit and sign exactly as printed. Do not solve it.
If a diagram or figure carries information that text cannot capture, set needs_figure to true.
Reply with JSON only: {{"stem": "...", "options": [{opt_hint}], "needs_figure": false}}"""


def _img(path: str) -> dict:
    data = (get_settings().live_dir / path).read_bytes()
    mime = "image/png" if path.endswith(".png") else "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}


SUPER = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉ℂℝℕℤ−–×∑", "01234567890123456789CRNZ--xΣ")


def _norm(s: str) -> str:
    """Notation-blind form for comparing two readings: z₁ = z_1, x² = x^2, ℂ = C."""
    return re.sub(r"[\s.,;:()\[\]{}'\"`*_^\\$]+", "", str(s).translate(SUPER).lower())


def agree(a: dict, b: dict) -> bool:
    if a.get("needs_figure") or b.get("needs_figure"):
        return False
    oa, ob = a.get("options") or [], b.get("options") or []
    if len(oa) != len(ob):
        return False
    pairs = [(a.get("stem", ""), b.get("stem", ""))] + list(zip(oa, ob))
    return all(difflib.SequenceMatcher(None, _norm(x), _norm(y)).ratio() >= 0.9 for x, y in pairs) and bool(_norm(a.get("stem", "")))


def read_question(q: Question, c: httpx.Client) -> tuple[list[dict], list[str]]:
    """Two independent readings of one question (network only, so it can run in a worker thread)."""
    s = get_settings()
    has_opts = q.qtype != "num"
    if q.option_images:
        content = [{"type": "text", "text": "Question:"}] + [_img(p) for p in q.images]
        for i, p in enumerate(q.option_images):
            content += [{"type": "text", "text": f"Option {i + 1}:"}, _img(p)]
    else:
        content = [_img(p) for p in q.images]
    prompt = READ_PROMPT.format(exam="Main" if q.exam == "main" else "Advanced",
                                opts=" with four options" if has_opts else " (numerical answer, no options)",
                                opt_hint='"option 1", "option 2", "option 3", "option 4"' if has_opts else "")
    reads, used = [], []
    for model in s.transcribe_models:  # the next reader stands in when one is busy
        r = ask(c, model, [{"type": "text", "text": prompt}] + content)
        time.sleep(PACE)
        if r:
            reads.append(r)
            used.append(model)
        if len(reads) == 2:
            break
    return reads, used


def store_transcript(db: Session, q: Question, reads: list[dict], used: list[str]) -> str:
    has_opts = q.qtype != "num"
    ok = len(reads) == 2 and agree(reads[0], reads[1]) and len(reads[0].get("options") or []) == (4 if has_opts else 0)
    first = reads[0] if reads else {}
    db.merge(Transcript(question_id=q.id, stem=str(first.get("stem", "")), options=[str(o) for o in first.get("options") or []],
                        agreed=bool(ok), models=" + ".join(used)))
    db.commit()
    return "agreed" if ok else "disagreed"


WORKERS = 3  # questions in flight at once: free models take 20-60 s a call but allow ~20 calls a minute


def parallel(fn, items, c: httpx.Client):
    """fn(item, client) for each item in worker threads, yielding (item, result) as each finishes. All database
    work stays in the caller's thread."""
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {pool.submit(fn, it, c): it for it in items}
        try:
            for f in as_completed(futures):
                yield futures[f], f.result()
        except DailyLimit:
            for f in futures:
                f.cancel()
            raise


def transcribe_many(db: Session, qs: list[Question], c: httpx.Client, out: dict) -> None:
    for q, (reads, used) in parallel(read_question, qs, c):
        r = "transcript " + store_transcript(db, q, reads, used)
        out[r] = out.get(r, 0) + 1


def transcribe_batch(db: Session, limit: int) -> dict:
    todo = db.scalars(select(Question).where(Question.origin == "pyq", Question.status == "served",
                                             Question.id.not_in(select(Transcript.question_id)))
                      .order_by(func.random()).limit(limit)).all()
    out: dict[str, int] = {}
    with client() as c:
        try:
            transcribe_many(db, list(todo), c, out)
        except DailyLimit:
            out["stopped: free daily limit"] = 1
    return out


# ---------------------------------------------------------------- 2. variants

STYLE = {
    "single": "a single-correct MCQ with exactly four options, exactly one correct",
    "multi": "a JEE Advanced multiple-correct MCQ with four options (A)-(D), one or more correct",
    "match": "a JEE Advanced List-I/List-II matching question with four options, exactly one correct",
    "num": "a numerical-value question with no options",
}
WRITE_PROMPT = """Rewrite this JEE {exam} {subject} question so a student who has seen it meets a new question on the
same idea. Change the numbers to new realistic values (and recompute) or, for theory, reword and change which facts
are used. Keep it {style}, at the same difficulty and within the JEE syllabus. {num_rule}
Then solve your version carefully.

Question:
{stem}
{options}
Reply with JSON only: {{"stem": "...", "options": [{opt_hint}], "answer": {ans_hint}, "solution": "<short working>"}}"""
CHECK_PROMPT = """Solve this JEE {exam} {subject} question carefully. {kind}

Question:
{stem}
{options}
Reply with JSON only: {{"answer": {ans_hint}, "confident": true or false, "well_posed": true or false}}"""


def _fmt_options(opts: list[str]) -> str:
    return "\n".join(f"({i + 1}) {o}" for i, o in enumerate(opts)) + ("\n" if opts else "")


def _ans_hint(qtype: str) -> str:
    return {"num": "<number>", "multi": "[list of correct option numbers 1-4]"}.get(qtype, "<correct option number 1-4>")


def _parse_answer(qtype: str, v) -> dict | None:
    try:
        if qtype == "num":
            x = float(v)
            return {"lo": x, "hi": x}
        picks = sorted({int(i) - 1 for i in (v if isinstance(v, list) else [v])})
        if not picks or any(not 0 <= i <= 3 for i in picks) or (qtype != "multi" and len(picks) != 1):
            return None
        return {"sets": [picks]}
    except (TypeError, ValueError):
        return None


def same_answer(a: dict, b: dict) -> bool:
    if "lo" in a:
        x, y = a["lo"], b.get("lo")
        return y is not None and abs(x - y) <= max(0.01, 0.01 * abs(x))
    return a.get("sets") == b.get("sets")


def draft_variant(job: dict, c: httpx.Client) -> dict | str:
    """Write and independently check one new version (network only). Returns the version, or why it was dropped."""
    s = get_settings()
    exam = "Main" if job["exam"] == "main" else "Advanced"
    qtype = job["qtype"]
    has_opts = qtype != "num"
    num_rule = "The answer must be a single number" + (" that is an integer." if job["exam"] == "main" else
                                                       " (give it to two decimal places if not an integer).") if not has_opts else ""
    raw = ask(c, s.variant_writer_model, WRITE_PROMPT.format(
        exam=exam, subject=job["subject"], style=STYLE[qtype], num_rule=num_rule, stem=job["stem"],
        options=_fmt_options(job["options"]), opt_hint='"", "", "", ""' if has_opts else "", ans_hint=_ans_hint(qtype)), 0.7)
    time.sleep(PACE)
    if not raw or not str(raw.get("stem", "")).strip():
        return "writer gave no question"
    stem, opts = str(raw["stem"]).strip(), [str(o).strip() for o in raw.get("options") or []] if has_opts else []
    if has_opts and (len(opts) != 4 or len({o.lower() for o in opts}) < 4 or not all(opts)):
        return "malformed options"
    answer = _parse_answer(qtype, raw.get("answer"))
    if answer is None or (job["exam"] == "main" and qtype == "num" and answer["lo"] != int(answer["lo"])):
        return "malformed answer"
    if _norm(stem) == _norm(job["stem"]):
        return "not changed"
    kind = {"multi": "One or more options may be correct.", "num": "The answer is a number."}.get(qtype, "Exactly one option is correct.")
    check, model = None, None
    for model in s.variant_checker_models:
        check = ask(c, model, CHECK_PROMPT.format(exam=exam, subject=job["subject"], kind=kind, stem=stem,
                                                  options=_fmt_options(opts), ans_hint=_ans_hint(qtype)))
        time.sleep(PACE)
        if check:
            break
    if not check:
        return "no checker available"
    theirs = _parse_answer(qtype, check.get("answer"))
    if not theirs or not same_answer(answer, theirs):
        return "checker disagreed"
    if not (check.get("confident") and check.get("well_posed")):
        return "checker unsure"
    return {"stem": stem, "options": opts, "answer": answer, "solution": str(raw.get("solution") or "")[:4000],
            "checked_by": model}


def store_variant(db: Session, src: Question, v: dict) -> None:
    s = get_settings()
    full, neg = next((f, n) for qt, _, f, n in MOCKS[src.exam]["sections"] if qt == src.qtype)
    db.add(Question(exam=src.exam, subject=src.subject, qtype=src.qtype, stem=v["stem"], options=v["options"],
                    answer=v["answer"], marks_full=full, marks_neg=neg, explanation=v["solution"] or None,
                    origin="variant", variant_of=src.id, status="served", checked_by=v["checked_by"],
                    source=f"New version of a {src.paper.title if src.paper else 'bank'} question, written by "
                           f"{s.variant_writer_model} and solved independently by {v['checked_by']}. Not expert-reviewed."))
    db.commit()


def variant_sources(db: Session, exam: str | None, subject: str | None, qtype: str | None, limit: int):
    v = aliased(Question)
    versions = select(func.count()).where(v.variant_of == Question.id).scalar_subquery()
    stmt = (select(Question, Transcript).join(Transcript, Transcript.question_id == Question.id)
            .where(Transcript.agreed.is_(True), Question.origin == "pyq", Question.status == "served"))
    for col, val in ((Question.exam, exam), (Question.subject, subject), (Question.qtype, qtype)):
        if val:
            stmt = stmt.where(col == val)
    return db.execute(stmt.order_by(versions, func.random()).limit(limit)).all()


def grow(db: Session, limit: int, exam: str | None = None, subject: str | None = None, qtype: str | None = None,
         transcribe_first: bool = True) -> dict:
    """Make up to `limit` versions for the given slice, transcribing more source questions when none are left."""
    out: dict[str, int] = {}
    with client() as c:
        try:
            rows = variant_sources(db, exam, subject, qtype, limit)
            if len(rows) < limit and transcribe_first:
                stmt = select(Question).where(Question.origin == "pyq", Question.status == "served",
                                              Question.id.not_in(select(Transcript.question_id)))
                for col, val in ((Question.exam, exam), (Question.subject, subject), (Question.qtype, qtype)):
                    if val:
                        stmt = stmt.where(col == val)
                transcribe_many(db, list(db.scalars(stmt.order_by(func.random()).limit(2 * (limit - len(rows))))), c, out)
                rows = variant_sources(db, exam, subject, qtype, limit)
            jobs = [{"src": src, "exam": src.exam, "subject": src.subject, "qtype": src.qtype, "stem": t.stem,
                     "options": list(t.options)} for src, t in rows]
            for job, v in parallel(draft_variant, jobs, c):
                if isinstance(v, dict):
                    store_variant(db, job["src"], v)
                    v = "kept"
                out[v] = out.get(v, 0) + 1
        except DailyLimit:
            out["stopped: free daily limit"] = 1
    log.info("grow %s/%s/%s: %s", exam, subject, qtype, out)
    return out


_job = threading.Lock()


def top_up_in_background(shortfall: dict[tuple[str, str], int], exam: str) -> bool:
    """Start one background job that fills a student's mock shortfall. False when one is already running or no
    key is configured."""
    if not get_settings().openrouter_api_key or not _job.acquire(blocking=False):
        return False

    def run():
        from app.db import SessionLocal
        try:
            with SessionLocal() as db:
                for (subject, qtype), n in shortfall.items():
                    grow(db, n + 2, exam, subject, qtype)
        except Exception:  # noqa: BLE001 - a background job must never take the site down
            log.exception("background top-up failed")
        finally:
            _job.release()

    threading.Thread(target=run, daemon=True).start()
    return True
