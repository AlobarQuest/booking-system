import uuid

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
            ("owner_event_title", "TEXT NOT NULL DEFAULT ''"),
            ("guest_event_title", "TEXT NOT NULL DEFAULT ''"),
            ("requires_drive_time", "BOOLEAN NOT NULL DEFAULT 0"),
            ("calendar_window_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
            ("calendar_window_title", "TEXT NOT NULL DEFAULT ''"),
            ("calendar_window_calendar_id", "TEXT NOT NULL DEFAULT ''"),
            ("photo_filename", "TEXT NOT NULL DEFAULT ''"),
            ("listing_url", "TEXT NOT NULL DEFAULT ''"),
            ("rental_application_url", "TEXT NOT NULL DEFAULT ''"),
            ("rental_requirements", "TEXT NOT NULL DEFAULT '[]'"),
            ("owner_reminders_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
            ("admin_initiated", "BOOLEAN NOT NULL DEFAULT 0"),
        ]:
            if col not in existing:
                conn.execute(text(f"ALTER TABLE appointment_types ADD COLUMN {col} {definition}"))

        existing_b = {row[1] for row in conn.execute(text("PRAGMA table_info(bookings)"))}
        for col, definition in [
            ("location", "TEXT NOT NULL DEFAULT ''"),
            ("reschedule_token", "VARCHAR(36) NOT NULL DEFAULT ''"),
        ]:
            if col not in existing_b:
                conn.execute(text(f"ALTER TABLE bookings ADD COLUMN {col} {definition}"))

        # Backfill reschedule_token for existing bookings that have none
        rows = conn.execute(text("SELECT id FROM bookings WHERE reschedule_token = ''")).fetchall()
        for (row_id,) in rows:
            conn.execute(
                text("UPDATE bookings SET reschedule_token = :tok WHERE id = :id"),
                {"tok": str(uuid.uuid4()), "id": row_id},
            )

        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_bookings_reschedule_token "
            "ON bookings (reschedule_token)"
        ))

        existing_ar = {row[1] for row in conn.execute(text("PRAGMA table_info(availability_rules)"))}
        for col, definition in [
            ("appointment_type_id", "INTEGER REFERENCES appointment_types(id)"),
        ]:
            if col not in existing_ar:
                conn.execute(text(f"ALTER TABLE availability_rules ADD COLUMN {col} {definition}"))

        conn.commit()
