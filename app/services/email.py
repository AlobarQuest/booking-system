import resend
from datetime import datetime
from html import escape


def _format_dt(dt: datetime) -> str:
    return dt.strftime("%A, %B %-d, %Y at %-I:%M %p")


def _render_custom_fields(custom_responses: dict) -> str:
    return "".join(
        f"<p><strong>{escape(str(k))}:</strong> {escape(str(v))}</p>"
        for k, v in custom_responses.items() if v
    )


def _render(template: str, default: str, **context) -> str:
    """Render a user-editable template, falling back to the trusted default.

    Admin-edited templates may reference placeholders that no longer exist
    (or contain stray braces); the default must always render.
    """
    try:
        return (template or default).format(**context)
    except (KeyError, ValueError, IndexError):
        return default.format(**context)


def _send(api_key: str, from_email: str, to_email: str, subject: str, html: str) -> None:
    resend.api_key = api_key
    resend.Emails.send({
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html,
    })


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
{agent_info}
<p style="margin-top:1.5rem;">Need to reschedule? <a href="{reschedule_url}" style="color:#2563eb;">Click here to pick a new time</a> — it&#39;s quick and easy.</p>
<p style="color:#64748b;font-size:.9em;">Need to cancel? <a href="{cancel_url}" style="color:#64748b;">Cancel your appointment here</a>.</p>
<p style="margin-top:1.5rem;">See you soon,<br><strong>{owner_name}</strong></p>
</div>"""

_ADMIN_ALERT_DEFAULT = """\
<h2>New Booking: {guest_name}</h2>
<p><strong>Type:</strong> {appt_type}</p>
<p><strong>Date/Time:</strong> {date_time}</p>
{location_line}<p><strong>Guest:</strong> {guest_name}</p>
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

# Ops alert — not admin-editable like the templates above.
_GOOGLE_TOKEN_ALERT = """\
<h2 style="color:#dc2626;">Google Calendar connection is broken</h2>
<p>Google rejected the saved Calendar credentials (the refresh token was revoked or expired).</p>
<p><strong>Until you re-authorize, booking slots are offered without checking your
calendar for conflicts</strong>, and new bookings will not create calendar events.</p>
<p><a href="{settings_url}">Open admin settings to re-authorize Google Calendar</a></p>"""


def send_google_token_alert(api_key: str, from_email: str, notify_email: str, settings_url: str):
    _send(
        api_key, from_email, notify_email,
        subject="Action needed: Google Calendar disconnected — bookings are not conflict-checked",
        html=_GOOGLE_TOKEN_ALERT.format(settings_url=settings_url),
    )


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
    cancel_url: str = "",
    location: str = "",
    contact_phone: str = "",
):
    location_row = (
        f'<tr style="border-bottom:1px solid #e2e8f0;">'
        f'<td style="padding:.5rem 1rem .5rem 0;color:#64748b;white-space:nowrap;vertical-align:top;">Location</td>'
        f'<td style="padding:.5rem 0;">{escape(location)}</td>'
        f'</tr>'
    ) if location.strip() else ""
    agent_info = (
        f'<p style="background:#f0fdf4;border-left:4px solid #059669;'
        f'padding:.75rem 1rem;margin:1.5rem 0;border-radius:0 .5rem .5rem 0;">'
        f'This is an agent guided tour. The agent will meet you at the property '
        f'at your appointment time. Their number is <strong>{escape(contact_phone)}</strong>.</p>'
    ) if contact_phone.strip() else ""
    html = _render(
        template,
        _GUEST_CONFIRMATION_DEFAULT,
        guest_name=escape(guest_name),
        appt_type=escape(appt_type_name),
        date_time=_format_dt(start_dt),
        owner_name=escape(owner_name),
        custom_fields=_render_custom_fields(custom_responses),
        reschedule_url=escape(reschedule_url),
        cancel_url=escape(cancel_url),
        location_row=location_row,
        agent_info=agent_info,
    )
    _send(
        api_key, from_email, guest_email,
        subject=f"Your {appt_type_name} is confirmed — {start_dt.strftime('%b %-d')}",
        html=html,
    )


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
    location: str = "",
):
    location_line = f"<p><strong>Location:</strong> {escape(location)}</p>\n" if location.strip() else ""
    html = _render(
        template,
        _ADMIN_ALERT_DEFAULT,
        guest_name=escape(guest_name),
        guest_email=escape(guest_email),
        guest_phone=escape(guest_phone or "not provided"),
        appt_type=escape(appt_type_name),
        date_time=_format_dt(start_dt),
        notes=escape(notes or "none"),
        custom_fields=_render_custom_fields(custom_responses),
        location_line=location_line,
    )
    _send(
        api_key, from_email, notify_email,
        subject=f"New booking: {guest_name} — {appt_type_name} on {start_dt.strftime('%b %-d')}",
        html=html,
    )


def send_cancellation_notice(
    api_key: str,
    from_email: str,
    guest_email: str,
    guest_name: str,
    appt_type_name: str,
    start_dt: datetime,
    template: str = "",
):
    html = _render(
        template,
        _CANCELLATION_DEFAULT,
        guest_name=escape(guest_name),
        appt_type=escape(appt_type_name),
        date_time=_format_dt(start_dt),
    )
    _send(
        api_key, from_email, guest_email,
        subject=f"Your {appt_type_name} on {start_dt.strftime('%b %-d')} has been cancelled",
        html=html,
    )
