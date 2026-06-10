from datetime import date as date_type

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AppointmentType
from app.services.slots import compute_slots_for_type
from app.templating import templates

router = APIRouter()


@router.get("/slots", response_class=HTMLResponse)
def get_slots(
    request: Request,
    type_id: int = Query(...),
    date: str = Query(...),
    destination: str = Query(""),
    db: Session = Depends(get_db),
):
    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True).first()
    if not appt_type:
        return HTMLResponse("<p class='no-slots'>Appointment type not found.</p>")
    try:
        target_date = date_type.fromisoformat(date)
    except ValueError:
        return HTMLResponse("<p class='no-slots'>Invalid date format.</p>")

    slot_data = compute_slots_for_type(appt_type, target_date, db, destination=destination)
    return templates.TemplateResponse(
        "booking/slots_partial.html",
        {"request": request, "slots": slot_data, "type_id": type_id, "date": date},
    )
