"""Command-line jobs.

    python -m app.maintenance load-data              # official papers + JoSAA + NIRF + syllabus from live/raw (re-runnable)
    python -m app.maintenance transcribe --limit 50  # free vision models read paper images (two must agree)
    python -m app.maintenance grow --limit 30 [--exam main] [--subject Physics] [--qtype single]
    python -m app.maintenance fill-mocks --exam main [--mocks 2]   # enough checked versions for N mocks
    python -m app.maintenance create-admin you@example.com   # admin with a password (typed, never shown)
    python -m app.maintenance make-admin you@example.com
    python -m app.maintenance stats
"""
import argparse
import json

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Role, User


def create_admin(db, email: str) -> str:
    """An admin account with a password, or a new password for an existing account (made admin). Same as NEET's."""
    import getpass

    from app.security import hash_password
    email = email.strip().lower()
    password = getpass.getpass("Password (min. 12 characters): ")
    if len(password) < 12 or password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords must match and be at least 12 characters.")
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        db.add(User(email=email, name=email.split("@")[0], password_hash=hash_password(password), role=Role.admin))
    else:
        user.password_hash, user.role = hash_password(password), Role.admin
    db.commit()
    return f"{email} is now an admin."


def fill_mocks(db, exam: str, mocks: int) -> None:
    """Grow each (subject, type) slot of the mock format until it holds enough checked versions; passes repeat
    while they make progress (free models disagree or are busy often)."""
    from sqlalchemy import func

    from app.models import Question
    from app.services import ai
    from app.services.exams import MOCKS, SUBJECTS
    for _ in range(6):
        progress = False
        for subject in SUBJECTS:
            for qtype, count, _, _ in MOCKS[exam]["sections"]:
                have = db.scalar(select(func.count()).where(Question.origin == "variant", Question.exam == exam,
                                                            Question.subject == subject, Question.qtype == qtype,
                                                            Question.status == "served"))
                need = count * mocks - have
                if need > 0:
                    out = ai.grow(db, need, exam, subject, qtype)
                    print(subject, qtype, f"had {have}", out, flush=True)
                    if "stopped: free daily limit" in out:
                        return
                    progress |= out.get("kept", 0) > 0
        if not progress:
            return


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m app.maintenance")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("load-data")
    t = sub.add_parser("transcribe")
    t.add_argument("--limit", type=int, default=50)
    g = sub.add_parser("grow")
    g.add_argument("--limit", type=int, default=30)
    g.add_argument("--exam", choices=["main", "advanced"])
    g.add_argument("--subject", choices=["Physics", "Chemistry", "Mathematics"])
    g.add_argument("--qtype", choices=["single", "multi", "num", "match"])
    f = sub.add_parser("fill-mocks")
    f.add_argument("--exam", choices=["main", "advanced"], default="main")
    f.add_argument("--mocks", type=int, default=1)
    c = sub.add_parser("create-admin")
    c.add_argument("email")
    a = sub.add_parser("make-admin")
    a.add_argument("email")
    sub.add_parser("stats")
    args = p.parse_args(argv)
    with SessionLocal() as db:
        if args.cmd == "load-data":
            from app.services import colleges, papers
            print("papers:", papers.load_all(db))
            print("colleges:", colleges.load_all(db))
            from app.services import study
            print("syllabus units:", study.load_syllabus(db))
        elif args.cmd == "transcribe":
            from app.services import ai
            print(ai.transcribe_batch(db, args.limit))
        elif args.cmd == "grow":
            from app.services import ai
            print(ai.grow(db, args.limit, args.exam, args.subject, args.qtype))
        elif args.cmd == "fill-mocks":
            fill_mocks(db, args.exam, args.mocks)
        elif args.cmd == "create-admin":
            print(create_admin(db, args.email))
        elif args.cmd == "make-admin":
            user = db.scalar(select(User).where(User.email == args.email.lower()))
            if not user:
                raise SystemExit("No account with that email; register on the site first.")
            user.role = Role.admin
            db.commit()
            print("admin:", user.email)
        elif args.cmd == "stats":
            from app.services.exams import counts
            print(json.dumps({"/".join(k): v for k, v in counts(db).items()}, indent=1))


if __name__ == "__main__":
    main()
