import httpx
from datetime import datetime
from datetime import date as _date_type
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from icalendar import Calendar as ICalendar

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
]


class CalendarService:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def _make_flow(self) -> Flow:
        return Flow.from_client_config(
            {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uris": [self.redirect_uri],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=SCOPES,
            redirect_uri=self.redirect_uri,
        )

    def get_auth_url(self) -> str:
        flow = self._make_flow()
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        return auth_url

    def exchange_code(self, code: str) -> str:
        """Exchange OAuth code for refresh token. Returns the refresh token."""
        flow = self._make_flow()
        flow.fetch_token(code=code)
        token = flow.credentials.refresh_token
        if not token:
            raise ValueError(
                "OAuth exchange did not return a refresh token. "
                "Ensure access_type='offline' and prompt='consent' are set."
            )
        return token

    def is_authorized(self, refresh_token: str) -> bool:
        return bool(refresh_token)

    def _build_service(self, refresh_token: str):
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=SCOPES,
        )
        return build("calendar", "v3", credentials=creds)

    def get_busy_intervals(
        self, refresh_token: str, calendar_ids: list[str], start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Return list of (start, end) busy intervals from Google Calendar freebusy API.
        start and end must be naive UTC datetimes.
        """
        service = self._build_service(refresh_token)
        body = {
            "timeMin": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeMax": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "items": [{"id": cal_id} for cal_id in calendar_ids],
        }
        result = service.freebusy().query(body=body).execute()
        intervals = []
        for cal_data in result.get("calendars", {}).values():
            for busy in cal_data.get("busy", []):
                busy_start = datetime.fromisoformat(busy["start"].replace("Z", "+00:00")).replace(tzinfo=None)
                busy_end = datetime.fromisoformat(busy["end"].replace("Z", "+00:00")).replace(tzinfo=None)
                intervals.append((busy_start, busy_end))
        return intervals

    def create_event(
        self,
        refresh_token: str,
        calendar_id: str,
        summary: str,
        description: str,
        start: datetime,
        end: datetime,
        attendee_email: str = "",
        location: str = "",
        show_as: str = "busy",
        visibility: str = "default",
    ) -> str:
        """Create a calendar event. Returns the event ID."""
        service = self._build_service(refresh_token)
        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S") + "Z", "timeZone": "UTC"},
            "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S") + "Z", "timeZone": "UTC"},
            "transparency": "transparent" if show_as == "free" else "opaque",
            "visibility": visibility,
        }
        if location:
            event["location"] = location
        if attendee_email:
            event["attendees"] = [{"email": attendee_email}]
        result = service.events().insert(calendarId=calendar_id, body=event, sendUpdates="all").execute()
        return result["id"]

    def delete_event(self, refresh_token: str, calendar_id: str, event_id: str):
        """Delete a calendar event."""
        service = self._build_service(refresh_token)
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()


def fetch_webcal_busy(
    url: str, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """Fetch an ICS/webcal feed and return busy (start, end) intervals as naive UTC datetimes.

    Skips recurring events (RRULE). All-day events are treated as busy for the full day (UTC).
    """
    from datetime import timezone as _utc_tz
    http_url = url.replace("webcal://", "https://").replace("webcal:", "https:")
    resp = httpx.get(http_url, timeout=10, follow_redirects=True)
    resp.raise_for_status()

    cal = ICalendar.from_ical(resp.content)
    intervals = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        if component.get("RRULE"):
            continue  # skip recurring events
        dt_start_prop = component.get("dtstart")
        dt_end_prop = component.get("dtend")
        if not dt_start_prop or not dt_end_prop:
            continue
        ev_start = dt_start_prop.dt
        ev_end = dt_end_prop.dt
        # All-day events come as date, not datetime
        if isinstance(ev_start, _date_type) and not isinstance(ev_start, datetime):
            ev_start = datetime(ev_start.year, ev_start.month, ev_start.day, 0, 0, 0)
        if isinstance(ev_end, _date_type) and not isinstance(ev_end, datetime):
            ev_end = datetime(ev_end.year, ev_end.month, ev_end.day, 0, 0, 0)
        # Convert timezone-aware datetimes to naive UTC
        if getattr(ev_start, "tzinfo", None) is not None:
            ev_start = ev_start.astimezone(_utc_tz.utc).replace(tzinfo=None)
        if getattr(ev_end, "tzinfo", None) is not None:
            ev_end = ev_end.astimezone(_utc_tz.utc).replace(tzinfo=None)
        # Only include events overlapping the query window
        if ev_end > start and ev_start < end:
            intervals.append((ev_start, ev_end))
    return intervals
