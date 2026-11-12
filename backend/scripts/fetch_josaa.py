"""Download JoSAA opening/closing ranks (final round, all institutes) from the official archive form.

Usage: python -m scripts.fetch_josaa 2025 2024 2023
Writes live/raw/josaa/<year>.html (the raw official table) and is parsed by app.services.ingestion.
"""
import html
import re
import sys
from pathlib import Path

import httpx

ARCHIVE = "https://josaa.admissions.nic.in/applicant/seatmatrix/openingclosingrankarchieve.aspx"
CURRENT = "https://josaa.admissions.nic.in/Applicant/SeatAllotmentResult/currentorcr.aspx"
OUT = Path(__file__).resolve().parent.parent / "live" / "raw" / "josaa"
P = "ctl00$ContentPlaceHolder1$"


def hidden(page: str) -> dict:
    return {m[0]: html.unescape(m[1]) for m in re.findall(r'name="(__[A-Z]+)"[^>]*value="([^"]*)"', page)}


def options(page: str, name: str) -> list[str]:
    m = re.search(r'<select[^>]*name="%s".*?</select>' % re.escape(P + name), page, re.S)
    return [v for v in re.findall(r'<option[^>]*value="([^"]*)"', m[0]) if v not in ("0", "")] if m else []


def fetch(year: int | None) -> str:
    url = ARCHIVE if year else CURRENT
    with httpx.Client(timeout=120, follow_redirects=True) as c:
        page = c.get(url).text
        form = {}
        fields = (["ddlYear"] if year else []) + ["ddlroundno", "ddlInstype", "ddlInstitute", "ddlBranch", "ddlSeatType"]
        for f in fields:
            opts = options(page, f)
            if not opts:
                raise RuntimeError(f"no options for {f}")
            pick = str(year) if f == "ddlYear" else (max(opts, key=int) if f == "ddlroundno" else
                                                       ("ALL" if "ALL" in opts else opts[0]))
            form[P + f] = pick
            page = c.post(url, data={**hidden(page), **form, "__EVENTTARGET": P + f, "__EVENTARGUMENT": ""}).text
        page = c.post(url, data={**hidden(page), **form, P + "btnSubmit": "Submit"}).text
        print(year or "current", {k[len(P):]: v for k, v in form.items()})
        return page


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for arg in sys.argv[1:] or ["current"]:
        y = None if arg == "current" else int(arg)
        page = fetch(y)
        (OUT / f"{arg}.html").write_text(page, encoding="utf-8")
        print(arg, page.count("<tr"), "rows")
