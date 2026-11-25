"""Host JEE Workbench from this computer, the same way NEET Compass is hosted.

    python serve_public.py        # or double-click start_site.cmd, which restarts it if it ever stops

- The site runs on 127.0.0.1 with its own live database (backend/live/jee.db) and secrets (backend/.env.live, created
  on first start with a random session secret). Both are git-ignored.
- A Cloudflare quick tunnel gives it a public HTTPS address. That address changes on every start, so the fixed link
  https://dhruvd910.github.io/JEE/ forwards to whatever the current one is: this script writes it to current.json on
  the repo's gh-pages branch (checked out in backend/live/pages) and pushes it.
- If the site, the tunnel or the public address stops working, the script exits so start_site.cmd starts everything
  again with a fresh tunnel (and the fixed link is updated again).
- Once a day it adds checked mock questions with free models (python -m app.maintenance fill-mocks), within the
  BACKGROUND_DAILY_CALLS budget so students' assistant questions keep enough of the shared free quota.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE = HERE / "live"
SECRETS = HERE / ".env.live"
PORT = int(os.environ.get("LIVE_PORT", "8790"))
PAGES = LIVE / "pages"  # git worktree of the gh-pages branch: the fixed link
FIXED_LINK = "https://dhruvd910.github.io/JEE/"
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def live_secrets() -> dict[str, str]:
    if not SECRETS.exists():
        SECRETS.write_text(
            "# Secrets for JEE Workbench hosted from this computer (serve_public.py). Never commit or share this file.\n"
            f"JWT_SECRET={secrets.token_urlsafe(48)}\n", encoding="utf-8")
        print(f"Created {SECRETS.name} (session secret).")
    pairs = (line.split("=", 1) for line in SECRETS.read_text(encoding="utf-8").splitlines()
             if "=" in line and not line.startswith("#"))
    return {k.strip(): v.strip() for k, v in pairs}


def find_cloudflared() -> str:
    exe = shutil.which("cloudflared") or r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
    if not Path(exe).exists():
        sys.exit("cloudflared is not installed. Run: winget install Cloudflare.cloudflared")
    return exe


def start_tunnel() -> tuple[subprocess.Popen, str]:
    tunnel = subprocess.Popen([find_cloudflared(), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                              errors="replace")
    deadline = time.time() + 60
    for line in tunnel.stdout:
        if m := TUNNEL_URL.search(line):
            threading.Thread(target=lambda: [None for _ in tunnel.stdout], daemon=True).start()  # keep the pipe drained
            return tunnel, m.group(0)
        if time.time() > deadline:
            break
    tunnel.kill()
    sys.exit("The Cloudflare tunnel did not start. Check your internet connection and try again.")


def publish_link(url: str) -> None:
    """Point the fixed GitHub Pages link at the current address. Failures are reported, never fatal."""
    target = PAGES / "current.json"
    if not (PAGES / ".git").exists():
        print("The fixed link is not set up (backend/live/pages is missing); share the address above instead.")
        return
    try:
        if json.loads(target.read_text(encoding="utf-8")).get("url") == url:
            return
    except (OSError, ValueError):
        pass
    target.write_text(json.dumps({"url": url, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n",
                      encoding="utf-8")
    # Google sends people back to a fixed address; this page forwards them to the site's current one.
    callback = PAGES / "google-callback.html"
    if not callback.exists():
        shutil.copy(HERE / "app" / "web" / "pages_google_callback.html", callback)
    git = ["git", "-C", str(PAGES), "-c", "user.name=Dhruvd910", "-c", "user.email=dhruvd910@gmail.com"]
    try:
        subprocess.run(git + ["add", "current.json", "google-callback.html"], check=True, capture_output=True, timeout=30)
        subprocess.run(git + ["commit", "-m", "JEE: point the fixed link at the current address"], check=True,
                       capture_output=True, timeout=30)
        subprocess.run(git + ["push", "origin", "HEAD"], check=True, capture_output=True, timeout=120)
        print(f"Fixed link updated: {FIXED_LINK} (GitHub takes about a minute to publish it).")
    except (subprocess.SubprocessError, OSError) as e:
        print(f"Could not update the fixed link ({e}); share the address above instead.")


def prepare_database(env: dict) -> None:
    """First start: the live database begins as a copy of the development one (papers, JoSAA, NIRF, syllabus, mock
    bank), without the development test accounts. Then migrations bring it up to date."""
    db = LIVE / "jee.db"
    if not db.exists():
        import sqlite3
        src, dst = sqlite3.connect(HERE / "jee_dev.db"), sqlite3.connect(db)
        with dst:
            src.backup(dst)
        dst.execute("DELETE FROM users WHERE email LIKE '%@jee.test' OR email LIKE '%@demo.test'")
        dst.commit()
        src.close()
        dst.close()
        print("Created live/jee.db from the development database (test accounts removed).")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=HERE, env=env, check=True)


def site_ok(url: str) -> bool:
    try:
        urllib.request.urlopen(f"{url}/login", timeout=20)
        return True
    except OSError:
        return False


def daily_jobs(env: dict) -> None:
    """Once a day: a backup copy of the live database (kept 14 days) and new checked mock questions."""
    import sqlite3
    backups = LIVE / "backups"
    backups.mkdir(exist_ok=True)
    target = backups / f"jee-{time.strftime('%Y-%m-%d')}.db"
    if target.exists():
        return
    try:
        src, dst = sqlite3.connect(LIVE / "jee.db"), sqlite3.connect(target)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        for old in backups.glob("jee-*.db"):
            if old.stat().st_mtime < time.time() - 14 * 86400:
                old.unlink()
        # Under-18 accounts whose parent declined or never answered are deleted (privacy notice, guardian consent).
        subprocess.run([sys.executable, "-m", "app.maintenance", "purge-minors"], cwd=HERE, env=env, timeout=300)
        with open(LIVE / "fill-mocks.log", "a") as log:
            subprocess.Popen([sys.executable, "-m", "app.maintenance", "fill-mocks", "--exam", "main"], cwd=HERE,
                             env=env, stdout=log, stderr=subprocess.STDOUT)
    except (sqlite3.Error, OSError, subprocess.SubprocessError) as e:
        print(f"Daily backup or mock top-up failed ({e}); will try again tomorrow.")


def main() -> None:
    LIVE.mkdir(exist_ok=True)
    s = live_secrets()
    env = {
        **os.environ,
        **{k: v for k, v in s.items() if v},
        "ENV": "staging",
        "DATABASE_URL": f"sqlite:///{(LIVE / 'jee.db').as_posix()}",
        "JWT_SECRET": s["JWT_SECRET"],
        "COOKIE_SECURE": "true",          # visitors always arrive over HTTPS through the tunnel
        "TRUST_PROXY_HEADERS": "true",    # safe: only the tunnel can reach 127.0.0.1
        # The NEET | JEE admin switch goes through NEET's fixed link, which forwards the part after '#'.
        "NEET_URL": s.get("NEET_URL") or "https://dhruvd910.github.io/NEET/#",
        # Emailed links (guardian consent) and Google's return address go through the fixed link, which survives restarts.
        "LINK_BASE": s.get("LINK_BASE") or FIXED_LINK + "#",
        "GOOGLE_REDIRECT_URI": s.get("GOOGLE_REDIRECT_URI") or FIXED_LINK + "google-callback.html",
    }
    prepare_database(env)
    tunnel, url = start_tunnel()
    site = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                             "--port", str(PORT), "--proxy-headers"], cwd=HERE, env=env)
    try:
        for _ in range(120):
            if site.poll() is not None:
                sys.exit("The site stopped while starting; see the messages above.")
            if site_ok(f"http://127.0.0.1:{PORT}"):
                break
            time.sleep(1)
        (LIVE / "public_url.txt").write_text(url + "\n", encoding="utf-8")
        print("\n" + "=" * 64 + f"\n  JEE Workbench is live at:  {url}\n  Fixed link:               {FIXED_LINK}\n"
              "  Keep this window open; Ctrl+C stops it.\n" + "=" * 64 + "\n")
        publish_link(url)
        misses = 0
        while site.poll() is None:
            daily_jobs(env)
            time.sleep(60)
            if tunnel.poll() is not None:
                print("The Cloudflare tunnel stopped; restarting.")
                break
            misses = 0 if site_ok(url) else misses + 1
            if misses >= 3:
                print("The public address stopped answering; restarting with a new tunnel.")
                break
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        for p in (site, tunnel):
            if p is not None and p.poll() is None:
                p.terminate()


if __name__ == "__main__":
    main()
