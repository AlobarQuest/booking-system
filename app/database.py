from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    connect_args = {"check_same_thread": False} if "sqlite" in settings.database_url else {}
    return create_engine(settings.database_url, connect_args=connect_args)


engine = _make_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    # Add columns introduced after initial schema (SQLite doesn't support IF NOT EXISTS on ALTER)
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(appointment_types)"))}
        for col, definition in [
            ("location", "TEXT NOT NULL DEFAULT ''"),
            ("show_as", "VARCHAR(20) NOT NULL DEFAULT 'busy'"),
            ("visibility", "VARCHAR(20) NOT NULL DEFAULT 'default'"),
        ]:
            if col not in existing:
                conn.execute(text(f"ALTER TABLE appointment_types ADD COLUMN {col} {definition}"))
        conn.commit()
