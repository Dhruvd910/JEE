"""Load official previous-year papers into the question bank.

JEE Main (NTA): each shift's question paper PDF stores every question and each option as an image, with the
question ID, type (MCQ / SA = numerical) and option IDs as text. NTA's final answer key maps question ID to the
correct option ID (or the numerical value). We keep the official images as they are, so nothing is retyped.

JEE Advanced (organising IIT): the papers are text PDFs. Each question is cropped from the rendered page between
its "Q.n" marker and the next marker, and the official answers come from the "solutions_final" PDF
("Answer Q5: AC", "Answer Q9: [1.70 to 1.75]"). Section headers give the type and marking scheme.

All files live under settings.live_dir (git-ignored): paper text is copyrighted and must not reach the repo.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pypdf
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Paper, Question

NTA_CDN = "https://cdnbbsr.s3waas.gov.in/s3f8e59f4b2fe7c5705bf878bbd494ccdf/uploads/"
# JEE Main 2026 Session 2 (B.E./B.Tech): shift papers and the final answer key, from jeemain.nta.nic.in.
MAIN_2026_S2 = {
    "2026-04-02-s1": "2026/04/202604092096865379.pdf", "2026-04-02-s2": "2026/04/20260409481957146.pdf",
    "2026-04-04-s1": "2026/04/202604091916616339.pdf", "2026-04-04-s2": "2026/04/20260409432593766.pdf",
    "2026-04-05-s1": "2026/04/20260409828731207.pdf", "2026-04-05-s2": "2026/04/20260409829414602.pdf",
    "2026-04-06-s1": "2026/04/202604092007095665.pdf", "2026-04-06-s2": "2026/04/20260409725707538.pdf",
    "2026-04-08-s2": "2026/04/20260409932754345.pdf",
}
MAIN_2026_S2_KEY = "2026/04/20260420409057044.pdf"
ADV = "https://jeeadv.ac.in/documents/"
SUBJECTS = ("Mathematics", "Physics", "Chemistry")


def _text(pdf: Path) -> str:
    return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(pdf).pages)


def _rel(path: Path) -> str:
    return path.relative_to(get_settings().live_dir).as_posix()


# ---------------------------------------------------------------- JEE Main

def parse_main_key(text: str) -> dict[str, str]:
    """Question ID -> correct option ID, numerical value, or a word such as 'Drop'."""
    return dict(re.findall(r"^(\d{6,})\s+(\S+)\s*$", text, re.M))


def parse_main_paper(text: str) -> list[dict]:
    """Questions in paper order: number, NTA question ID, type, subject, section, option IDs."""
    out, subject, section = [], None, None
    pattern = re.compile(r"(Mathematics|Physics|Chemistry) Section ([AB])|Question Number : (\d+) Question Id : (\d+) "
                         r"Question Type : (\w+)(.*?)(?=Question Number : \d+ Question Id|(?:Mathematics|Physics|Chemistry) Section|\Z)",
                         re.S)
    for m in pattern.finditer(text):
        if m[1]:
            subject, section = m[1], f"Section {m[2]}"
            continue
        opts = re.findall(r"^(\d{6,})\.\s*$", m[6], re.M)
        out.append({"number": int(m[3]), "qid": m[4], "type": m[5], "subject": subject, "section": section,
                    "option_ids": opts})
    return out


def load_main_paper(db: Session, code: str, pdf: Path, key: dict[str, str]) -> Paper:
    s = get_settings()
    reader = pypdf.PdfReader(pdf)
    rows = parse_main_paper("\n".join(p.extract_text() or "" for p in reader.pages))
    images = [im for p in reader.pages for im in p.images]
    need = sum(5 if r["type"] == "MCQ" else 1 for r in rows)
    if need != len(images) or not rows:
        raise ValueError(f"{pdf.name}: {len(rows)} questions need {need} images, found {len(images)}")
    out_dir = s.live_dir / "q" / code
    out_dir.mkdir(parents=True, exist_ok=True)
    d = date.fromisoformat(code[5:15])
    shift = code[-1]
    paper = _replace_paper(db, code, exam="main", year=d.year, duration_min=180,
                           title=f"JEE Main {d.year} · {d.day} {d:%b} · Shift {shift}",
                           source_url=NTA_CDN + MAIN_2026_S2[code[5:]], key_url=NTA_CDN + MAIN_2026_S2_KEY)
    it = iter(images)

    def save(name: str) -> str:
        im = next(it)
        path = out_dir / (name + Path(im.name).suffix.lower())
        path.write_bytes(im.data)
        return _rel(path)

    for r in rows:
        n = r["number"]
        stem_img = save(f"{n}")
        opt_imgs = [save(f"{n}_{i + 1}") for i in range(4)] if r["type"] == "MCQ" else []
        given = key.get(r["qid"])
        status = "served"
        if r["type"] == "MCQ":
            qtype = "single"
            if given in r["option_ids"]:
                answer = {"sets": [[r["option_ids"].index(given)]]}
            else:
                answer, status = {"sets": []}, "dropped"
        else:
            qtype = "num"
            try:
                v = float(given)
                answer = {"lo": v, "hi": v}
            except (TypeError, ValueError):
                answer, status = {"lo": None, "hi": None}, "dropped"
        db.add(Question(paper=paper, exam="main", subject=r["subject"], number=n, section=r["section"], qtype=qtype,
                        images=[stem_img], option_images=opt_imgs, answer=answer, marks_full=4, marks_neg=1,
                        nta_qid=r["qid"], status=status, origin="pyq",
                        source=f"NTA question paper and final answer key, {paper.title}"))
    db.commit()
    return paper


def _replace_paper(db: Session, code: str, **fields) -> Paper:
    """Re-loading a paper replaces it, so the loader can be run again safely."""
    old = db.scalar(select(Paper).where(Paper.code == code))
    if old:
        db.execute(delete(Question).where(Question.paper_id == old.id))
        db.delete(old)
        db.flush()
    paper = Paper(code=code, **fields)
    db.add(paper)
    db.flush()
    return paper


# ---------------------------------------------------------------- JEE Advanced

KEYWORDS = {
    "Mathematics": r"matri|function|integer|real number|probability|ellipse|parabola|tangent|∫|lim|plane|vector|"
                   r"complex|sequence|polynomial|determinant|set of all",
    "Physics": r"velocity|mass|charge|field|lens|wave|current|resistance|particle|gravit|spring|frequency|"
               r"capacit|magnetic|photon|kinetic|momentum|circuit",
    "Chemistry": r"compound|reaction|\bmol\b|acid|\bbond|orbital|isomer|oxid|aqueous|enthalpy|\bsalt|catalys|"
                 r"equilibri|ligand|\bpH\b",
}


def guess_subject(text: str) -> str:
    """Subject of one block of a paper, from its vocabulary (the section instructions are removed first)."""
    text = SECTION_RE.sub(" ", text)
    return max(KEYWORDS, key=lambda k: len(re.findall(KEYWORDS[k], text, re.I)))


SECTION_RE = re.compile(r"SECTION\s+\d.*?(?=Q\.\s*\d)", re.S)
WORDS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8}


def parse_sections(block: str) -> list[dict]:
    """Section headers of one subject, in order: type, how many questions, marks."""
    out = []
    for m in SECTION_RE.finditer(block):
        h = re.sub(r"\s+", " ", m.group(0))
        count = int(re.search(r"contains \w+ \((\d+)\)", h, re.I).group(1))
        if re.search(r"question stems", h, re.I):
            per = re.search(r"There are \w+ \((\d+)\) questions corresponding|contains \w+ \((\d+)\) questions corr", h, re.I)
            count *= int(next(g for g in per.groups() if g)) if per else 2
        if "Matching List" in h:
            qtype = "match"
        elif "ONE OR MORE" in h:
            qtype = "multi"
        elif "NUMERICAL VALUE" in h or "NON-NEGATIVE INTEGER" in h:
            qtype = "num"
        else:
            qtype = "single"
        full = int(re.search(r"Full Marks\s*:\s*\+\s*(\d)", h).group(1))
        neg = re.search(r"Negative Marks\s*:\s*[−–-]\s*(\d)", h)
        out.append({"type": qtype, "count": count, "full": full, "neg": int(neg.group(1)) if neg else 0,
                    "title": h.split("(")[0].strip().title()})
    return out


def split_subjects(text: str) -> list[str]:
    """A paper's text split where question numbering restarts at Q.1 (one block per subject)."""
    starts = [m.start() for m in re.finditer(r"SECTION\s+1\b", text)]
    return [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]


def parse_adv_answer(raw: str) -> dict | None:
    raw = raw.strip()
    m = re.fullmatch(r"\[\s*([-\d.]+)\s*(?:to|TO)?\s*([-\d.]+)?\s*\]", raw)
    if m:
        lo = float(m[1])
        return {"lo": lo, "hi": float(m[2]) if m[2] else lo}
    alts = [a.strip() for a in raw.split(" or ")]  # "A or B", "BD or D": any listed answer gets full marks
    if all(re.fullmatch(r"[ABCD]+", a) for a in alts):
        return {"sets": [sorted("ABCD".index(x) for x in a) for a in alts]}
    return None


def adv_answers(solutions_text: str) -> list[dict[int, str]]:
    return [dict((int(n), a) for n, a in re.findall(r"Answer Q(\d+)\s*:\s*(.+)", b)) for b in split_subjects(solutions_text)]


def crop_adv_questions(pdf: Path, out_dir: Path, scale: float = 2.0) -> list[list[str]]:
    """Image crops for each question, in paper order (subject blocks concatenated). A question that runs onto the
    next page gets one crop per page; a shared "Question Stem" is prepended to each question that uses it."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    marks = []  # (page, top_y_from_top, kind, number)
    heights = []
    for pi in range(len(doc)):
        page = doc[pi]
        w, h = page.get_size()
        heights.append(h)
        tp = page.get_textpage()
        # pdfium's own search gives character indexes that match its boxes (Python string offsets do not, because
        # of maths characters outside the BMP).
        for needle, kind in (("Q.", "q"), ("SECTION", "section"), ("Question Stem", "stem")):
            found = tp.search(needle, match_case=True)
            while (hit := found.get_next()) is not None:
                after = tp.get_text_range(hit[0], len(needle) + 4)
                num = re.match(r"Q\.\s*(\d+)", after)
                box = tp.get_charbox(hit[0])
                if kind == "q" and (not num or box[0] > 100):  # only markers at the left margin start a question
                    continue
                marks.append((pi, h - box[3], kind, int(num.group(1)) if num else 0))
    marks.sort(key=lambda x: (x[0], x[1]))
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered: dict[int, object] = {}

    def crop(pi: int, y0: float, y1: float, name: str) -> str:
        if pi not in rendered:
            rendered[pi] = doc[pi].render(scale=scale).to_pil()
        img = rendered[pi]
        box = (0, max(0, int((y0 - 6) * scale)), img.width, min(img.height, int((y1 - 4) * scale)))
        path = out_dir / f"{name}.png"
        img.crop(box).save(path, optimize=True)
        return _rel(path)

    head, foot = 50, 30  # page header ("JEE (Advanced) 2026 Paper 1 · 3/10") and footer, in points
    questions, stem_crops, idx = [], [], 0
    for i, (pi, y, kind, num) in enumerate(marks):
        nxt = marks[i + 1] if i + 1 < len(marks) else None
        if kind == "section":
            stem_crops = []
            continue
        parts, p, top = [], pi, y
        while True:
            if nxt and nxt[0] == p:
                parts.append((p, top, nxt[1]))
                break
            parts.append((p, top, heights[p] - foot))
            p += 1
            if p >= len(heights) or (nxt and nxt[0] < p):
                break
            top = head
        parts = [x for x in parts if x[2] - x[1] > 45]  # skip slivers when a question ends right at a page break
        if kind == "stem":
            stem_crops = [crop(pp, a, b, f"stem{idx}_{j}") for j, (pp, a, b) in enumerate(parts)]
            continue
        idx += 1
        questions.append(stem_crops + [crop(pp, a, b, f"{idx}_{j}") for j, (pp, a, b) in enumerate(parts)])
    return questions


def load_adv_paper(db: Session, year: int, paper_no: int, paper_pdf: Path, solutions_pdf: Path) -> Paper:
    s = get_settings()
    code = f"adv-{year}-p{paper_no}"
    blocks = split_subjects(_text(paper_pdf))
    answers = adv_answers(_text(solutions_pdf))
    if len(blocks) != 3 or len(answers) != 3:
        raise ValueError(f"{paper_pdf.name}: expected 3 subjects, found {len(blocks)} papers / {len(answers)} keys")
    crops = crop_adv_questions(paper_pdf, s.live_dir / "q" / code)
    paper = _replace_paper(db, code, exam="advanced", year=year, duration_min=180,
                           title=f"JEE Advanced {year} · Paper {paper_no}",
                           source_url=ADV + f"p{paper_no}_english.pdf", key_url=ADV + f"p{paper_no}_solutions_final.pdf")
    k = 0
    for block, key in zip(blocks, answers):
        subject = guess_subject(block)
        n = 0
        for sec in parse_sections(block):
            for _ in range(sec["count"]):
                n += 1
                raw = key.get(n, "")
                answer = parse_adv_answer(raw)
                qtype = sec["type"]
                if answer and (qtype == "num") != ("lo" in answer):
                    answer = None  # answer does not fit the section's format: leave the question out, never guess
                elif answer and qtype in ("single", "match") and any(len(x) != 1 for x in answer["sets"]):
                    answer = None
                db.add(Question(paper=paper, exam="advanced", subject=subject, number=k + 1, section=sec["title"],
                                qtype=qtype, images=crops[k] if k < len(crops) else [], answer=answer or {},
                                marks_full=sec["full"], marks_neg=sec["neg"], status="served" if answer else "dropped",
                                origin="pyq", source=f"{paper.title}, official paper and final answers (Q.{n}: {raw})"))
                k += 1
    if k != len(crops):
        raise ValueError(f"{paper_pdf.name}: {k} questions in the section headers but {len(crops)} cropped")
    db.commit()
    return paper


def load_all(db: Session) -> dict:
    """Load every official paper found under live/raw that is not loaded yet. Loaded papers are left alone:
    replacing one would delete students' answers to its questions."""
    raw = get_settings().live_dir / "raw"
    have = set(db.scalars(select(Paper.code)))
    done = {}
    key_pdf = raw / "main" / "key-s2.pdf"
    if key_pdf.exists():
        key = parse_main_key(_text(key_pdf))
        for code in MAIN_2026_S2:
            pdf = raw / "main" / f"{code}.pdf"
            if pdf.exists() and "main-" + code not in have:
                p = load_main_paper(db, "main-" + code, pdf, key)
                done[p.code] = len(p.questions)
    for n in (1, 2):
        paper, sol = raw / "adv" / f"2026_p{n}_english.pdf", raw / "adv" / f"2026_p{n}_solutions_final.pdf"
        if paper.exists() and sol.exists() and f"adv-2026-p{n}" not in have:
            p = load_adv_paper(db, 2026, n, paper, sol)
            done[p.code] = len(p.questions)
    return done
