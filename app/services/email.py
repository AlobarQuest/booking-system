import resend
from datetime import datetime


def _format_dt(dt: datetime) -> str:
    return dt.strftime("%A, %B %-d, %Y at %-I:%M %p UTC")


def send_guest_confirmation(
    api_key: str,
    from_email: str,
    guest_email: str,
    guest_name: str,
    appt_type_name: str,
    start_dt: datetime,
    end_dt: datetime,
    custom_responses: dict,
    owner_name: str,
):
    resend.api_key = api_key
    custom_html = "".join(
        f"<p><strong>{k}:</strong> {v}</p>"
        for k, v in custom_responses.items()
        if v
    )
    html = f"""
    <h2>Your appointment is confirmed</h2>
    <p>Hi {guest_name},</p>
    <p>Your <strong>{appt_type_name}</strong> is confirmed:</p>
    <p><strong>Date/Time:</strong> {_format_dt(start_dt)}</p>
    {custom_html}
    <p>If you need to cancel, please reply to this email.</p>
    <p>— {owner_name}</p>
    """
    resend.Emails.send({
        "from": from_email,
        "to": [guest_email],
        "subject": f"Your {appt_type_name} is confirmed — {start_dt.strftime('%b %-d')}",
        "html": html,
    })


def send_admin_alert(
    api_key: str,
    from_email: str,
    notify_email: str,
    guest_name: str,
    guest_email: str,
    guest_phone: str,
    appt_type_name: str,
    start_dt: datetime,
    notes: str,
    custom_responses: dict,
):
    resend.api_key = api_key
    custom_html = "".join(
        f"<p><strong>{k}:</strong> {v}</p>"
        for k, v in custom_responses.items()
        if v
    )
    html = f"""
    <h2>New Booking: {guest_name}</h2>
    <p><strong>Type:</strong> {appt_type_name}</p>
    <p><strong>Date/Time:</strong> {_format_dt(start_dt)}</p>
    <p><strong>Guest:</strong> {guest_name}</p>
    <p><strong>Email:</strong> {guest_email}</p>
    <p><strong>Phone:</strong> {guest_phone or 'not provided'}</p>
    {custom_html}
    <p><strong>Notes:</strong> {notes or 'none'}</p>
    <p><a href="/admin/bookings">View in admin panel</a></p>
    """
    resend.Emails.send({
        "from": from_email,
        "to": [notify_email],
        "subject": f"New booking: {guest_name} — {appt_type_name} on {start_dt.strftime('%b %-d')}",
        "html": html,
    })


def send_cancellation_notice(
    api_key: str,
    from_email: str,
    guest_email: str,
    guest_name: str,
    appt_type_name: str,
    start_dt: datetime,
):
    resend.api_key = api_key
    html = f"""
    <h2>Appointment Cancelled</h2>
    <p>Hi {guest_name},</p>
    <p>Your <strong>{appt_type_name}</strong> on {_format_dt(start_dt)} has been cancelled.</p>
    <p>Please reach out to reschedule.</p>
    """
    resend.Emails.send({
        "from": from_email,
        "to": [guest_email],
        "subject": f"Your {appt_type_name} on {start_dt.strftime('%b %-d')} has been cancelled",
        "html": html,
    })
