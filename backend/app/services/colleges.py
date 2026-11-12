"""College catalogue from official tables: JoSAA opening/closing ranks (final round) and NIRF 2025 Engineering.

The raw JoSAA pages are fetched by scripts/fetch_josaa.py into live/raw/josaa/<year>.html.
"""
from __future__ import annotations

import csv
import html
import re
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import BASE_DIR, get_settings
from app.models import Cutoff, Institute

JOSAA_URL = "https://josaa.admissions.nic.in/applicant/seatmatrix/openingclosingrankarchieve.aspx"
NIRF_URL = "https://www.nirfindia.org/Rankings/2025/EngineeringRanking.html"
NIRF_CSV = BASE_DIR / "data" / "nirf_2025_engineering.csv"
# Home-state quota needs the institute's state. Where NIRF does not list an institute, the state is read from the
# place named in its JoSAA name (shortcut: a fixed table of places; add one when a new institute appears).
PLACES = {
    "Diu": "Dadra and Nagar Haveli and Daman and Diu", "Odisha Campus": "Odisha", "Deoghar": "Jharkhand",
    "Kota": "Rajasthan", "Guwahati": "Assam", "Kalyani": "West Bengal", "Sonepat": "Haryana",
    "Una": "Himachal Pradesh", "Sri City": "Andhra Pradesh", "Vadodara": "Gujarat", "Lucknow": "Uttar Pradesh",
    "Dharwad": "Karnataka", "Kottayam": "Kerala", "Pune": "Maharashtra", "Bhagalpur": "Bihar",
    "Raichur": "Karnataka", "Haridwar": "Uttarakhand", "Bhadohi": "Uttar Pradesh", "Ahmedabad": "Gujarat",
    "Bilaspur": "Chhattisgarh", "Aurangabad": "Maharashtra", "Vijayawada": "Andhra Pradesh",
    "Bhubaneswar": "Odisha", "Malda": "West Bengal", "Rajasthan": "Rajasthan", "Kundli": "Haryana",
    "Thanjavur": "Tamil Nadu", "Varanasi": "Uttar Pradesh", "Bhilai": "Chhattisgarh", "Shillong": "Meghalaya",
    "Sagar": "Madhya Pradesh", "Haryana": "Haryana", "Salem": "Tamil Nadu", "Jharkhand": "Jharkhand",
    "Ajmer": "Rajasthan", "Gorakhpur": "Uttar Pradesh", "Amethi": "Uttar Pradesh", "Indore": "Madhya Pradesh",
    "Agartala": "Tripura", "Allahabad": "Uttar Pradesh", "Arunachal Pradesh": "Arunachal Pradesh",
    "Bhopal": "Madhya Pradesh", "Calicut": "Kerala", "Delhi": "Delhi", "Durgapur": "West Bengal",
    "Goa": "Goa", "Hamirpur": "Himachal Pradesh", "Jaipur": "Rajasthan", "Jalandhar": "Punjab",
    "Jamshedpur": "Jharkhand", "Kurukshetra": "Haryana", "Manipur": "Manipur", "Meghalaya": "Meghalaya",
    "Mizoram": "Mizoram", "Nagaland": "Nagaland", "Nagpur": "Maharashtra", "Patna": "Bihar",
    "Puducherry": "Puducherry", "Raipur": "Chhattisgarh", "Rourkela": "Odisha", "Sikkim": "Sikkim",
    "Silchar": "Assam", "Srinagar": "Jammu and Kashmir", "Surat": "Gujarat", "Surathkal": "Karnataka",
    "Tiruchirappalli": "Tamil Nadu", "Uttarakhand": "Uttarakhand", "Warangal": "Telangana",
    "Andhra Pradesh": "Andhra Pradesh", "Shibpur": "West Bengal", "Howrah": "West Bengal",
    "Mesra": "Jharkhand", "Ranchi": "Jharkhand", "Tezpur": "Assam", "Assam": "Assam", "Punjab": "Punjab",
    "Jammu": "Jammu and Kashmir", "Kashmir": "Jammu and Kashmir", "Gwalior": "Madhya Pradesh",
    "Jabalpur": "Madhya Pradesh", "Kancheepuram": "Tamil Nadu", "Kurnool": "Andhra Pradesh",
}


def kind_of(name: str) -> str:
    n = name.lower()
    if "indian institute of technology" in n and "information" not in n:
        return "IIT"
    if "national institute of technology" in n or "indian institute of engineering science" in n:
        return "NIT"
    if "information technology" in n:
        return "IIIT"
    return "GFTI"


def _norm(name: str) -> set[str]:
    stop = {"of", "and", "the", "institute", "technology", "indian", "national", "information", "engineering"}
    n = name.lower().replace("&", " and ").replace("banaras hindu university", "bhu").replace("indian school of mines", "ism")
    words = re.findall(r"[a-z]+", n)
    return {w for w in words if w not in stop} or set(words)


def parse_josaa(page: str) -> list[list[str]]:
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) == 7 and cells[5] and cells[6]:
            rows.append(cells)
    return rows


def _rank(v: str) -> tuple[int, bool]:
    v = v.strip()
    return int(float(v.rstrip("P"))), v.endswith("P")


def load_cutoffs(db: Session, year: int, page: str, round_no: int) -> int:
    institutes = {i.name: i for i in db.scalars(select(Institute))}
    rows = parse_josaa(page)
    if not rows:
        raise ValueError(f"JoSAA {year}: no rank rows found")
    db.execute(delete(Cutoff).where(Cutoff.year == year))
    for name, program, quota, seat, gender, opening, closing in rows:
        inst = institutes.get(name)
        if inst is None:
            inst = institutes[name] = Institute(name=name, kind=kind_of(name))
            db.add(inst)
            db.flush()
        o, _ = _rank(opening)
        c, prep = _rank(closing)
        db.add(Cutoff(institute_id=inst.id, program=program, year=year, round=round_no, quota=quota, seat_type=seat,
                      gender=gender, opening=o, closing=c, preparatory=prep, source_url=JOSAA_URL))
    db.commit()
    return len(rows)


def attach_nirf(db: Session) -> int:
    """NIRF rank, score and location for each catalogue institute that NIRF 2025 Engineering lists."""
    with open(NIRF_CSV, encoding="utf-8") as f:
        nirf = list(csv.DictReader(f))
    matched = 0
    for inst in db.scalars(select(Institute)):
        mine = _norm(inst.name)
        best, score = None, 0.0
        for r in nirf:
            if kind_of(r["name"]) != inst.kind:  # never match an IIT to the NIT in the same city
                continue
            theirs = _norm(r["name"] + " " + r["city"])
            s = len(mine & theirs) / len(mine | theirs)
            if s > score:
                best, score = r, s
        # Accept when every distinctive word of our name is in NIRF's, or the match is close and NIRF's city or
        # state is named too (keeps "Jawaharlal Nehru University, Delhi" off JNTU Hyderabad's row).
        ok = best and (mine <= _norm(best["name"] + " " + best["city"]) or (
            score >= 0.5 and (best["city"].lower() in inst.name.lower() or best["state"].lower() in inst.name.lower())))
        if ok:
            inst.nirf_rank, inst.nirf_score, inst.nirf_id = int(best["rank"]), float(best["score"]), best["nirf_id"]
            inst.nirf_parts = {k.upper(): float(best[k]) for k in ("tlr", "rpc", "go", "oi", "pr")}
            inst.state, inst.city = best["state"].replace("Pondicherry", "Puducherry"), best["city"]
            matched += 1
        else:
            inst.nirf_rank = inst.nirf_score = inst.nirf_id = inst.nirf_parts = None
        if not inst.state:
            inst.state = next((st for place, st in PLACES.items() if re.search(rf"\b{place}\b", inst.name, re.I)), None)
    db.commit()
    return matched


def load_all(db: Session) -> dict:
    raw: Path = get_settings().live_dir / "raw" / "josaa"
    out = {}
    for f in sorted(raw.glob("20*.html")):
        page = f.read_text(encoding="utf-8")
        m = re.search(r'ddlroundno".*?selected="selected" value="(\d+)"', page, re.S)
        out[int(f.stem)] = load_cutoffs(db, int(f.stem), page, int(m.group(1)) if m else 0)
    out["nirf_matched"] = attach_nirf(db)
    return out
