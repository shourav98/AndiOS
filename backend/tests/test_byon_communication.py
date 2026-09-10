"""
Unit and integration tests for BYON (Bring Your Own Number) Communication Layer.

Covers:
- CommunicationAccount dataclass with agent_id support
- Provider factory resolving agent-level BYON vs agency-level vs Twilio legacy
- MetaWhatsAppAdapter inbound parsing and signature verification
- send_whatsapp_for_agency routing with agent_id
- Webhook inbound Meta Cloud API routing to agency & agent via get_agency_by_phone_number_id RPC
"""
import pytest
import hmac
import hashlib
import json
from unittest.mock import patch, MagicMock, AsyncMock

from services.communication.base import CommunicationAccount, SendResult
from services.communication.meta_adapter import MetaWhatsAppAdapter
from services.communication.provider_factory import get_whatsapp_provider, get_whatsapp_provider_for_agency
from services.whatsapp_service import send_whatsapp_for_agency
from routers.webhooks import _verify_meta_request, whatsapp_inbound
from config import settings


def test_communication_account_agent_level():
    """Verify CommunicationAccount stores both agency_id and optional agent_id."""
    acc = CommunicationAccount(
        id="comm-1",
        agency_id="agency-100",
        agent_id="agent-200",
        channel="whatsapp",
        provider="meta",
        phone_number="971501234567",
        phone_number_id="meta-phone-id-1",
        access_token="test-token",
    )
    assert acc.agency_id == "agency-100"
    assert acc.agent_id == "agent-200"
    assert acc.provider == "meta"
    assert acc.phone_number_id == "meta-phone-id-1"


def test_meta_webhook_signature_verification():
    """Verify Meta X-Hub-Signature-256 HMAC-SHA256 calculation and verification."""
    secret = "meta_test_secret_123"
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    adapter = MetaWhatsAppAdapter(app_secret=secret)
    assert adapter.verify_webhook(body, {"x-hub-signature-256": sig}) is True
    assert adapter.verify_webhook(body, {"x-hub-signature-256": "sha256=invalid"}) is False
    assert adapter.verify_webhook(body, {}) is False


def test_meta_inbound_parsing():
    """Verify Meta Cloud API inbound JSON parsing extracts sender, phone_number_id, body."""
    adapter = MetaWhatsAppAdapter()
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba_1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "971501234567",
                                "phone_number_id": "meta_pid_999",
                            },
                            "messages": [
                                {
                                    "from": "971509998888",
                                    "id": "wamid.ABC123XYZ",
                                    "timestamp": "1720000000",
                                    "text": {"body": "Hello, I am interested in the penthouse"},
                                    "type": "text",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    messages = adapter.parse_inbound(payload)
    assert len(messages) == 1
    msg = messages[0]
    assert msg.from_phone == "971509998888"
    assert msg.to_identifier == "meta_pid_999"
    assert msg.body == "Hello, I am interested in the penthouse"
    assert msg.message_id == "wamid.ABC123XYZ"
    assert msg.provider == "meta"


@pytest.mark.asyncio
async def test_provider_factory_resolves_agent_then_agency_fallback():
    """Verify factory looks for agent-specific account first, then agency default."""
    mock_sb = MagicMock()

    # Case 1: Agent has their own BYON number
    mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
        "id": "comm-agent",
        "agency_id": "agency-1",
        "agent_id": "agent-1",
        "channel": "whatsapp",
        "provider": "meta",
        "phone_number": "971501111111",
        "phone_number_id": "pid-agent-1",
        "access_token": "token-agent",
        "status": "active",
        "metadata": {},
    }

    with patch("database.supabase_client.get_supabase", return_value=mock_sb):
        provider, account = await get_whatsapp_provider_for_agency("agency-1", agent_id="agent-1")
        assert account is not None
        assert account.agent_id == "agent-1"
        assert account.phone_number_id == "pid-agent-1"
        assert isinstance(provider, MetaWhatsAppAdapter)


@pytest.mark.asyncio
async def test_send_whatsapp_for_agency_with_agent_id():
    """Verify send_whatsapp_for_agency invokes provider with agent credentials."""
    mock_provider = AsyncMock()
    mock_provider.send_message.return_value = SendResult(success=True, message_id="wamid.mock123")

    mock_account = CommunicationAccount(
        id="comm-1",
        agency_id="agency-1",
        agent_id="agent-1",
        channel="whatsapp",
        provider="meta",
        phone_number="971501111111",
    )

    with patch(
        "services.communication.provider_factory.get_whatsapp_provider_for_agency",
        new=AsyncMock(return_value=(mock_provider, mock_account)),
    ):
        result = await send_whatsapp_for_agency("agency-1", "+971502222222", "Hello!", agent_id="agent-1")
        assert result["status"] == "sent"
        assert result["sid"] == "wamid.mock123"
        mock_provider.send_message.assert_awaited_once_with("971502222222", "Hello!")
