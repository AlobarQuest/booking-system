from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import AppointmentType, AvailabilityRule, BlockedPeriod, Booking, Setting


def test_all_tables_create():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    appt = AppointmentType(
        name="Phone Call",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=5,
        calendar_id="primary",
        custom_fields=[],
        active=True,
        color="#3b82f6",
    )
    db.add(appt)
    db.commit()
    assert appt.id is not None

    rule = AvailabilityRule(day_of_week=0, start_time="09:00", end_time="17:00", active=True)
    db.add(rule)
    db.commit()
    assert rule.id is not None

    setting = Setting(key="timezone", value="America/New_York")
    db.add(setting)
    db.commit()
    assert db.query(Setting).filter_by(key="timezone").first().value == "America/New_York"
    db.close()
