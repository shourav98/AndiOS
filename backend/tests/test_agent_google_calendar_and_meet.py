"""
Tests for:
1. Per-Agent Google Calendar OAuth (connect, callback, status, disconnect, fallback)
2. Client Google Calendar Attendees & sendUpdates='all'
3. Google Meet Link generation & fallback
4. Viewing booking flow using agent-aware credentials
"""
import json
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock
import pytest
from fastapi.testclient import TestClient

from main import app
from services.calendar_service import (
    create_viewing_event,
    get_calendar_token_for_agent_or_agency,
)
from utils.crypto import encrypt_token
from middleware.auth_middleware import verify_token


@pytest.fixture
def mock_agent_user():
    return {
        "id": "agent-uuid-101",
        "sub": "agent-uuid-101",
        "agent_id": "agent-uuid-101",
        "agency_id": "agency-uuid-999",
        "role": "agent",
    }


@pytest.fixture
def mock_owner_user():
    return {
        "id": "owner-uuid-001",
        "sub": "owner-uuid-001",
        "agency_id": "agency-uuid-999",
        "role": "owner",
    }


# ─── 1. Unit Tests for create_viewing_event (Attendees & Meet Link) ────────────

def test_create_viewing_event_with_attendees_and_meet_link():
    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_insert = MagicMock()

    created_event_data = {
        "id": "g-event-12345",
        "htmlLink": "https://calendar.google.com/event?id=g-event-12345",
        "hangoutLink": "https://meet.google.com/abc-defg-hij",
    }
    mock_insert.execute.return_value = created_event_data
    mock_events.insert.return_value = mock_insert
    mock_service.events.return_value = mock_events

    dummy_tokens = {
        "token": "ya29.dummy-access-token",
        "refresh_token": "1//dummy-refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "dummy-client-id",
        "client_secret": "dummy-secret",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
    }

    start_dt = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)

    with patch("services.calendar_service._build_service", return_value=mock_service):
        result = create_viewing_event(
            token_data=dummy_tokens,
            calendar_id="agent@agency.com",
            lead_name="Sarah Connor",
            lead_phone="+971501234567",
            property_address="Villa 42, Palm Jumeirah",
            start_datetime=start_dt,
            duration_minutes=45,
            agent_name="John Agent",
            lead_email="sarah@example.com",
            create_meet_link=True,
        )

    # Verify return dict has event_id and meet_link
    assert result["event_id"] == "g-event-12345"
    assert result["meet_link"] == "https://meet.google.com/abc-defg-hij"

    # Verify Google Calendar API call kwargs
    mock_events.insert.assert_called_once()
    call_kwargs = mock_events.insert.call_args.kwargs

    assert call_kwargs["calendarId"] == "agent@agency.com"
    assert call_kwargs["conferenceDataVersion"] == 1
    assert call_kwargs["sendUpdates"] == "all"

    event_body = call_kwargs["body"]
    assert event_body["attendees"] == [
        {"email": "sarah@example.com", "displayName": "Sarah Connor", "responseStatus": "needsAction"}
    ]
    assert "conferenceData" in event_body
    assert event_body["conferenceData"]["createRequest"]["conferenceSolutionKey"]["type"] == "hangoutsMeet"
    assert "sarah@example.com" in event_body["description"]
    assert "John Agent" in event_body["description"]


def test_create_viewing_event_meet_fallback_on_conference_restriction():
    """If Google Meet creation fails (e.g. domain restriction), retries without conferenceData."""
    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_insert = MagicMock()

    # First call raises Exception, second call succeeds without conferenceData
    mock_insert.execute.side_effect = [
        Exception("Invalid conference type value: hangoutsMeet"),
        {"id": "g-event-fallback-999", "htmlLink": "https://calendar.google.com/event?id=999"},
    ]
    mock_events.insert.return_value = mock_insert
    mock_service.events.return_value = mock_events

    dummy_tokens = {"token": "dummy", "refresh_token": "dummy", "token_uri": "uri", "client_id": "id", "client_secret": "s", "scopes": []}
    start_dt = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)

    with patch("services.calendar_service._build_service", return_value=mock_service):
        result = create_viewing_event(
            token_data=dummy_tokens,
            calendar_id="primary",
            lead_name="Test Lead",
            lead_phone="+971501112233",
            property_address="Downtown Dubai",
            start_datetime=start_dt,
            create_meet_link=True,
        )

    assert result["event_id"] == "g-event-fallback-999"
    assert result["meet_link"] is None
    assert mock_events.insert.call_count == 2


# ─── 2. Unit Tests for get_calendar_token_for_agent_or_agency ──────────────────

def test_token_resolution_prioritizes_agent_connected_calendar():
    mock_sb = MagicMock()
    mock_agent_table = MagicMock()
    mock_connector_table = MagicMock()

    agent_tokens = {"token": "agent-access-token", "refresh_token": "agent-refresh"}
    encrypted_agent_tokens = encrypt_token(json.dumps(agent_tokens))

    # Agent has token
    mock_agent_res = MagicMock()
    mock_agent_res.data = {
        "id": "agent-123",
        "calendar_id": "john.agent@gmail.com",
        "google_token_data": encrypted_agent_tokens,
        "is_calendar_connected": True,
    }
    mock_agent_table.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = mock_agent_res

    def table_router(name):
        if name == "agents":
            return mock_agent_table
        return mock_connector_table

    mock_sb.table.side_effect = table_router

    cal_id, token_dict, source = get_calendar_token_for_agent_or_agency(
        mock_sb, agency_id="agency-999", agent_id="agent-123"
    )

    assert source == "agent"
    assert cal_id == "john.agent@gmail.com"
    assert token_dict["token"] == "agent-access-token"


def test_token_resolution_falls_back_to_agency_when_agent_not_connected():
    mock_sb = MagicMock()
    mock_agent_table = MagicMock()
    mock_connector_table = MagicMock()

    # Agent exists but has no google_token_data
    mock_agent_res = MagicMock()
    mock_agent_res.data = {
        "id": "agent-123",
        "calendar_id": None,
        "google_token_data": None,
        "is_calendar_connected": False,
    }
    mock_agent_table.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = mock_agent_res

    # Agency has shared connector
    agency_tokens = {"token": "agency-shared-token", "refresh_token": "agency-refresh"}
    mock_connector_res = MagicMock()
    mock_connector_res.data = [
        {"auth_data": agency_tokens, "is_connected": True}
    ]
    mock_connector_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = mock_connector_res

    def table_router(name):
        if name == "agents":
            return mock_agent_table
        return mock_connector_table

    mock_sb.table.side_effect = table_router

    cal_id, token_dict, source = get_calendar_token_for_agent_or_agency(
        mock_sb, agency_id="agency-999", agent_id="agent-123"
    )

    assert source == "agency"
    assert token_dict["token"] == "agency-shared-token"


# ─── 3. Endpoint Tests for Per-Agent OAuth & Connectors ───────────────────────

def test_google_calendar_auth_agent_state(mock_agent_user):
    app.dependency_overrides[verify_token] = lambda: mock_agent_user
    client = TestClient(app)

    response = client.get("/connectors/google-calendar/auth")
    assert response.status_code == 200
    auth_url = response.json()["data"]["auth_url"]
    # Agent calling gets state with agent: prefix
    assert "agent%3Aagency-uuid-999%3Aagent-uuid-101" in auth_url or "agent:agency-uuid-999:agent-uuid-101" in auth_url
    app.dependency_overrides.clear()


def test_google_calendar_status_endpoint(mock_agent_user):
    app.dependency_overrides[verify_token] = lambda: mock_agent_user
    mock_sb = MagicMock()

    mock_agent_res = MagicMock(data={"calendar_id": "agent@gmail.com", "google_token_data": "enc", "is_calendar_connected": True})
    mock_connector_res = MagicMock(data=[{"is_connected": True}])

    def table_router(name):
        mock_t = MagicMock()
        if name == "agents":
            mock_t.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = mock_agent_res
        elif name == "connectors":
            mock_t.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = mock_connector_res
        return mock_t

    mock_sb.table.side_effect = table_router

    with patch("routers.connectors.get_supabase", return_value=mock_sb):
        client = TestClient(app)
        response = client.get("/connectors/google-calendar/status")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["is_connected"] is True
        assert data["agent_calendar_connected"] is True
        assert data["agent_calendar_id"] == "agent@gmail.com"
        assert data["mode"] == "agent"

    app.dependency_overrides.clear()


def test_google_calendar_agent_disconnect(mock_agent_user):
    app.dependency_overrides[verify_token] = lambda: mock_agent_user
    mock_sb = MagicMock()
    mock_table = MagicMock()
    mock_update = MagicMock()
    mock_update.execute.return_value = MagicMock(data=[])
    mock_table.update.return_value.eq.return_value.eq.return_value = mock_update
    mock_sb.table.return_value = mock_table

    with patch("routers.connectors.get_supabase", return_value=mock_sb):
        client = TestClient(app)
        response = client.post("/connectors/google-calendar/disconnect?agent_id=agent-uuid-101")
        assert response.status_code == 200
        assert response.json()["message"] == "Agent Google Calendar disconnected"
        # Verify agents table was updated
        mock_table.update.assert_called_with({"google_token_data": None, "is_calendar_connected": False})

    app.dependency_overrides.clear()
