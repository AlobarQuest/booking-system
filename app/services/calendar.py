from datetime import datetime
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

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
    ) -> str:
        """Create a calendar event. Returns the event ID."""
        service = self._build_service(refresh_token)
        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S") + "Z", "timeZone": "UTC"},
            "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S") + "Z", "timeZone": "UTC"},
        }
        if attendee_email:
            event["attendees"] = [{"email": attendee_email}]
        result = service.events().insert(calendarId=calendar_id, body=event, sendUpdates="all").execute()
        return result["id"]

    def delete_event(self, refresh_token: str, calendar_id: str, event_id: str):
        """Delete a calendar event."""
        service = self._build_service(refresh_token)
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
