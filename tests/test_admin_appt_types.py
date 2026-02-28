# tests/test_admin_appt_types.py
import io
import json
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.main import app
from app.dependencies import require_admin, require_csrf
from app.models import AppointmentType


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    # Set upload dir to tmp
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    os.makedirs(tmp_path / "uploads", exist_ok=True)
    from app.config import get_settings
    get_settings.cache_clear()

    # In-memory DB
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # Bypass admin auth and CSRF via dependency_overrides (the correct FastAPI way)
    app.dependency_overrides[require_admin] = lambda: True
    app.dependency_overrides[require_csrf] = lambda: None

    with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as c:
        yield c, TestSession, tmp_path

    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_create_appt_type_with_photo(admin_client):
    client, SessionFactory, tmp_path = admin_client
    upload_dir = str(tmp_path / "uploads")

    resp = client.post(
        "/admin/appointment-types",
        files={"photo": ("house.jpg", io.BytesIO(b"img data"), "image/jpeg")},
        data={
            "name": "Rental Showing",
            "duration_minutes": "30",
            "listing_url": "https://example.com/listing",
            "rental_requirements_json": json.dumps(["No pets", "Income 3x rent"]),
            "owner_reminders_enabled": "true",
        },
    )
    assert resp.status_code == 302

    db = SessionFactory()
    t = db.query(AppointmentType).filter_by(name="Rental Showing").first()
    assert t is not None
    assert t.photo_filename != ""
    assert t.listing_url == "https://example.com/listing"
    assert t.rental_requirements == ["No pets", "Income 3x rent"]
    assert t.owner_reminders_enabled is True
    assert os.path.isfile(os.path.join(upload_dir, t.photo_filename))
    db.close()


def test_update_appt_type_replaces_photo(admin_client):
    client, SessionFactory, tmp_path = admin_client
    upload_dir = str(tmp_path / "uploads")

    # Create initial type with a pre-existing photo
    db = SessionFactory()
    t = AppointmentType(name="Showing", duration_minutes=30)
    t.photo_filename = "old.jpg"
    db.add(t)
    db.commit()
    type_id = t.id
    db.close()

    # Write the old photo file
    with open(os.path.join(upload_dir, "old.jpg"), "wb") as f:
        f.write(b"old img")

    # Update with new photo
    resp = client.post(
        f"/admin/appointment-types/{type_id}",
        files={"photo": ("new.jpg", io.BytesIO(b"new img"), "image/jpeg")},
        data={"name": "Showing", "duration_minutes": "30"},
    )
    assert resp.status_code == 302

    # Old file deleted, new file exists
    assert not os.path.isfile(os.path.join(upload_dir, "old.jpg"))
    db = SessionFactory()
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    assert t.photo_filename != "old.jpg"
    assert t.photo_filename != ""
    assert os.path.isfile(os.path.join(upload_dir, t.photo_filename))
    db.close()


def test_create_appt_type_rejects_javascript_listing_url(admin_client):
    client, SessionFactory, tmp_path = admin_client
    resp = client.post("/admin/appointment-types", data={
        "name": "TestJS",
        "duration_minutes": 30,
        "listing_url": "javascript:alert(1)",
    }, follow_redirects=True)
    assert resp.status_code == 200
    db = SessionFactory()
    t = db.query(AppointmentType).filter_by(name="TestJS").first()
    db.close()
    assert t.listing_url == ""


def test_create_appt_type_accepts_https_listing_url(admin_client):
    client, SessionFactory, tmp_path = admin_client
    resp = client.post("/admin/appointment-types", data={
        "name": "TestHTTPS",
        "duration_minutes": 30,
        "listing_url": "https://example.com/listing",
    }, follow_redirects=True)
    assert resp.status_code == 200
    db = SessionFactory()
    t = db.query(AppointmentType).filter_by(name="TestHTTPS").first()
    db.close()
    assert t.listing_url == "https://example.com/listing"


def test_create_admin_initiated_type(admin_client):
    """Can create an admin-initiated appointment type. requires_drive_time is forced True."""
    client, SessionFactory, _ = admin_client
    resp = client.post(
        "/admin/appointment-types",
        data={
            "name": "Property Inspection",
            "description": "",
            "duration_minutes": "45",
            "buffer_before_minutes": "0",
            "buffer_after_minutes": "15",
            "calendar_id": "primary",
            "color": "#3b82f6",
            "owner_event_title": "Damage Inspection",
            "admin_initiated": "true",
        },
    )
    assert resp.status_code == 302
    db = SessionFactory()
    t = db.query(AppointmentType).filter_by(name="Property Inspection").first()
    assert t is not None
    assert t.admin_initiated is True
    assert t.requires_drive_time is True
    db.close()


def test_create_type_specific_availability_rule(admin_client):
    """Can add a type-specific availability rule to an appointment type."""
    client, SessionFactory, _ = admin_client
    # Create the type first
    client.post(
        "/admin/appointment-types",
        data={
            "name": "Inspection",
            "duration_minutes": "30",
            "admin_initiated": "true",
            "color": "#fff",
            "calendar_id": "primary",
            "owner_event_title": "Insp",
        },
    )
    db = SessionFactory()
    t = db.query(AppointmentType).filter_by(name="Inspection").first()
    type_id = t.id
    db.close()

    resp = client.post(
        f"/admin/appointment-types/{type_id}/rules",
        data={"day_of_week": "1", "start_time": "08:00", "end_time": "16:00"},
    )
    assert resp.status_code == 302

    db = SessionFactory()
    from app.models import AvailabilityRule
    rule = db.query(AvailabilityRule).filter_by(appointment_type_id=type_id).first()
    assert rule is not None
    assert rule.day_of_week == 1
    assert rule.start_time == "08:00"
    assert rule.end_time == "16:00"
    db.close()


def test_remove_photo_flag(admin_client):
    client, SessionFactory, tmp_path = admin_client
    upload_dir = str(tmp_path / "uploads")

    db = SessionFactory()
    t = AppointmentType(name="Showing2", duration_minutes=30)
    t.photo_filename = "existing.jpg"
    db.add(t)
    db.commit()
    type_id = t.id
    db.close()

    with open(os.path.join(upload_dir, "existing.jpg"), "wb") as f:
        f.write(b"x")

    # Post with remove_photo=true, no new photo
    resp = client.post(
        f"/admin/appointment-types/{type_id}",
        data={"name": "Showing2", "duration_minutes": "30", "remove_photo": "true"},
    )
    assert resp.status_code == 302

    assert not os.path.isfile(os.path.join(upload_dir, "existing.jpg"))
    db = SessionFactory()
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    assert (t.photo_filename or "") == ""
    db.close()
