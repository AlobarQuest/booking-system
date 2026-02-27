# tests/test_booking_page.py
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.main import app
from app.models import AppointmentType


@pytest.fixture
def booking_client(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    from app.config import get_settings
    get_settings.cache_clear()

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

    db = TestSession()
    t = AppointmentType(
        name="Rental Showing",
        duration_minutes=30,
        active=True,
    )
    t.photo_filename = "house.jpg"
    t.listing_url = "https://example.com/listing"
    t.rental_requirements = ["No pets", "Income 3x rent"]
    db.add(t)
    db.commit()
    db.close()

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_booking_page_shows_photo(booking_client):
    resp = booking_client.get("/")
    assert resp.status_code == 200
    assert '/uploads/house.jpg' in resp.text


def test_booking_page_shows_listing_link(booking_client):
    resp = booking_client.get("/")
    assert resp.status_code == 200
    assert 'https://example.com/listing' in resp.text
    assert 'View Listing' in resp.text


def test_booking_page_shows_requirements_button(booking_client):
    resp = booking_client.get("/")
    assert resp.status_code == 200
    assert 'View Requirements' in resp.text
    assert 'No pets' in resp.text


def test_booking_page_no_requirements_no_button(client):
    # client fixture from conftest has no appointment types by default
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'View Requirements' not in resp.text


def test_booking_page_card_content_has_class(booking_client):
    resp = booking_client.get("/")
    assert resp.status_code == 200
    assert 'class="card-content"' in resp.text
    assert 'class="card-text"' in resp.text
