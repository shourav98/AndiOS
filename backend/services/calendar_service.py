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


def get_redirect_uri() -> str:
    """Return the configured redirect URI or dynamically build it from API_BASE_URL."""
    configured = (getattr(settings, "GOOGLE_REDIRECT_URI", "") or "").strip()
    if configured:
        return configured
    api_base = (getattr(settings, "API_BASE_URL", "") or "http://localhost:8000").rstrip("/")
    return f"{api_base}/connectors/google-calendar/callback"


def get_oauth_flow() -> Flow:
    redirect_uri = get_redirect_uri()
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = redirect_uri
    return flow


import urllib.parse
import httpx

def get_auth_url(state: str = None) -> str:
    redirect_uri = get_redirect_uri()
    base_url = "https://accounts.google.com/o/oauth2/auth"
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    return f"{base_url}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict:
    redirect_uri = get_redirect_uri()
    data = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    resp = httpx.post("https://oauth2.googleapis.com/token", data=data)
    resp.raise_for_status()
    token_json = resp.json()
    
    return {
        "token": token_json.get("access_token"),
        "refresh_token": token_json.get("refresh_token"),
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "scopes": SCOPES,
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
    lead_email: Optional[str] = None,
    create_meet_link: bool = True,
) -> dict:
    """
    Create a Google Calendar event for a property viewing.
    - Adds lead_email to attendees with sendUpdates='all' to send native Google Calendar invite
    - Requests Google Meet conference room (hangoutsMeet) with conferenceDataVersion=1
    """
    try:
        service = _build_service(token_data)
        end_datetime = start_datetime + timedelta(minutes=duration_minutes)

        # Ensure datetime is in RFC3339 format for Google Calendar
        def _format_dt(dt: datetime) -> str:
            if dt.tzinfo is not None:
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt.utcoffset().total_seconds() == 0 else dt.isoformat()
            else:
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Format Dubai time display for cross-timezone clarity
        try:
            import pytz
            dubai_tz = pytz.timezone("Asia/Dubai")
            dt_dubai = start_datetime.astimezone(dubai_tz) if start_datetime.tzinfo else start_datetime
            dubai_time_str = dt_dubai.strftime("%I:%M %p")
        except Exception:
            dubai_time_str = start_datetime.strftime("%I:%M %p")

        desc_lines = [
            f"Lead: {lead_name}",
            f"Phone: {lead_phone}",
        ]
        if lead_email:
            desc_lines.append(f"Email: {lead_email}")
        desc_lines.extend([
            f"Property: {property_address}",
            f"Time: {dubai_time_str} (Dubai Time, GMT+4)",
            "Booked via AndiOS AI",
        ])
        if agent_name:
            desc_lines.append(f"Agent: {agent_name}")

        event = {
            "summary": f"🏠 Viewing: {lead_name} — {property_address} ({dubai_time_str} Dubai Time)",
            "description": "\n".join(desc_lines),
            "location": property_address,
            "start": {
                "dateTime": _format_dt(start_datetime),
                "timeZone": "Asia/Dubai",
            },
            "end": {
                "dateTime": _format_dt(end_datetime),
                "timeZone": "Asia/Dubai",
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 120},
                    {"method": "popup", "minutes": 30},
                ],
            },
        }

        # 1. Add Client to Google Calendar Attendees
        if lead_email and str(lead_email).strip():
            event["attendees"] = [
                {
                    "email": str(lead_email).strip(),
                    "displayName": lead_name,
                    "responseStatus": "needsAction",
                }
            ]

        # 2. Enable Google Meet / Conference Link Generation
        import uuid
        if create_meet_link:
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": f"andios-{uuid.uuid4().hex[:12]}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }

        insert_kwargs = {
            "calendarId": calendar_id,
            "body": event,
        }
        if create_meet_link:
            insert_kwargs["conferenceDataVersion"] = 1
        if lead_email and str(lead_email).strip():
            insert_kwargs["sendUpdates"] = "all"

        try:
            created = service.events().insert(**insert_kwargs).execute()
        except Exception as conf_err:
            if create_meet_link:
                logger.warning(f"Google Meet conference creation failed ({conf_err}), retrying standard event...")
                insert_kwargs.pop("conferenceDataVersion", None)
                event.pop("conferenceData", None)
                created = service.events().insert(**insert_kwargs).execute()
            else:
                raise

        # Extract meet_link from created event
        meet_link = created.get("hangoutLink")
        if not meet_link and "conferenceData" in created:
            entry_points = created["conferenceData"].get("entryPoints", [])
            for ep in entry_points:
                if ep.get("entryPointType") == "video":
                    meet_link = ep.get("uri")
                    break

        return {
            "event_id": created.get("id"),
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


def get_calendar_token_for_agent_or_agency(
    sb,
    agency_id: str,
    agent_id: Optional[str] = None,
) -> tuple[str, dict, str]:
    """
    Resolve Google Calendar credentials with Agent Priority + Agency Fallback:
    1. If agent_id provided: check if agent has their own connected Google Calendar tokens.
    2. Fallback gracefully to the agency's shared Google Calendar if agent hasn't connected theirs.
    Returns: (calendar_id, token_data, source: 'agent' | 'agency')
    Raises: HTTPException(400) if neither is connected.
    """
    from fastapi import HTTPException
    from utils.crypto import decrypt_token, is_encrypted

    # 1. Check Agent's Personal Google Calendar
    if agent_id:
        try:
            agent_res = (
                sb.table("agents")
                .select("id, name, calendar_id, google_token_data, is_calendar_connected")
                .eq("id", str(agent_id))
                .eq("agency_id", agency_id)
                .maybe_single()
                .execute()
            )
            agent_row = agent_res.data if agent_res else None
            if agent_row and agent_row.get("google_token_data"):
                raw_token = agent_row["google_token_data"]
                token_dict = None
                if isinstance(raw_token, str):
                    try:
                        decrypted = decrypt_token(raw_token, account_id=f"agent-{agent_id}")
                        token_dict = json.loads(decrypted) if decrypted else None
                    except Exception:
                        try:
                            token_dict = json.loads(raw_token)
                        except Exception:
                            token_dict = None
                elif isinstance(raw_token, dict):
                    token_dict = raw_token

                if token_dict and token_dict.get("token"):
                    cal_id = agent_row.get("calendar_id") or "primary"
                    return cal_id, token_dict, "agent"
        except Exception as e:
            logger.warning(f"Error checking agent {agent_id} personal calendar: {e}")

    # 2. Fallback to Agency's Shared Google Calendar
    connector = (
        sb.table("connectors")
        .select("auth_data")
        .eq("name", "google_calendar")
        .eq("agency_id", agency_id)
        .eq("is_connected", True)
        .limit(1)
        .execute()
    )
    if connector.data and connector.data[0].get("auth_data"):
        raw_auth = connector.data[0]["auth_data"]
        token_dict = None
        if isinstance(raw_auth, str):
            try:
                decrypted = decrypt_token(raw_auth, account_id=f"agency-{agency_id}")
                token_dict = json.loads(decrypted) if decrypted else None
            except Exception:
                try:
                    token_dict = json.loads(raw_auth)
                except Exception:
                    token_dict = None
        elif isinstance(raw_auth, dict):
            token_dict = raw_auth

        if token_dict and token_dict.get("token"):
            cal_id = settings.GOOGLE_SHARED_CALENDAR_ID or "primary"
            return cal_id, token_dict, "agency"

    raise HTTPException(
        status_code=400,
        detail="Google Calendar not connected. Please connect Google Calendar in settings.",
    )

