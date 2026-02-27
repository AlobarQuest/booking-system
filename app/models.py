import json
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class AppointmentType(Base):
    __tablename__ = "appointment_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    buffer_before_minutes: Mapped[int] = mapped_column(Integer, default=0)
    buffer_after_minutes: Mapped[int] = mapped_column(Integer, default=0)
    calendar_id: Mapped[str] = mapped_column(String(200), default="primary")
    _custom_fields: Mapped[str] = mapped_column("custom_fields", Text, default="[]")
    location: Mapped[str] = mapped_column(Text, default="")
    show_as: Mapped[str] = mapped_column(String(20), default="busy")  # "free" | "busy"
    visibility: Mapped[str] = mapped_column(String(20), default="default")  # "default" | "public" | "private"
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    color: Mapped[str] = mapped_column(String(20), default="#3b82f6")
    owner_event_title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    guest_event_title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    requires_drive_time: Mapped[bool] = mapped_column(Boolean, default=False)
    calendar_window_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    calendar_window_title: Mapped[str] = mapped_column(Text, default="")
    calendar_window_calendar_id: Mapped[str] = mapped_column(Text, default="")
    photo_filename: Mapped[str] = mapped_column(Text, default="")
    listing_url: Mapped[str] = mapped_column(Text, default="")
    _rental_requirements: Mapped[str] = mapped_column("rental_requirements", Text, default="[]")
    owner_reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    bookings: Mapped[list["Booking"]] = relationship(back_populates="appointment_type")

    def __init__(self, **kwargs):
        if "photo_filename" not in kwargs:
            kwargs["photo_filename"] = ""
        if "listing_url" not in kwargs:
            kwargs["listing_url"] = ""
        if "_rental_requirements" not in kwargs and "rental_requirements" not in kwargs:
            kwargs["_rental_requirements"] = "[]"
        if "owner_reminders_enabled" not in kwargs:
            kwargs["owner_reminders_enabled"] = False
        super().__init__(**kwargs)

    @property
    def custom_fields(self) -> list:
        return json.loads(self._custom_fields)

    @custom_fields.setter
    def custom_fields(self, value: list):
        self._custom_fields = json.dumps(value)

    @property
    def rental_requirements(self) -> list:
        if not self._rental_requirements:
            return []
        return json.loads(self._rental_requirements)

    @rental_requirements.setter
    def rental_requirements(self, value: list):
        self._rental_requirements = json.dumps(value)


class AvailabilityRule(Base):
    __tablename__ = "availability_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Mon, 6=Sun
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)  # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def start_time_display(self) -> str:
        from datetime import datetime as _dt
        return _dt.strptime(self.start_time, "%H:%M").strftime("%-I:%M %p")

    @property
    def end_time_display(self) -> str:
        from datetime import datetime as _dt
        return _dt.strptime(self.end_time, "%H:%M").strftime("%-I:%M %p")


class BlockedPeriod(Base):
    __tablename__ = "blocked_periods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_type_id: Mapped[int] = mapped_column(ForeignKey("appointment_types.id"))
    start_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    guest_name: Mapped[str] = mapped_column(String(200), nullable=False)
    guest_email: Mapped[str] = mapped_column(String(200), nullable=False)
    guest_phone: Mapped[str] = mapped_column(String(50), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    _custom_field_responses: Mapped[str] = mapped_column("custom_field_responses", Text, default="{}")
    google_event_id: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="confirmed")  # confirmed | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    appointment_type: Mapped["AppointmentType"] = relationship(back_populates="bookings")

    @property
    def custom_field_responses(self) -> dict:
        return json.loads(self._custom_field_responses)

    @custom_field_responses.setter
    def custom_field_responses(self, value: dict):
        self._custom_field_responses = json.dumps(value)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class DriveTimeCache(Base):
    __tablename__ = "drive_time_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    origin_address: Mapped[str] = mapped_column(Text, nullable=False)
    destination_address: Mapped[str] = mapped_column(Text, nullable=False)
    drive_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    cached_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
