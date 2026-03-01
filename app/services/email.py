import resend
from datetime import datetime
from html import escape


def _format_dt(dt: datetime) -> str:
    return dt.strftime("%A, %B %-d, %Y at %-I:%M %p")


# These are trusted fallback templates — all placeholders must match the kwargs in each send function.
_GUEST_CONFIRMATION_DEFAULT = """\
<div style="font-family:sans-serif;max-width:520px;margin:0 auto;color:#1e293b;">
<h2 style="color:#059669;margin-bottom:.5rem;">Your appointment is confirmed!</h2>
<p>Hi {guest_name},</p>
<p>We&#39;re looking forward to seeing you. Here are your appointment details:</p>
<table style="width:100%;border-collapse:collapse;margin:1rem 0;font-size:.95em;">
  <tr style="border-bottom:1px solid #e2e8f0;">
    <td style="padding:.5rem 1rem .5rem 0;color:#64748b;white-space:nowrap;vertical-align:top;">Appointment</td>
    <td style="padding:.5rem 0;font-weight:600;">{appt_type}</td>
  </tr>
  <tr style="border-bottom:1px solid #e2e8f0;">
    <td style="padding:.5rem 1rem .5rem 0;color:#64748b;white-space:nowrap;vertical-align:top;">Date &amp; Time</td>
    <td style="padding:.5rem 0;">{date_time}</td>
  </tr>
  {location_row}
</table>
{custom_fields}
<p style="margin-top:1.5rem;">Need to reschedule? <a href="{reschedule_url}" style="color:#2563eb;">Click here to pick a new time</a> — it&#39;s quick and easy.</p>
<p style="color:#64748b;font-size:.9em;">To cancel your appointment, please reply to this email.</p>
<p style="margin-top:1.5rem;">See you soon,<br><strong>{owner_name}</strong></p>
</div>"""

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
    reschedule_url: str = "",
    location: str = "",
):
    resend.api_key = api_key
    custom_html = "".join(
        f"<p><strong>{escape(str(k))}:</strong> {escape(str(v))}</p>"
        for k, v in custom_responses.items() if v
    )
    location_row = (
        f'<tr style="border-bottom:1px solid #e2e8f0;">'
        f'<td style="padding:.5rem 1rem .5rem 0;color:#64748b;white-space:nowrap;vertical-align:top;">Location</td>'
        f'<td style="padding:.5rem 0;">{escape(location)}</td>'
        f'</tr>'
    ) if location.strip() else ""
    try:
        html = (template or _GUEST_CONFIRMATION_DEFAULT).format(
            guest_name=escape(guest_name),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
            owner_name=escape(owner_name),
            custom_fields=custom_html,
            reschedule_url=escape(reschedule_url),
            location_row=location_row,
        )
    except (KeyError, ValueError, IndexError):
        html = _GUEST_CONFIRMATION_DEFAULT.format(
            guest_name=escape(guest_name),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
            owner_name=escape(owner_name),
            custom_fields=custom_html,
            reschedule_url=escape(reschedule_url),
            location_row=location_row,
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
        f"<p><strong>{escape(str(k))}:</strong> {escape(str(v))}</p>"
        for k, v in custom_responses.items() if v
    )
    try:
        html = (template or _ADMIN_ALERT_DEFAULT).format(
            guest_name=escape(guest_name),
            guest_email=escape(guest_email),
            guest_phone=escape(guest_phone or "not provided"),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
            notes=escape(notes or "none"),
            custom_fields=custom_html,
        )
    except (KeyError, ValueError, IndexError):
        html = _ADMIN_ALERT_DEFAULT.format(
            guest_name=escape(guest_name),
            guest_email=escape(guest_email),
            guest_phone=escape(guest_phone or "not provided"),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
            notes=escape(notes or "none"),
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
            guest_name=escape(guest_name),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
        )
    except (KeyError, ValueError, IndexError):
        html = _CANCELLATION_DEFAULT.format(
            guest_name=escape(guest_name),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
        )
    resend.Emails.send({
        "from": from_email,
        "to": [guest_email],
        "subject": f"Your {appt_type_name} on {start_dt.strftime('%b %-d')} has been cancelled",
        "html": html,
    })
