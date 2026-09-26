"""
Unit and integration tests for WhatsApp Inbound Auto-Create Lead feature.
Verifies that:
  1. Unknown incoming WhatsApp senders automatically create a new lead row.
  2. Sender name is extracted from Meta contacts profile (or fallback to 'WhatsApp Lead').
  3. property_ref is parsed if present in wa.me pre-filled text.
  4. Instant AI qualification is triggered.
  5. Invalid / dummy agency IDs fall back safely to DEFAULT_AGENCY_ID.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from config import settings
from routers.webhooks import whatsapp_inbound, _extract_property_ref_from_wame_body


class FakeRequest:
    def __init__(self, headers=None, json_body=None, form_data=None):
        self._headers = headers or {}
        self._json_body = json_body or {}
        self._form_data = form_data or {}

    @property
    def headers(self):
        return self._headers

    async def body(self):
        import json
        return json.dumps(self._json_body).encode("utf-8")

    async def json(self):
        return self._json_body

    async def form(self):
        return self._form_data


def test_extract_property_ref_from_wame_body():
    assert _extract_property_ref_from_wame_body("I am interested in REF-123456") == "REF-123456"
    assert _extract_property_ref_from_wame_body("Hello, listing REF 789012 please") == "REF-789012"
    assert _extract_property_ref_from_wame_body("Just saying hi") is None
    assert _extract_property_ref_from_wame_body("") is None


@pytest.mark.asyncio
async def test_meta_inbound_auto_creates_lead_for_unknown_number():
    """When an unlisted number messages, a lead should be auto-created and AI replied."""
    sb = MagicMock()
    
    # RPC lookup finds agency
    sb.rpc.return_value.execute.return_value.data = [
        {
            "agency_id": "test-agency-uuid",
            "agent_id": None,
            "comm_account_id": "comm-acc-uuid",
        }
    ]
    
    # Agency check finds agency exists
    def mock_table(table_name):
        mock_t = MagicMock()
        if table_name == "agencies":
            mock_t.select.return_value.eq.return_value.execute.return_value.data = [{"id": "test-agency-uuid"}]
        elif table_name == "leads":
            # Select returns empty (unknown sender)
            mock_t.select.return_value.ilike.return_value.execute.return_value.data = []
            # Insert returns newly created lead
            mock_t.insert.return_value.execute.return_value.data = [
                {
                    "id": "new-lead-uuid",
                    "name": "mdmominulshourav",
                    "phone": "+8801581087779",
                    "source": "whatsapp",
                    "status": "new",
                    "ai_stage": "greeting",
                    "is_ai_handling": True,
                    "agency_id": "test-agency-uuid",
                    "property_ref": "REF-554433",
                }
            ]
            mock_t.update.return_value.eq.return_value.execute.return_value = MagicMock()
        elif table_name == "conversations":
            mock_t.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
            mock_t.insert.return_value.execute.return_value = MagicMock()
        return mock_t

    sb.table.side_effect = mock_table

    meta_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba_id_123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15556712300",
                                "phone_number_id": "1279606448577557"
                            },
                            "contacts": [
                                {
                                    "profile": {
                                        "name": "mdmominulshourav"
                                    },
                                    "wa_id": "8801581087779"
                                }
                            ],
                            "messages": [
                                {
                                    "from": "8801581087779",
                                    "id": "wamid.test_unique_msg_1",
                                    "timestamp": "1727156000",
                                    "text": {
                                        "body": "Hi, I am interested in REF-554433"
                                    },
                                    "type": "text"
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    request = FakeRequest(json_body=meta_payload)

    with patch("routers.webhooks.get_supabase", return_value=sb), \
         patch.object(settings, "WHATSAPP_PROVIDER", "meta"), \
         patch("routers.webhooks._verify_meta_request", return_value=True), \
         patch("routers.webhooks._check_and_mark_message_id", return_value=True), \
         patch("routers.webhooks.drain_outbound_queue_for_lead", new_callable=AsyncMock, return_value=[]), \
         patch("routers.webhooks.detect_handover", new_callable=AsyncMock, return_value={"needs_handover": False}), \
         patch("routers.webhooks.check_and_consume_whatsapp_quota", new_callable=AsyncMock, return_value=True), \
         patch("routers.webhooks.qualify_and_respond", new_callable=AsyncMock, return_value="Hello! I would love to help you with REF-554433.") as mock_ai, \
         patch("routers.webhooks.extract_lead_qualifications", new_callable=AsyncMock, return_value={}), \
         patch("routers.webhooks.send_whatsapp_smart", new_callable=AsyncMock, return_value={"status": "sent", "sid": "wamid.reply_1"}) as mock_smart_send:

        result = await whatsapp_inbound(request)

    assert result["success"] is True
    # Verify AI was triggered
    mock_ai.assert_called_once()
    # Verify smart send was called with the new lead ID
    mock_smart_send.assert_called_once()
    args, kwargs = mock_smart_send.call_args
    assert args[0] == "test-agency-uuid"
    assert args[1] == "new-lead-uuid"
    assert args[2] == "8801581087779"
    assert "Hello! I would love to help you with REF-554433." in args[3]


@pytest.mark.asyncio
async def test_meta_inbound_dummy_agency_fallback():
    """When RPC returns a dummy agency (e.g. 11111111-...), fallback to DEFAULT_AGENCY_ID occurs."""
    sb = MagicMock()
    
    # RPC lookup finds dummy agency 11111111-1111-1111-1111-111111111111
    sb.rpc.return_value.execute.return_value.data = [
        {
            "agency_id": "11111111-1111-1111-1111-111111111111",
            "agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "comm_account_id": "comm-acc-uuid",
        }
    ]
    
    # Agency check: 1111... does not exist, but DEFAULT_AGENCY_ID does exist
    def mock_table(table_name):
        mock_t = MagicMock()
        if table_name == "agencies":
            def mock_eq(col, val):
                eq_mock = MagicMock()
                if val == "11111111-1111-1111-1111-111111111111":
                    eq_mock.execute.return_value.data = []  # Not found
                else:
                    eq_mock.execute.return_value.data = [{"id": val}]  # Found
                return eq_mock
            mock_t.select.return_value.eq.side_effect = mock_eq
        elif table_name == "agents":
            mock_t.select.return_value.eq.return_value.execute.return_value.data = []
        elif table_name == "leads":
            mock_t.select.return_value.ilike.return_value.execute.return_value.data = []
            mock_t.insert.return_value.execute.return_value.data = [
                {
                    "id": "new-lead-uuid-2",
                    "name": "mdmominulshourav",
                    "phone": "+8801581087779",
                    "source": "whatsapp",
                    "status": "new",
                    "ai_stage": "greeting",
                    "is_ai_handling": True,
                    "agency_id": "default-fallback-agency",
                }
            ]
            mock_t.update.return_value.eq.return_value.execute.return_value = MagicMock()
        elif table_name == "conversations":
            mock_t.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
            mock_t.insert.return_value.execute.return_value = MagicMock()
        return mock_t

    sb.table.side_effect = mock_table

    meta_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba_id_123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15556712300",
                                "phone_number_id": "1279606448577557"
                            },
                            "contacts": [
                                {
                                    "profile": {
                                        "name": "mdmominulshourav"
                                    },
                                    "wa_id": "8801581087779"
                                }
                            ],
                            "messages": [
                                {
                                    "from": "8801581087779",
                                    "id": "wamid.test_unique_msg_2",
                                    "timestamp": "1727156001",
                                    "text": {
                                        "body": "Hello there"
                                    },
                                    "type": "text"
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    request = FakeRequest(json_body=meta_payload)

    with patch("routers.webhooks.get_supabase", return_value=sb), \
         patch.object(settings, "WHATSAPP_PROVIDER", "meta"), \
         patch.object(settings, "DEFAULT_AGENCY_ID", "default-fallback-agency"), \
         patch("routers.webhooks._verify_meta_request", return_value=True), \
         patch("routers.webhooks._check_and_mark_message_id", return_value=True), \
         patch("routers.webhooks.drain_outbound_queue_for_lead", new_callable=AsyncMock, return_value=[]), \
         patch("routers.webhooks.detect_handover", new_callable=AsyncMock, return_value={"needs_handover": False}), \
         patch("routers.webhooks.check_and_consume_whatsapp_quota", new_callable=AsyncMock, return_value=True), \
         patch("routers.webhooks.qualify_and_respond", new_callable=AsyncMock, return_value="Hello! Welcome to our agency.") as mock_ai, \
         patch("routers.webhooks.extract_lead_qualifications", new_callable=AsyncMock, return_value={}), \
         patch("routers.webhooks.send_whatsapp_smart", new_callable=AsyncMock, return_value={"status": "sent", "sid": "wamid.reply_2"}) as mock_smart_send:

        result = await whatsapp_inbound(request)

    assert result["success"] is True
    mock_ai.assert_called_once()
    mock_smart_send.assert_called_once()
    args, _ = mock_smart_send.call_args
    # Confirms it fell back to default-fallback-agency instead of the dummy 1111...
    assert args[0] == "default-fallback-agency"
    assert args[1] == "new-lead-uuid-2"

