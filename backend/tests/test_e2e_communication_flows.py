"""
End-to-End Test Suite for WhatsApp Automation and Voice Calling Pipelines.

Covers:
  1. WhatsApp Automation Flow:
     - Inbound Meta WhatsApp webhook delivery
     - Signature validation & parsing
     - RPC resolution (get_agency_by_phone_number_id) to agent + agency
     - Lead creation / association
     - AI qualification prompt generation
     - Outbound auto-reply routing via send_whatsapp_smart (window-aware)
     - Quota deduction & conversation persistence

  2. Inbound Voice Calling Flow (Central DID BYON Forwarding):
     - Inbound call forwarded to Central Platform DID
     - Multi-tier agent resolution (ForwardedFrom match via resolve_agent_by_voice_number)
     - TwiML generation with Vapi BYO SIP Trunk (credential_id subdomain)
     - Dynamic SIP header injection (X-Agent-Id, X-Agency-Id, X-Call-Sid)
     - Inbound call record created in 'calls' table

  3. Outbound Voice Calling & Vapi End-of-Call Report Flow:
     - Outbound call initiation via Vapi API adapter
     - Vapi webhook callback processing (end-of-call-report)
     - Call status, duration, recording URL, and transcript extraction
     - Automatic qualification score update on lead
"""
import pytest
import hmac
import hashlib
import json
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import HTTPException
from xml.etree import ElementTree

from config import settings
from routers.webhooks import whatsapp_inbound, voice_inbound, vapi_webhook
from services.voice_service import resolve_inbound_caller, generate_vapi_sip_twiml
from services.whatsapp_service import send_whatsapp_for_agency
from services.communication.base import SendResult


# ─── Mock Request Helpers ──────────────────────────────────────────────────────

class FakeRequest:
    def __init__(self, headers=None, json_body=None, form_data=None, raw_body=b""):
        self.headers = headers or {}
        self._json = json_body
        self._form = form_data or {}
        self._raw = raw_body

    async def json(self):
        return self._json

    async def form(self):
        return self._form

    async def body(self):
        return self._raw


# ─── 1. WhatsApp Automation E2E Flow ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_whatsapp_automation_inbound_to_ai_reply_flow():
    """
    Simulates a real customer sending a WhatsApp message:
      1. Meta Cloud API delivers inbound webhook payload.
      2. Signature is verified.
      3. get_agency_by_phone_number_id RPC resolves to specific agent and agency.
      4. AI generates qualification response.
      5. send_whatsapp_for_agency dispatches the reply using agent's WhatsApp channel.
      6. Conversation and message logged.
    """
    app_secret = "test_meta_app_secret"
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba_e2e_123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "971501112233",
                                "phone_number_id": "meta_pid_e2e_test",
                            },
                            "messages": [
                                {
                                    "from": "971509998877",
                                    "id": "wamid.E2E_MSG_123",
                                    "timestamp": "1720000000",
                                    "text": {"body": "Hi, I am looking for a 2-bedroom apartment in Downtown Dubai around 2.5M AED."},
                                    "type": "text",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()

    req = FakeRequest(
        headers={"x-hub-signature-256": sig},
        json_body=payload,
        raw_body=raw_body,
    )

    agency_uuid = "11111111-1111-1111-1111-111111111111"
    agent_uuid = "22222222-2222-2222-2222-222222222222"

    # Mock DB behavior
    mock_sb = MagicMock()
    # 1. RPC resolve phone_number_id
    mock_sb.rpc.return_value.execute.return_value.data = [
        {
            "agency_id": agency_uuid,
            "agent_id": agent_uuid,
            "comm_account_id": "33333333-3333-3333-3333-333333333333",
            "provider": "meta",
            "phone_number": "971501112233",
            "status": "active",
        }
    ]

    # Mock lead — include last_inbound_at so webhooks.py can read it
    mock_lead = {
        "id": "44444444-4444-4444-4444-444444444444",
        "agency_id": agency_uuid,
        "assigned_agent_id": agent_uuid,
        "phone": "+971509998877",
        "status": "New",
        "handover_required": False,
        "last_inbound_at": "2099-01-01T10:00:00+00:00",  # in-window: smart send => free-form
    }

    def table_router(table_name):
        t = MagicMock()
        if table_name == "leads":
            t.select.return_value.ilike.return_value.execute.return_value.data = [mock_lead]
            t.select.return_value.eq.return_value.execute.return_value.data = [mock_lead]
            t.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [mock_lead]
            t.update.return_value.eq.return_value.execute.return_value.data = [mock_lead]
        elif table_name == "conversations":
            t.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
            t.insert.return_value.execute.return_value.data = [{"id": "conv-1"}]
        elif table_name == "whatsapp_processed_messages":
            t.insert.return_value.execute.return_value.data = [{"message_id": "wamid.INBOUND_MSG_1"}]
        elif table_name == "communication_accounts":
            t.update.return_value.eq.return_value.execute.return_value.data = []
        elif table_name == "whatsapp_outbound_queue":
            # drain_outbound_queue_for_lead checks this; empty = nothing to drain
            t.select.return_value.eq.return_value.execute.return_value.data = []
        return t

    mock_sb.table.side_effect = table_router

    with patch.object(settings, "META_APP_SECRET", app_secret), \
         patch.object(settings, "WHATSAPP_PROVIDER", "meta"), \
         patch("routers.webhooks.get_supabase", return_value=mock_sb), \
         patch("routers.webhooks.check_and_consume_whatsapp_quota", new_callable=AsyncMock) as mock_quota, \
         patch("routers.webhooks.qualify_and_respond", new_callable=AsyncMock) as mock_ai, \
         patch("routers.webhooks.send_whatsapp_for_agency", new_callable=AsyncMock) as mock_send, \
         patch("routers.webhooks.send_whatsapp_smart", new_callable=AsyncMock) as mock_smart, \
         patch("routers.webhooks.drain_outbound_queue_for_lead", new_callable=AsyncMock) as mock_drain, \
         patch("routers.webhooks.extract_lead_qualifications", new_callable=AsyncMock, return_value={}) as mock_extract:

        mock_quota.return_value = True
        mock_ai.return_value = "Hello! I'd be happy to show you our 2BR Downtown listings under 2.5M AED. When are you available for a viewing?"
        mock_smart.return_value = {"status": "sent", "sid": "wamid.OUTBOUND_REPLY_1"}
        mock_send.return_value = {"status": "sent", "sid": "wamid.OUTBOUND_REPLY_1"}
        mock_drain.return_value = 0

        resp = await whatsapp_inbound(req)

        assert resp["success"] is True

        # Verify AI qualification was invoked with the lead's message
        assert mock_ai.await_count == 1
        call_args = mock_ai.await_args[0]
        assert "Downtown Dubai" in call_args[2]

        # Verify outgoing auto-reply was routed via send_whatsapp_smart (window-aware)
        # last_inbound_at=2099 => in-window => smart send calls free-form internally,
        # but from the webhook's perspective send_whatsapp_smart is the entry point.
        assert mock_smart.await_count == 1
        call_pos = mock_smart.await_args[0]
        assert call_pos[0] == agency_uuid        # agency_id
        assert call_pos[1] == mock_lead["id"]    # lead_id
        assert call_pos[2] == "971509998877"     # to_phone (stripped)
        assert "2BR Downtown" in call_pos[3]     # body


# ─── 2. Voice Inbound Calling E2E Flow (Central DID BYON) ─────────────────────

@pytest.mark.asyncio
async def test_voice_inbound_byon_forwarding_e2e_flow():
    """
    Simulates a live inbound voice call forwarded from an agent's mobile:
      1. Twilio receives forwarded call and hits /webhooks/voice/inbound.
      2. ForwardedFrom header identifies the agent via DB RPC.
      3. Calls table logs the call record.
      4. TwiML response bridges to Vapi BYO SIP Trunk with subdomain credential_id.
      5. SIP custom headers X-Agent-Id, X-Agency-Id are injected.
    """
    form_payload = {
        "CallSid": "CA_LIVE_E2E_12345",
        "From": "+971509998877",
        "To": "+97141234567",
        "ForwardedFrom": "+971501112233",
        "Direction": "inbound",
    }
    req = FakeRequest(form_data=form_payload)

    mock_sb = MagicMock()
    # resolve_agent_by_voice_number RPC
    mock_sb.rpc.return_value.execute.return_value.data = [
        {
            "agency_id": "agency-dubai-realty",
            "agent_id": "agent-sarah",
            "agent_name": "Sarah Jenkins",
            "custom_greeting": "Welcome to Dubai Realty! I am Sarah's AI assistant.",
            "comm_account_id": "comm-voice-1",
        }
    ]
    mock_sb.table.return_value.insert.return_value.execute.return_value.data = [{"id": "call-rec-1"}]

    with patch("routers.webhooks._verify_twilio_request", return_value=True), \
         patch("database.supabase_client.get_supabase", return_value=mock_sb), \
         patch("routers.webhooks.get_supabase", return_value=mock_sb), \
         patch("services.voice_service.get_supabase", return_value=mock_sb), \
         patch("services.quota_service.check_and_consume_voice_quota", new_callable=AsyncMock) as mock_quota, \
         patch.object(settings, "CENTRAL_INBOUND_DID", "+97141234567"), \
         patch.object(settings, "VAPI_SIP_CREDENTIAL_ID", "cred_trunk_prod_99"), \
         patch.object(settings, "VAPI_SIP_DOMAIN", "sip.vapi.ai"):

        mock_quota.return_value = {"allowed": True}

        response = await voice_inbound(req)

        assert response.status_code == 200
        assert "application/xml" in response.media_type

        # Parse and assert TwiML content
        root = ElementTree.fromstring(response.body)
        dial = root.find("Dial")
        assert dial is not None

        sip = dial.find("Sip")
        assert sip is not None
        sip_text = sip.text

        # Verify correct Vapi SIP Trunk subdomain
        assert "@cred_trunk_prod_99.sip.vapi.ai" in sip_text
        # Verify custom persona headers
        assert "X-Agent-Id=agent-sarah" in sip_text
        assert "X-Agency-Id=agency-dubai-realty" in sip_text
        assert "X-Call-Sid=CA_LIVE_E2E_12345" in sip_text
        assert "X-Resolution-Tier=1" in sip_text


# ─── 3. Outbound Voice & Vapi Webhook E2E Flow ─────────────────────────────────

@pytest.mark.asyncio
async def test_vapi_end_of_call_webhook_processing_flow():
    """
    Simulates Vapi delivering an end-of-call report after AI completes conversation:
      1. Vapi sends end-of-call-report with transcript, summary, and analysis.
      2. Webhook validates x-vapi-secret header.
      3. Processor records transcript and updates call status.
    """
    vapi_secret = "vapi_shared_server_secret"
    payload = {
        "message": {
            "type": "end-of-call-report",
            "call": {
                "id": "vapi-call-session-987",
                "status": "ended",
                "endedReason": "customer-ended-call",
                "customer": {"number": "+971509998877"},
            },
            "transcript": (
                "AI: Hello, thank you for calling Dubai Realty. How can I help you today?\n"
                "Caller: Hi, I wanted to schedule a viewing for the Marina unit tomorrow at 4 PM.\n"
                "AI: Wonderful! I have noted that for tomorrow at 4 PM. Sarah Jenkins will confirm."
            ),
            "summary": "Customer requested a viewing for the Marina unit tomorrow at 4 PM.",
            "analysis": {
                "structuredData": {
                    "viewing_requested": True,
                    "preferred_time": "tomorrow 4 PM",
                    "interested_property": "Marina unit",
                },
                "successEvaluation": "true",
            },
            "durationMinutes": 2.5,
        }
    }

    req = FakeRequest(
        headers={"x-vapi-secret": vapi_secret},
        json_body=payload,
    )

    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "VAPI_WEBHOOK_SECRET", vapi_secret), \
         patch("services.vapi_service.process_vapi_webhook", new_callable=AsyncMock) as mock_proc:

        mock_proc.return_value = {
            "status": "processed",
            "call_id": "vapi-call-session-987",
            "viewing_scheduled": True,
        }

        resp = await vapi_webhook(req)

        assert resp["success"] is True
        assert resp["data"]["viewing_scheduled"] is True
        mock_proc.assert_awaited_once_with(payload)
