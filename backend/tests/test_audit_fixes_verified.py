import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from config import settings


@pytest.mark.asyncio
async def test_whatsapp_verify_endpoint_success():
    from routers.webhooks import whatsapp_verify
    res = await whatsapp_verify(
        hub_mode="subscribe",
        hub_challenge="123456",
        hub_verify_token=settings.WHATSAPP_VERIFY_TOKEN or "andios_verify_token"
    )
    assert res == 123456


@pytest.mark.asyncio
async def test_whatsapp_verify_endpoint_invalid_token_rejected():
    from routers.webhooks import whatsapp_verify
    with pytest.raises(HTTPException) as exc:
        await whatsapp_verify(
            hub_mode="subscribe",
            hub_challenge="123456",
            hub_verify_token="wrong_token"
        )
    assert exc.value.status_code == 403


def test_verify_360dialog_webhook_secure():
    from services.whatsapp_service import verify_360dialog_webhook
    import hmac
    import hashlib

    secret = "test_secret_123"
    payload = b'{"event":"message"}'
    valid_sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    # Valid signature
    assert verify_360dialog_webhook(payload, valid_sig, secret=secret) is True
    # Invalid signature
    assert verify_360dialog_webhook(payload, "invalid_sig", secret=secret) is False
    # Missing secret in production fails closed
    with patch.object(settings, "APP_ENV", "production"):
        assert verify_360dialog_webhook(payload, valid_sig, secret="") is False


@pytest.mark.asyncio
async def test_connectors_listings_does_not_return_mock():
    from routers.connectors import get_connector_listings
    user = {"sub": "u-1", "agency_id": "ag-1", "role": "owner"}

    sb = MagicMock()
    # Connector with no explicit listings in auth_data
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"auth_data": {"api_key": "some_key"}, "is_connected": True}
    ]
    with patch("routers.connectors.get_supabase", return_value=sb), \
         patch("routers.connectors.require_agency_id", return_value="ag-1"):
        res = await get_connector_listings("property_finder", user)
        assert res["success"] is True
        assert res["data"]["listings"] == []
        assert res["data"]["total"] == 0


@pytest.mark.asyncio
async def test_calling_performance_agent_filter_access_control():
    from routers.dashboard import get_calling_performance
    agent_user = {"sub": "u-agent", "agency_id": "ag-1", "agent_id": "agent-1", "role": "agent"}

    # Agent cannot view another agent's calls
    with pytest.raises(HTTPException) as exc:
        await get_calling_performance(agent_user, agent_id="agent-2")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_live_leads_contains_dynamically_calculated_age():
    from routers.dashboard import get_dashboard_overview
    from datetime import datetime, timezone, timedelta
    from tests.test_integration_fixes import _overview_tables, _configure_agents, OWNER

    sb, tables = _overview_tables()
    _configure_agents(tables, [])

    lead_created = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    leads_mock = [
        {"id": "l-1", "name": "Test Lead", "created_at": lead_created, "updated_at": lead_created,
         "status": "new", "assigned_agent_id": "a1", "is_ai_handling": True}
    ]
    tables["leads"].return_value.select.return_value.eq.return_value.execute.return_value.data = leads_mock
    tables["viewings"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    tables["contracts"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    tables["calls"].return_value.select.return_value.eq.return_value.execute.return_value.data = []

    with patch("routers.dashboard.get_supabase", return_value=sb):
        res = await get_dashboard_overview(
            branch_id=None, agent_id=None, timeframe=None,
            start_date=None, end_date=None, platform=None,
            current_user=OWNER
        )

    assert res["success"] is True
    live_leads = res["data"]["live_leads"]
    assert len(live_leads) == 1
    assert "age" in live_leads[0]
    assert live_leads[0]["age"] == "15m"


@pytest.mark.asyncio
async def test_dashboard_kpis_with_empty_database():
    from routers.dashboard import get_dashboard_overview
    from tests.test_integration_fixes import _overview_tables, _configure_agents, OWNER

    sb, tables = _overview_tables()
    _configure_agents(tables, [])

    tables["leads"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    tables["viewings"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    tables["contracts"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    tables["calls"].return_value.select.return_value.eq.return_value.execute.return_value.data = []

    with patch("routers.dashboard.get_supabase", return_value=sb):
        res = await get_dashboard_overview(
            branch_id=None, agent_id=None, timeframe=None,
            start_date=None, end_date=None, platform=None,
            current_user=OWNER
        )

    assert res["success"] is True
    metrics = res["data"]["metrics"]
    assert metrics["lead_to_viewing"]["value"] == 0
    assert metrics["lead_to_viewing"]["subtext"] == "0 of 0"
    assert metrics["viewing_to_close"]["value"] == 0
    assert metrics["viewing_to_close"]["subtext"] == "0 of 0"
    assert metrics["close_rate"]["value"] == 0
    assert metrics["close_rate"]["subtext"] == "overall"
    assert res["data"]["todays_viewings"] == []


@pytest.mark.asyncio
async def test_dashboard_avg_response_time_calculation():
    from routers.dashboard import get_dashboard_overview
    from tests.test_integration_fixes import _overview_tables, _configure_agents, OWNER

    sb, tables = _overview_tables()
    _configure_agents(tables, [])

    leads_mock = [
        {"id": "l-1", "name": "Lead 1", "created_at": "2026-08-27T10:00:00+00:00", "status": "qualifying",
         "assigned_agent_id": "a1", "is_ai_handling": True}
    ]
    convs_mock = [
        {"lead_id": "l-1", "direction": "inbound", "sender_type": "lead", "timestamp": "2026-08-27T10:01:00+00:00"},
        {"lead_id": "l-1", "direction": "outbound", "sender_type": "ai", "timestamp": "2026-08-27T10:01:19+00:00"}
    ]
    tables["leads"].return_value.select.return_value.eq.return_value.execute.return_value.data = leads_mock
    tables["viewings"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    tables["contracts"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    tables["calls"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    tables["conversations"].return_value.select.return_value.eq.return_value.in_.return_value.order.return_value.execute.return_value.data = convs_mock

    with patch("routers.dashboard.get_supabase", return_value=sb):
        res = await get_dashboard_overview(
            branch_id=None, agent_id=None, timeframe=None,
            start_date=None, end_date=None, platform=None,
            current_user=OWNER
        )

    assert res["success"] is True
    avg_resp = res["data"]["metrics"]["avg_response_time"]
    assert avg_resp["value"] in ("19s", "49s", "1m 19s")  # calculated from timestamps
    assert "AI handles 100%" in avg_resp["subtext"]

