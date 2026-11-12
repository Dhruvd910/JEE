# JEE Workbench: practice and counselling for JEE Main and JEE Advanced

A guidance and practice platform for engineering aspirants, built on the same stack and patterns as NEET Compass
(FastAPI + Jinja2 + HTMX + SQLAlchemy 2 + Alembic + pytest, Tailwind built with `pytailwindcss`, no Node).

Everything shown to students comes from official sources. There is no demo data.

| Data | Source | What is loaded |
|---|---|---|
| JEE Main papers | NTA, jeemain.nta.nic.in (question papers + final answer key) | 2026 Session 2, B.E./B.Tech: all 9 shifts, 675 questions; 670 with an official key (5 had no usable key and are left out) |
| JEE Advanced papers | jeeadv.ac.in (paper + `solutions_final` answers) | 2026 Paper 1 (48) and Paper 2 (54) |
| Opening/closing ranks | JoSAA archive (josaa.admissions.nic.in) | 2023, 2024, 2025, final round: 33,570 rows, 130 institutes |
| Rankings | NIRF 2025 Engineering (nirfindia.org) | Top 100, matched to 48 JoSAA institutes |
| Syllabus | NTA JEE Main syllabus PDF (October 2026) | 54 units: Mathematics 14, Physics 20, Chemistry 20 |

## Features

**Study**
- **Dashboard:** exam countdown (the student's own exam date), daily-quiz streak, score-trend chart, and per-subject
  rings for Physics, Chemistry and Mathematics (accuracy, previous-year coverage, syllabus progress, mistakes to fix).
- **Previous-year papers** (kept separate from mocks): the official papers shown as the official images, timed,
  marked with the official key.
- **Mocks that never repeat:** a JEE Main-format mock (20 MCQ + 5 numerical per subject) and a JEE Advanced-format
  mock (2026 Paper 1 sections). They are built only from AI-checked new versions of past questions the student has
  never been given, one version per original question.
- **Custom drills:** 3–30 official questions filtered by subject, exam (Main/Advanced) and type (single, numerical,
  multiple-correct, matching). The source can be unseen questions, the student's mistakes, or bookmarks.
- **Mistake notebook and bookmarks:** collects every wrong or partly-right answer (the latest attempt), lets the
  student bookmark any question, and retests either set in one click.
- **Syllabus tracker:** all 54 units of NTA's official JEE Main syllabus (October 2026 PDF). Each unit is marked
  studied, revised or confident, with completion per subject.
- **Analytics:** score % for every test, attempt rate, marks lost to negative marking, and accuracy by subject ×
  question type.
- **Daily quiz:** 9 previous-year questions (3 per subject), 20 minutes, the same set for everyone each day.

**Counselling**
- **College catalogue:** every JoSAA institute with NIRF rank and parameter bars, plus opening/closing ranks by
  program, quota, seat type and gender pool, with a three-year trend sparkline.
- **Compare:** up to three institutes side by side (NIRF parameters, latest closing rank per branch).
- **Rank → college predictor:** compares ranks with JoSAA final-round closing ranks of the last three years
  (no probabilities). It follows JoSAA's rules: IITs use the Advanced rank, the others use the Main rank;
  category seats use category ranks; Home State / Other State quotas; Female-only seats for female candidates.
- **Choice-list builder:** add programs from the predictor or a college page, reorder them, see each one's latest
  closing rank for the student's own pool, and download the list as CSV for JoSAA choice filling.

**Admin console (shared with NEET Compass):** both sites use the same console: same layout, components and
sign-in page, with a **NEET | JEE switcher** in the header. Each console shows only its own site's data:
- JEE: overview (live numbers, 14-day sparklines, activity feed, health checklist), student register and student
  files, question bank (mock-bank fill per slot, paper browser to check and hide questions), and data sources.
- NEET: its existing tabs (overview, students, imports, sources, eligibility rules, info pages, colleges, issues).

Admins sign in at `/admin/login`. Set `NEET_URL` here and `JEE_URL` on NEET to point the switcher at the other
site. With the same `ADMIN_BRIDGE_SECRET` on both sites, switching signs the admin in on the other site through a
signed, 60-second, single-use link (only for an account with the same email that is already an admin there).
NEET's `app/web/styles/admin.css` is a copy of this app's `styles/input.css`; edit the design here and copy it over.

**Design:** a dark "lab at night" theme built with Tailwind: circuit-grid background, glass panels, cyan → violet
accents, one colour per subject (Physics sky, Chemistry emerald, Mathematics fuchsia), monospace numbers. Official
paper images sit on white sheets so they stay readable.

## Marking

| Exam | Rule | Source |
|---|---|---|
| JEE Main | MCQ +4 / −1 / 0; numerical value **+4 / −1** / 0 | NTA Information Bulletin 2025 (NTA added negative marking for numericals in 2025, when Section B became compulsory) |
| JEE Advanced | Each section's own scheme as printed in the paper: single correct +3/−1, multiple correct +4 with partial +3/+2/+1 and a negative, numerical +4 or +2 with no negative, matching +4/−1 | The paper's section headers, stored on every question |

Where the official key accepts more than one answer ("A or B", "BD or D"), any listed answer gets full marks.

## How questions get into the bank

1. **JEE Main:** each NTA paper PDF stores each question and each option as an image, with the question ID and
   option IDs as text. The loader keeps the official images and maps NTA's final key (question ID → correct
   option ID) onto them. No text is retyped, so nothing can be misread.
2. **JEE Advanced:** each question is cropped from the rendered page between its `Q.n` marker and the next one. The
   type and marks come from the section headers, and the official answers come from `solutions_final`.
3. **Transcription (free models):** for mocks, two free NVIDIA Nemotron vision models read each question image
   into text. The reading is kept only if both agree and neither says a figure is needed. Gemma 4 stands in when a
   reader is busy.
4. **Mock versions (free models):** Nemotron 3 Ultra writes a new version with new numbers or wording and solves
   it. A different model solves it without the key, and the version is kept only if both answers match. Versions are
   labelled "AI-checked, not expert-reviewed".

Paid generation is not used. Free models allow about 20 calls a minute and 1000 a day, so the bank grows
gradually: `fill-mocks` stops at the daily limit and can be run again the next day. A mock that can't be built yet
starts a background top-up and says so.

## Run it locally

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env                  # add OPENROUTER_API_KEY for transcription/mock versions
python -m scripts.fetch_papers        # official PDFs -> live/raw (git-ignored)
python -m scripts.fetch_josaa 2025 2024 2023
alembic upgrade head
python -m app.maintenance load-data   # papers, JoSAA, NIRF, syllabus; papers already loaded are left alone
uvicorn app.main:app --port 8770      # http://localhost:8770
```

- **Admin:** register on the site, then run `python -m app.maintenance make-admin you@example.com`.
- **Mock bank:** `python -m app.maintenance fill-mocks --exam main` (or `--exam advanced`). Progress is shown
  on `/admin`.
- **Stylesheet:** after changing templates or `app/web/styles/input.css`, run `python -m app.web.build_css`.
- **Tests:** `cd backend && python -m pytest -q`. The checks against the real PDFs are skipped when `live/raw` is
  missing.

## Copyright and privacy

- **Copyrighted papers stay out of the repo.** Official PDFs, question crops and transcripts live in
  `backend/live/`, which is git-ignored. Question images are served only to signed-in students, from `/img/...`.
- **Factual data is committed.** The NIRF table (`backend/data/nirf_2025_engineering.csv`) is in the repo; JoSAA
  tables are downloaded by the script.
- **Personal data is minimal.** Students give a name, email and password. Category, gender and home state are
  optional and used only by the predictor.

## Layout

```
backend/
  app/
    models.py               # users, papers, questions, transcripts, attempts, institutes, cutoffs
    services/papers.py      # official PDF loaders (Main images + key, Advanced crops + answers)
    services/exams.py       # marking, papers, mocks (no repeats), daily quiz, progress
    services/ai.py          # free-model transcription and mock versions
    services/colleges.py    # JoSAA + NIRF loaders
    services/study.py       # syllabus, mistakes/bookmarks, drills, analytics, choice list, compare
    services/predictor.py   # closing-rank matcher
    web/                    # routes.py, core.py, templates/, styles/input.css -> static/app.css
    maintenance.py          # CLI jobs
  scripts/fetch_papers.py, scripts/fetch_josaa.py
  data/nirf_2025_engineering.csv
  tests/
```
