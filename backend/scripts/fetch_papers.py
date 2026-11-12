"""Download the official papers and answer keys into live/raw (git-ignored).

Usage: python -m scripts.fetch_papers
Then: python -m app.maintenance load-data
"""
from pathlib import Path

import httpx

from app.services.papers import ADV, MAIN_2026_S2, MAIN_2026_S2_KEY, NTA_CDN
from app.services.study import SYLLABUS_URL

RAW = Path(__file__).resolve().parent.parent / "live" / "raw"
FILES = {f"main/{code}.pdf": NTA_CDN + path for code, path in MAIN_2026_S2.items()}
FILES["main/key-s2.pdf"] = NTA_CDN + MAIN_2026_S2_KEY
FILES["syllabus/main.pdf"] = SYLLABUS_URL  # JEE Main syllabus published by NTA in October 2026
for n in (1, 2):
    FILES[f"adv/2026_p{n}_english.pdf"] = ADV + f"p{n}_english.pdf"
    FILES[f"adv/2026_p{n}_solutions_final.pdf"] = ADV + f"p{n}_solutions_final.pdf"

if __name__ == "__main__":
    with httpx.Client(timeout=120, follow_redirects=True) as c:
        for rel, url in FILES.items():
            dest = RAW / rel
            if dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            r = c.get(url)
            r.raise_for_status()
            if not r.content.startswith(b"%PDF"):
                raise SystemExit(f"{url} did not return a PDF")
            dest.write_bytes(r.content)
            print("saved", rel, len(r.content))
