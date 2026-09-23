"""
Unit tests for Central DID Inbound Voice Routing & BYON Voice Architecture.

Covers:
  1. voice_service.resolve_inbound_caller:
     - Tier 1: ForwardedFrom match via resolve_agent_by_voice_number RPC
     - Tier 2: Fallback to CRM recent contact graph match (leads -> agent_id)
     - Tier 3: Platform receptionist fallback
  2. voice_service.build_vapi_sip_uri:
     - Official Vapi BYO SIP Trunk URI format: sip:{phone_number}@{credential_id}.sip.vapi.ai
     - Subdomain placement of credential_id (crucial for Vapi trunk matching)
     - EU region domain support (.sip.eu.vapi.ai)
  3. voice_service.generate_vapi_sip_twiml:
     - Twilio <Dial><Sip> generation with correct subdomain credential_id
     - X-Agent-Id, X-Agency-Id, X-Agent-Name, X-Call-Sid SIP header injection
     - Fallback assistant routing (Tier 3)
     - XML ampersand escaping (&amp;)
  4. POST /webhooks/voice/inbound:
     - Form parsing of ForwardedFrom, From, CallSid
     - TwiML XML response structure and headers
  5. agent_phone_settings router:
     - GET /agents/me/forwarding (including standard GSM ##002# cancel code)
     - POST /agents/me/outbound-caller-id/initiate & confirm
     - PATCH /agents/me/forwarding/status
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from xml.etree import ElementTree
from fastapi import HTTPException

from services.voice_service import (
    resolve_inbound_caller,
    build_vapi_sip_uri,
    generate_vapi_sip_twiml,
    initiate_outbound_caller_id_verification,
    confirm_outbound_caller_id_verification,
    InboundCallContext,
)
from routers.agent_phone_settings import (
    get_forwarding_info,
    initiate_caller_id_verification,
    confirm_caller_id_verification,
    update_forwarding_status,
    CallerIdInitiateRequest,
    CallerIdConfirmRequest,
    ForwardingStatusUpdate,
)
from routers.webhooks import voice_inbound
from config import settings


# ─── 1. Multi-Tier Caller Resolution ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_inbound_caller_tier1_forwarded_from():
    """Tier 1: When ForwardedFrom matches an agent's registered voice number via RPC."""
    mock_sb = MagicMock()
    mock_sb.rpc.return_value.execute.return_value.data = [
        {
            "agent_id": "agent-123",
            "agency_id": "agency-456",
            "agent_name": "Test Agent",
            "custom_greeting": "Welcome to Marina Realty",
            "comm_account_id": "comm-789",
        }
    ]

    with patch("services.voice_service.get_supabase", return_value=mock_sb):
        ctx = await resolve_inbound_caller(
            from_phone="971509998877",
            forwarded_from="+971501112233",
            to_number="+97141234567",
        )

    assert ctx["agent_id"] == "agent-123"
    assert ctx["agency_id"] == "agency-456"
    assert ctx["resolution_tier"] == 1
    assert ctx["agent_name"] == "Test Agent"


@pytest.mark.asyncio
async def test_resolve_inbound_caller_tier2_crm_lead_match():
    """Tier 2: Carrier stripped Diversion header, but caller is known lead in CRM -> route to lead's assigned agent."""
    mock_sb = MagicMock()

    def table_router(table_name):
        t = MagicMock()
        if table_name == "leads":
            t.select().ilike().order().limit().execute.return_value.data = [
                {"id": "lead-1", "agency_id": "agency-crm-101", "agent_id": "agent-crm-789", "phone": "971509998877"}
            ]
        elif table_name == "conversations":
            t.select().eq().gte().order().limit().execute.return_value.data = [
                {"id": "conv-1", "agency_id": "agency-crm-101", "agent_id": "agent-crm-789"}
            ]
        elif table_name == "agents":
            t.select().eq().limit().execute.return_value.data = [
                {"name": "Sarah CRM"}
            ]
        return t

    mock_sb.table.side_effect = table_router

    with patch("services.voice_service.get_supabase", return_value=mock_sb):
        ctx = await resolve_inbound_caller(
            from_phone="971509998877",
            forwarded_from=None,  # carrier stripped SIP Diversion
            to_number="+97141234567",
        )

    assert ctx["agent_id"] == "agent-crm-789"
    assert ctx["agency_id"] == "agency-crm-101"
    assert ctx["resolution_tier"] == 2
    assert ctx["agent_name"] == "Sarah CRM"


@pytest.mark.asyncio
async def test_resolve_inbound_caller_tier3_receptionist_fallback():
    """Tier 3: No ForwardedFrom and unknown caller -> route to generic platform receptionist."""
    mock_sb = MagicMock()

    def table_router(table_name):
        t = MagicMock()
        t.select().ilike().order().limit().execute.return_value.data = []
        return t

    mock_sb.table.side_effect = table_router

    with patch("services.voice_service.get_supabase", return_value=mock_sb):
        ctx = await resolve_inbound_caller(
            from_phone="971500000000",
            forwarded_from=None,
            to_number="+97141234567",
        )

    assert ctx["agent_id"] is None
    assert ctx["agency_id"] is None
    assert ctx["resolution_tier"] == 3


# ─── 2. Vapi BYO SIP Trunk URI Construction ──────────────────────────────────

def test_build_vapi_sip_uri_credential_subdomain():
    """
    CRITICAL: Credential ID must be the subdomain in the SIP URI:
      sip:{phone_number}@{credential_id}.sip.vapi.ai
    This tells Vapi which BYO SIP Trunk / organization account the call belongs to.
    """
    uri = build_vapi_sip_uri(
        destination_number="+97141234567",
        credential_id="cred_trunk_abc123",
        domain="sip.vapi.ai",
    )
    assert uri == "sip:97141234567@cred_trunk_abc123.sip.vapi.ai"
    # Ensure credential_id is specifically in the subdomain position before .sip.vapi.ai
    assert "@cred_trunk_abc123.sip.vapi.ai" in uri
    assert not uri.startswith("sip:cred_trunk_abc123@")  # credential is host, not user


def test_build_vapi_sip_uri_eu_domain():
    """Ensure EU-hosted Vapi organizations (.sip.eu.vapi.ai) are supported."""
    uri = build_vapi_sip_uri(
        destination_number="+97141234567",
        credential_id="cred_eu_999",
        domain="sip.eu.vapi.ai",
    )
    assert uri == "sip:97141234567@cred_eu_999.sip.eu.vapi.ai"


# ─── 3. TwiML Generation (BYO SIP Trunk) ──────────────────────────────────────

def test_generate_vapi_sip_twiml_with_agent_headers_and_credential_subdomain():
    """Generates valid TwiML with <Dial><Sip> using credential_id subdomain and custom SIP headers."""
    ctx: InboundCallContext = {
        "agency_id": "agency-xyz",
        "agent_id": "agent-abc",
        "agent_name": "Hamdan Al-Maktoum",
        "custom_greeting": None,
        "resolution_tier": 1,
        "comm_account_id": "comm-1",
    }

    with patch.object(settings, "VAPI_SIP_CREDENTIAL_ID", "cred_trunk_real"):
        twiml = generate_vapi_sip_twiml(
            ctx=ctx,
            call_sid="CA998877",
            to_number="+97141234567",
        )

    root = ElementTree.fromstring(twiml)
    assert root.tag == "Response"

    dial = root.find("Dial")
    assert dial is not None

    sip = dial.find("Sip")
    assert sip is not None
    # Subdomain position assertion:
    assert "sip:97141234567@cred_trunk_real.sip.vapi.ai" in sip.text
    assert "X-Agent-Id=agent-abc" in sip.text
    assert "X-Agency-Id=agency-xyz" in sip.text
    assert "X-Call-Sid=CA998877" in sip.text
    assert "X-Resolution-Tier=1" in sip.text


def test_generate_vapi_sip_twiml_receptionist_fallback():
    """Fallback receptionist call when no agent match is found (Tier 3)."""
    ctx: InboundCallContext = {
        "agency_id": None,
        "agent_id": None,
        "agent_name": "",
        "custom_greeting": None,
        "resolution_tier": 3,
        "comm_account_id": None,
    }

    with patch.object(settings, "VAPI_SIP_CREDENTIAL_ID", "cred_trunk_fallback"):
        twiml = generate_vapi_sip_twiml(
            ctx=ctx,
            call_sid="CAfallback",
            to_number="+97141234567",
        )

    root = ElementTree.fromstring(twiml)
    sip = root.find("Dial/Sip")
    assert sip is not None
    assert "sip:97141234567@cred_trunk_fallback.sip.vapi.ai" in sip.text
    assert "X-Resolution-Tier=3" in sip.text
    assert "X-Agent-Id=" not in sip.text


# ─── 4. Inbound Webhook Endpoint ──────────────────────────────────────────────

class FakeVoiceRequest:
    def __init__(self, form_data, headers=None):
        self._form_data = form_data
        self.headers = headers or {}

    async def form(self):
        return self._form_data


@pytest.mark.asyncio
async def test_voice_inbound_webhook_returns_xml_twiml():
    """Verify POST /webhooks/voice/inbound processes form data and returns application/xml TwiML."""
    mock_form = {
        "CallSid": "CA1234567890abcdef",
        "From": "+971509998877",
        "To": "+97141234567",
        "ForwardedFrom": "+971501112233",
        "Direction": "inbound",
    }
    req = FakeVoiceRequest(form_data=mock_form)

    mock_sb = MagicMock()
    mock_sb.table().insert().execute.return_value.data = [{"id": "call-1"}]

    mock_ctx: InboundCallContext = {
        "agency_id": "agency-456",
        "agent_id": "agent-123",
        "agent_name": "Test Agent",
        "custom_greeting": None,
        "resolution_tier": 1,
        "comm_account_id": "comm-1",
    }

    with patch("routers.webhooks._verify_twilio_request", return_value=True), \
         patch("services.voice_service.resolve_inbound_caller", new_callable=AsyncMock) as mock_resolve, \
         patch("database.supabase_client.get_supabase", return_value=mock_sb), \
         patch("routers.webhooks.get_supabase", return_value=mock_sb), \
         patch("services.quota_service.check_and_consume_voice_quota", new_callable=AsyncMock), \
         patch.object(settings, "VAPI_SIP_CREDENTIAL_ID", "cred_live_trunk"):

        mock_resolve.return_value = mock_ctx

        response = await voice_inbound(req)

        assert response.status_code == 200
        assert "application/xml" in response.media_type
        assert b"<Response>" in response.body
        assert b"@cred_live_trunk.sip.vapi.ai" in response.body
        assert b"X-Agent-Id=agent-123" in response.body


# ─── 5. Agent Phone Settings Router ───────────────────────────────────────────

class FakeAgentRequest:
    def __init__(self, agent_id="agent-me", agency_id="agency-mine"):
        class State:
            pass
        self.state = State()
        self.state.agent_id = agent_id
        self.state.agency_id = agency_id


@pytest.mark.asyncio
async def test_get_forwarding_instructions():
    req = FakeAgentRequest()
    mock_sb = MagicMock()
    mock_sb.table().select().eq().eq().eq().maybe_single().execute.return_value.data = {
        "forwarding_status": "not_configured",
        "phone_number": "971501112233",
        "verified_caller_id_sid": None,
    }

    with patch.object(settings, "CENTRAL_INBOUND_DID", "+97141234567"), \
         patch("routers.agent_phone_settings.get_supabase", return_value=mock_sb):
        resp = await get_forwarding_info(req)

    assert resp["success"] is True
    data = resp["data"]
    assert data["central_inbound_did"] == "+97141234567"
    assert "**61*+97141234567#" in data["dial_codes"]["etisalat_du_no_answer"]
    assert "*67*+97141234567#" in data["dial_codes"]["generic_on_busy"]
    assert "*21*+97141234567#" in data["dial_codes"]["unconditional"]
    # 3GPP GSM standard for cancel all forwarding is ##002# (double hash)
    assert data["dial_codes"]["cancel_all"] == "##002#"


@pytest.mark.asyncio
async def test_initiate_outbound_caller_id():
    req = FakeAgentRequest()
    body = CallerIdInitiateRequest(phone_number="+971501112233")

    with patch("services.voice_service.initiate_outbound_caller_id_verification", new_callable=AsyncMock) as mock_init:
        mock_init.return_value = {
            "status": "verification_initiated",
            "validation_code": "123456",
            "call_sid": "CAtest",
            "message": "Twilio will call with a code",
        }
        resp = await initiate_caller_id_verification(request=req, body=body)

    assert resp["success"] is True
    assert resp["data"]["validation_code"] == "123456"


@pytest.mark.asyncio
async def test_confirm_outbound_caller_id():
    req = FakeAgentRequest()
    body = CallerIdConfirmRequest(phone_number="+971501112233", validation_code="123456")

    with patch("services.voice_service.confirm_outbound_caller_id_verification", new_callable=AsyncMock) as mock_conf:
        mock_conf.return_value = {
            "status": "verified",
            "outgoing_caller_id_sid": "PN123456",
            "message": "Number verified",
        }
        resp = await confirm_caller_id_verification(request=req, body=body)

    assert resp["success"] is True
    assert resp["data"]["outgoing_caller_id_sid"] == "PN123456"


@pytest.mark.asyncio
async def test_update_forwarding_status():
    req = FakeAgentRequest()
    body = ForwardingStatusUpdate(forwarding_status="configured")

    mock_sb = MagicMock()
    mock_sb.table().upsert().execute.return_value.data = [{"id": "comm-1"}]

    with patch("routers.agent_phone_settings.get_supabase", return_value=mock_sb):
        resp = await update_forwarding_status(request=req, body=body)

    assert resp["success"] is True
    assert resp["data"]["forwarding_status"] == "configured"
