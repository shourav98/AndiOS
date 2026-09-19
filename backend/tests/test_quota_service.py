"""
Unit and integration tests for Quota Service and Shared Gateway Webhook Routing.
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi import Request
from services.quota_service import (
    check_and_consume_whatsapp_quota,
    refund_whatsapp_quota,
    check_and_consume_voice_quota,
    refund_voice_quota,
    freeze_agency,
    unfreeze_agency,
    reset_all_quotas,
    get_agency_quota_status,
)
from config import settings


# ─── 1. Quota Service Tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_and_consume_whatsapp_quota_allowed():
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value.data = True

    with patch("services.quota_service.get_supabase", return_value=sb), \
         patch.object(settings, "QUOTA_ENFORCEMENT_ENABLED", True):
        allowed = await check_and_consume_whatsapp_quota("agency-123")

    assert allowed is True
    sb.rpc.assert_called_once_with(
        "check_and_increment_whatsapp_quota",
        {"agency_uuid": "agency-123"}
    )


@pytest.mark.asyncio
async def test_check_and_consume_whatsapp_quota_blocked():
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value.data = False

    with patch("services.quota_service.get_supabase", return_value=sb), \
         patch.object(settings, "QUOTA_ENFORCEMENT_ENABLED", True):
        allowed = await check_and_consume_whatsapp_quota("agency-123")

    assert allowed is False


@pytest.mark.asyncio
async def test_check_and_consume_whatsapp_quota_disabled():
    with patch.object(settings, "QUOTA_ENFORCEMENT_ENABLED", False):
        allowed = await check_and_consume_whatsapp_quota("agency-123")

    assert allowed is True


@pytest.mark.asyncio
async def test_refund_whatsapp_quota_calls_rpc():
    sb = MagicMock()

    with patch("services.quota_service.get_supabase", return_value=sb), \
         patch.object(settings, "QUOTA_ENFORCEMENT_ENABLED", True):
        await refund_whatsapp_quota("agency-123")

    sb.rpc.assert_called_once_with(
        "decrement_whatsapp_used",
        {"agency_uuid": "agency-123"}
    )


@pytest.mark.asyncio
async def test_check_and_consume_voice_quota_allowed_and_blocked():
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value.data = True

    with patch("services.quota_service.get_supabase", return_value=sb), \
         patch.object(settings, "QUOTA_ENFORCEMENT_ENABLED", True):
        allowed = await check_and_consume_voice_quota("agency-123")
    assert allowed is True

    sb.rpc.return_value.execute.return_value.data = False
    with patch("services.quota_service.get_supabase", return_value=sb), \
         patch.object(settings, "QUOTA_ENFORCEMENT_ENABLED", True):
        blocked = await check_and_consume_voice_quota("agency-123")
    assert blocked is False


@pytest.mark.asyncio
async def test_refund_voice_quota_calls_rpc():
    sb = MagicMock()

    with patch("services.quota_service.get_supabase", return_value=sb), \
         patch.object(settings, "QUOTA_ENFORCEMENT_ENABLED", True):
        await refund_voice_quota("agency-123")

    sb.rpc.assert_called_once_with(
        "decrement_voice_used",
        {"agency_uuid": "agency-123"}
    )


@pytest.mark.asyncio
async def test_freeze_and_unfreeze_agency():
    sb = MagicMock()

    with patch("services.quota_service.get_supabase", return_value=sb):
        await freeze_agency("agency-123", reason="testing")
        sb.table("agencies").update.assert_called_with({"is_quota_frozen": True})

        await unfreeze_agency("agency-123")
        sb.table("agencies").update.assert_called_with({"is_quota_frozen": False})


@pytest.mark.asyncio
async def test_reset_all_quotas():
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value.data = 5

    with patch("services.quota_service.get_supabase", return_value=sb):
        count = await reset_all_quotas()
        assert count == 5
        sb.rpc.assert_called_with("reset_monthly_quotas", {})

        count_single = await reset_all_quotas(["agency-1"])
        assert count_single == 5
        sb.rpc.assert_called_with("reset_monthly_quotas", {"target_agency_ids": ["agency-1"]})


def test_get_agency_quota_status():
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": "agency-123",
        "dedicated_whatsapp_number": "+14155238886",
        "dedicated_voice_number": "+14155238887",
        "whatsapp_number_status": "active",
        "whatsapp_monthly_limit": 1000,
        "whatsapp_monthly_used": 850,
        "voice_monthly_limit": 500,
        "voice_monthly_used": 500,
        "is_quota_frozen": False,
        "quota_reset_at": "2026-09-01T00:00:00Z",
    }

    with patch("services.quota_service.get_supabase", return_value=sb):
        status = get_agency_quota_status("agency-123")

    assert status is not None
    assert status["whatsapp"]["used"] == 850
    assert status["whatsapp"]["remaining"] == 150
    assert status["whatsapp"]["percent_used"] == 85.0

    assert status["voice"]["used"] == 500
    assert status["voice"]["remaining"] == 0
    assert status["voice"]["percent_used"] == 100.0

    assert status["whatsapp_number_status"] == "active"
    assert status["is_quota_frozen"] is False
    assert status["is_frozen_display"] is False


# ─── 2. Inbound Webhook Shared Gateway Tests ─────────────────────────────────

class FakeRequest:
    def __init__(self, headers=None, form_data=None):
        self._headers = headers or {}
        self._form_data = form_data or {}
        self.url = "http://testserver/webhooks/whatsapp"

    @property
    def headers(self):
        return self._headers

    async def form(self):
        return self._form_data


@pytest.mark.asyncio
async def test_webhook_unknown_to_number_graceful_skip():
    """Test Case 4: Unknown To number -> graceful skip, no crash."""
    from routers.webhooks import whatsapp_inbound

    sb = MagicMock()
    # Agency lookup for To number yields no data
    sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = None

    request = FakeRequest(
        form_data={
            "From": "whatsapp:+971500000001",
            "To": "whatsapp:+14150009999",  # unknown number
            "Body": "Hello there",
            "SmsMessageSid": "SM_UNKNOWN",
        }
    )

    with patch("routers.webhooks.get_supabase", return_value=sb), \
         patch.object(settings, "WHATSAPP_PROVIDER", "twilio"), \
         patch.object(settings, "APP_ENV", "development"):
        resp = await whatsapp_inbound(request)

    assert resp["success"] is True
    # Verify no lead lookup was performed because agency resolution failed
    sb.table.return_value.select.return_value.ilike.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_quota_exceeded_suppresses_ai_reply():
    """Test Case: Quota exceeded -> saves lead message, suppresses AI reply."""
    from routers.webhooks import whatsapp_inbound

    sb = MagicMock()
    # 1. Agency lookup
    sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": "agency-123",
        "whatsapp_number_status": "active",
    }
    # 2. Lead lookup
    sb.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [
        {"id": "lead-1", "agency_id": "agency-123", "phone": "+971500000001", "name": "Test Lead"}
    ]
    sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []

    request = FakeRequest(
        form_data={
            "From": "whatsapp:+971500000001",
            "To": "whatsapp:+14155238886",
            "Body": "Can I book a viewing?",
            "SmsMessageSid": "SM123",
        }
    )

    with patch("routers.webhooks.get_supabase", return_value=sb), \
         patch.object(settings, "WHATSAPP_PROVIDER", "twilio"), \
         patch.object(settings, "APP_ENV", "development"), \
         patch("routers.webhooks._check_and_mark_message_id", return_value=True), \
         patch("routers.webhooks.check_and_consume_whatsapp_quota", return_value=False) as mock_quota, \
         patch("routers.webhooks.qualify_and_respond", new_callable=AsyncMock) as mock_ai, \
         patch("routers.webhooks.send_whatsapp_for_agency", new_callable=AsyncMock) as mock_send:

        resp = await whatsapp_inbound(request)

    assert resp["success"] is True
    mock_quota.assert_called_once_with("agency-123")
    # AI response and send are suppressed
    mock_ai.assert_not_called()
    mock_send.assert_not_called()
    # Inbound message is still saved to conversations
    sb.table.return_value.insert.assert_called()


@pytest.mark.asyncio
async def test_webhook_ai_failure_refunds_quota():
    """Test Case: AI failure after quota consumption refunds quota."""
    from routers.webhooks import whatsapp_inbound

    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": "agency-123",
        "whatsapp_number_status": "active",
    }
    sb.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [
        {"id": "lead-1", "agency_id": "agency-123", "phone": "+971500000001", "name": "Test Lead"}
    ]
    sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []

    request = FakeRequest(
        form_data={
            "From": "whatsapp:+971500000001",
            "To": "whatsapp:+14155238886",
            "Body": "I want to rent",
            "SmsMessageSid": "SM123",
        }
    )

    with patch("routers.webhooks.get_supabase", return_value=sb), \
         patch.object(settings, "WHATSAPP_PROVIDER", "twilio"), \
         patch.object(settings, "APP_ENV", "development"), \
         patch("routers.webhooks._check_and_mark_message_id", return_value=True), \
         patch("routers.webhooks.check_and_consume_whatsapp_quota", return_value=True) as mock_quota, \
         patch("routers.webhooks.refund_whatsapp_quota", new_callable=AsyncMock) as mock_refund, \
         patch("routers.webhooks.qualify_and_respond", side_effect=RuntimeError("OpenAI timeout")) as mock_ai:

        resp = await whatsapp_inbound(request)

    assert resp["success"] is True
    mock_quota.assert_called_once_with("agency-123")
    mock_refund.assert_called_once_with("agency-123")
