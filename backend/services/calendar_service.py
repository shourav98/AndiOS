"""
Google Calendar Service — OAuth2 flow + event management.
Supports shared calendar or per-agent calendar modes.
"""
import json
from datetime import datetime, timedelta
from typing import Optional
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from config import settings
import logging

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_oauth_flow() -> Flow:
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = settings.GOOGLE_REDIRECT_URI
    return flow


def get_auth_url() -> str:
    flow = get_oauth_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return auth_url


def exchange_code_for_tokens(code: str) -> dict:
    flow = get_oauth_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
    }


def _build_service(token_data: dict):
    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data["scopes"],
    )
    return build("calendar", "v3", credentials=creds)


def get_available_slots(
    token_data: dict,
    calendar_id: str,
    date_from: datetime,
    date_to: datetime,
    duration_minutes: int = 60,
) -> list[dict]:
    """Get available viewing slots by checking freebusy."""
    try:
        service = _build_service(token_data)

        # Query busy times
        body = {
            "timeMin": date_from.isoformat() + "Z",
            "timeMax": date_to.isoformat() + "Z",
            "items": [{"id": calendar_id}],
        }
        freebusy = service.freebusy().query(body=body).execute()
        busy_times = freebusy["calendars"].get(calendar_id, {}).get("busy", [])

        # Generate slots (9am-6pm, every hour)
        slots = []
        current = date_from.replace(hour=9, minute=0, second=0, microsecond=0)
        end_day = date_to.replace(hour=18, minute=0, second=0, microsecond=0)

        while current < end_day:
            slot_end = current + timedelta(minutes=duration_minutes)
            # Check if slot overlaps with any busy period
            is_free = True
            for busy in busy_times:
                busy_start = datetime.fromisoformat(busy["start"].replace("Z", "+00:00"))
                busy_end = datetime.fromisoformat(busy["end"].replace("Z", "+00:00"))
                if current < busy_end and slot_end > busy_start:
                    is_free = False
                    break
            if is_free and current.hour >= 9 and slot_end.hour <= 18:
                slots.append({
                    "start": current.isoformat(),
                    "end": slot_end.isoformat(),
                })
            current += timedelta(hours=1)

        return slots
    except Exception as e:
        logger.error(f"Error getting calendar slots: {e}")
        return []


def create_viewing_event(
    token_data: dict,
    calendar_id: str,
    lead_name: str,
    lead_phone: str,
    property_address: str,
    start_datetime: datetime,
    duration_minutes: int = 60,
    agent_name: Optional[str] = None,
) -> dict:
    """Create a Google Calendar event for a property viewing."""
    try:
        service = _build_service(token_data)
        end_datetime = start_datetime + timedelta(minutes=duration_minutes)

        event = {
            "summary": f"🏠 Viewing: {lead_name} — {property_address}",
            "description": (
                f"Lead: {lead_name}\nPhone: {lead_phone}\n"
                f"Property: {property_address}\n"
                f"Booked via AndiOS AI"
                + (f"\nAgent: {agent_name}" if agent_name else "")
            ),
            "location": property_address,
            "start": {
                "dateTime": start_datetime.isoformat() + "Z",
                "timeZone": "Asia/Dubai",
            },
            "end": {
                "dateTime": end_datetime.isoformat() + "Z",
                "timeZone": "Asia/Dubai",
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 120},
                    {"method": "popup", "minutes": 30},
                ],
            },
            "conferenceData": {
                "createRequest": {
                    "requestId": f"andios-{lead_name}-{int(start_datetime.timestamp())}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }

        created = service.events().insert(
            calendarId=calendar_id,
            body=event,
            conferenceDataVersion=1,
        ).execute()

        meet_link = None
        if "conferenceData" in created:
            for ep in created["conferenceData"].get("entryPoints", []):
                if ep.get("entryPointType") == "video":
                    meet_link = ep.get("uri")

        return {
            "event_id": created["id"],
            "html_link": created.get("htmlLink"),
            "meet_link": meet_link,
        }
    except Exception as e:
        logger.error(f"Error creating calendar event: {e}")
        return {}


def cancel_viewing_event(token_data: dict, calendar_id: str, event_id: str) -> bool:
    """Delete/cancel a Google Calendar event."""
    try:
        service = _build_service(token_data)
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error cancelling event: {e}")
        return False
