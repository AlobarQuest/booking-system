import resend
from datetime import datetime


def _format_dt(dt: datetime) -> str:
    return dt.strftime("%A, %B %-d, %Y at %-I:%M %p UTC")


# These are trusted fallback templates — all placeholders must match the kwargs in each send function.
_GUEST_CONFIRMATION_DEFAULT = """\
<h2>Your appointment is confirmed</h2>
<p>Hi {guest_name},</p>
<p>Your <strong>{appt_type}</strong> is confirmed:</p>
<p><strong>Date/Time:</strong> {date_time}</p>
{custom_fields}
<p>If you need to cancel, please reply to this email.</p>
<p>— {owner_name}</p>"""

_ADMIN_ALERT_DEFAULT = """\
<h2>New Booking: {guest_name}</h2>
<p><strong>Type:</strong> {appt_type}</p>
<p><strong>Date/Time:</strong> {date_time}</p>
<p><strong>Guest:</strong> {guest_name}</p>
<p><strong>Email:</strong> {guest_email}</p>
<p><strong>Phone:</strong> {guest_phone}</p>
{custom_fields}
<p><strong>Notes:</strong> {notes}</p>
<p><a href="/admin/bookings">View in admin panel</a></p>"""

_CANCELLATION_DEFAULT = """\
<h2>Appointment Cancelled</h2>
<p>Hi {guest_name},</p>
<p>Your <strong>{appt_type}</strong> on {date_time} has been cancelled.</p>
<p>Please reach out to reschedule.</p>"""


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
    template: str = "",
):
    resend.api_key = api_key
    custom_html = "".join(
        f"<p><strong>{k}:</strong> {v}</p>"
        for k, v in custom_responses.items() if v
    )
    try:
        html = (template or _GUEST_CONFIRMATION_DEFAULT).format(
            guest_name=guest_name,
            appt_type=appt_type_name,
            date_time=_format_dt(start_dt),
            owner_name=owner_name,
            custom_fields=custom_html,
        )
    except (KeyError, ValueError, IndexError):
        html = _GUEST_CONFIRMATION_DEFAULT.format(
            guest_name=guest_name,
            appt_type=appt_type_name,
            date_time=_format_dt(start_dt),
            owner_name=owner_name,
            custom_fields=custom_html,
        )
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
    template: str = "",
):
    resend.api_key = api_key
    custom_html = "".join(
        f"<p><strong>{k}:</strong> {v}</p>"
        for k, v in custom_responses.items() if v
    )
    try:
        html = (template or _ADMIN_ALERT_DEFAULT).format(
            guest_name=guest_name,
            guest_email=guest_email,
            guest_phone=guest_phone or "not provided",
            appt_type=appt_type_name,
            date_time=_format_dt(start_dt),
            notes=notes or "none",
            custom_fields=custom_html,
        )
    except (KeyError, ValueError, IndexError):
        html = _ADMIN_ALERT_DEFAULT.format(
            guest_name=guest_name,
            guest_email=guest_email,
            guest_phone=guest_phone or "not provided",
            appt_type=appt_type_name,
            date_time=_format_dt(start_dt),
            notes=notes or "none",
            custom_fields=custom_html,
        )
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
    template: str = "",
):
    resend.api_key = api_key
    try:
        html = (template or _CANCELLATION_DEFAULT).format(
            guest_name=guest_name,
            appt_type=appt_type_name,
            date_time=_format_dt(start_dt),
        )
    except (KeyError, ValueError, IndexError):
        html = _CANCELLATION_DEFAULT.format(
            guest_name=guest_name,
            appt_type=appt_type_name,
            date_time=_format_dt(start_dt),
        )
    resend.Emails.send({
        "from": from_email,
        "to": [guest_email],
        "subject": f"Your {appt_type_name} on {start_dt.strftime('%b %-d')} has been cancelled",
        "html": html,
    })
