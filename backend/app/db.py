from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    kwargs = {}
    if url.startswith("sqlite"):
        # Wait up to 30 s for a write lock instead of failing at once (background jobs share the file).
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        # Serverless Postgres (e.g. Neon's free tier) closes idle connections; test before reuse, recycle often.
        kwargs.update(pool_pre_ping=True, pool_recycle=300, pool_size=5, max_overflow=5)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # SQLite needs FKs switched on per connection
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            if ":memory:" not in url:
                # WAL: readers are never blocked by a writer, so the site stays responsive during data jobs.
                dbapi_conn.execute("PRAGMA journal_mode=WAL")
    return engine


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
