"""
WhatsApp inbound webhook security tests (Issue #4c).

Covers:
- provider authentication (360dialog shared secret, Twilio X-Twilio-Signature)
- fail-closed behaviour in production when unconfigured
- safe sender→lead resolution: exact-first matching, ambiguity fail-safe,
  cross-tenant collision refusal
- no database mutation / no AI reply for rejected or ambiguous requests

Tests invoke whatsapp_inbound directly (no TestClient / main import) so they
cannot interfere with the app-wide supabase mock binding other suites rely on.
Run: pytest tests/test_whatsapp_webhook_security.py -v
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import HTTPException
from twilio.request_validator import RequestValidator

from config import settings
from routers.webhooks import whatsapp_inbound

WEBHOOK_URL = "http://testserver/webhooks/whatsapp"
DIALOG_TOKEN = "whsec_test_shared_secret"
TWILIO_AUTH_TOKEN = "twilio_test_auth_token"

SENDER_PHONE = "+971501234567"


def _lead(id_, agency_id, phone=SENDER_PHONE, ai_handling=True):
    return {
        "id": id_,
        "agency_id": agency_id,
        "phone": phone,
        "name": "Test Lead",
        "status": "qualifying",
        "is_ai_handling": ai_handling,
    }


class _CIHeaders:
    """Case-insensitive headers stand-in (mimics starlette.Headers.get)."""

    def __init__(self, items=None):
        self._d = {k.lower(): v for k, v in (items or {}).items()}

    def get(self, key, default=None):
        return self._d.get(str(key).lower(), default)


class FakeRequest:
    """Minimal starlette.Request stand-in for whatsapp_inbound."""

    def __init__(self, headers=None, query=None, json_body=None, form_data=None, url=WEBHOOK_URL):
        self.headers = _CIHeaders(headers)
        self.query_params = query or {}
        self._json_body = json_body
        self._form_data = form_data or {}
        self.url = url

    async def json(self):
        if self._json_body is None:
            raise ValueError("no json body")
        return self._json_body

    async def form(self):
        return self._form_data


def _dialog_payload(phone=SENDER_PHONE, body="Hello, I want a 2BR in Marina"):
    return {
        "messages": [{
            "type": "text",
            "from": phone,
            "text": {"body": body},
            "id": "wamid.test123",
        }]
    }


def _twilio_params(body="Hello, I want a 2BR in Marina", phone=SENDER_PHONE):
    return {
        "From": f"whatsapp:{phone}",
        "To": "whatsapp:+14155238886",
        "Body": body,
        "SmsMessageSid": "SMtest123",
        "ProfileName": "Test Lead",
    }


def _mock_sb(candidate_leads):
    sb = MagicMock()
    # leads candidate query: select -> ilike -> execute
    sb.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = candidate_leads
    # conversation history: select -> eq -> order -> limit -> execute
    sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
    return sb


async def _run_dialog(sb, request, ai_reply="AI reply text"):
    with patch("routers.webhooks.get_supabase", return_value=sb), \
         patch.object(settings, "WHATSAPP_PROVIDER", "360dialog"), \
         patch("routers.webhooks.qualify_and_respond", new_callable=AsyncMock) as mock_ai, \
         patch("routers.webhooks.detect_handover", new_callable=AsyncMock) as mock_handover, \
         patch("routers.webhooks.extract_lead_qualifications", new_callable=AsyncMock) as mock_extract, \
         patch("routers.webhooks.send_whatsapp_message", new_callable=AsyncMock) as mock_send:
        mock_ai.return_value = ai_reply
        mock_handover.return_value = {"needs_handover": False}
        mock_extract.return_value = {}
        mock_send.return_value = {"status": "sent", "sid": "SM1"}
        result = await whatsapp_inbound(request)
        return result, mock_ai, mock_send


async def _run_twilio(sb, request, ai_reply="AI reply text"):
    with patch("routers.webhooks.get_supabase", return_value=sb), \
         patch.object(settings, "WHATSAPP_PROVIDER", "twilio"), \
         patch.object(settings, "TWILIO_AUTH_TOKEN", TWILIO_AUTH_TOKEN), \
         patch("routers.webhooks.qualify_and_respond", new_callable=AsyncMock) as mock_ai, \
         patch("routers.webhooks.detect_handover", new_callable=AsyncMock) as mock_handover, \
         patch("routers.webhooks.extract_lead_qualifications", new_callable=AsyncMock) as mock_extract, \
         patch("routers.webhooks.send_whatsapp_message", new_callable=AsyncMock) as mock_send:
        mock_ai.return_value = ai_reply
        mock_handover.return_value = {"needs_handover": False}
        mock_extract.return_value = {}
        mock_send.return_value = {"status": "sent", "sid": "SM1"}
        result = await whatsapp_inbound(request)
        return result, mock_ai, mock_send


# ─── AUTHENTICATED REQUESTS BEHAVE AS BEFORE ──────────────────────────────────

@pytest.mark.asyncio
async def test_360dialog_valid_token_processes_message():
    sb = _mock_sb([_lead("lead-1", "agency-1")])
    request = FakeRequest(
        headers={"X-Webhook-Token": DIALOG_TOKEN},
        json_body=_dialog_payload(),
    )
    with patch.object(settings, "WHATSAPP_WEBHOOK_TOKEN", DIALOG_TOKEN):
        result, mock_ai, _ = await _run_dialog(sb, request)

    assert result["success"] is True
    mock_ai.assert_awaited_once()
    # inbound + outbound conversation rows inserted
    assert sb.table.return_value.insert.call_count == 2


@pytest.mark.asyncio
async def test_twilio_valid_signature_processes_message():
    sb = _mock_sb([_lead("lead-1", "agency-1")])
    params = _twilio_params()
    signature = RequestValidator(TWILIO_AUTH_TOKEN).compute_signature(WEBHOOK_URL, params)
    request = FakeRequest(
        headers={"X-Twilio-Signature": signature},
        form_data=params,
    )
    result, mock_ai, _ = await _run_twilio(sb, request)

    assert result["success"] is True
    mock_ai.assert_awaited_once()


@pytest.mark.asyncio
async def test_unique_exact_match_selects_correct_agency_and_lead():
    other = _lead("lead-suffix-only", "agency-other", phone="+971501234999")
    target = _lead("lead-exact", "agency-exact")
    sb = _mock_sb([other, target])
    request = FakeRequest(
        headers={"X-Webhook-Token": DIALOG_TOKEN},
        json_body=_dialog_payload(),
    )
    with patch.object(settings, "WHATSAPP_WEBHOOK_TOKEN", DIALOG_TOKEN):
        await _run_dialog(sb, request)

    insert_args = sb.table.return_value.insert.call_args[0][0]
    assert insert_args["lead_id"] == "lead-exact"
    assert insert_args["agency_id"] == "agency-exact"


@pytest.mark.asyncio
async def test_unknown_sender_still_skipped_without_mutation():
    sb = _mock_sb([])
    request = FakeRequest(
        headers={"X-Webhook-Token": DIALOG_TOKEN},
        json_body=_dialog_payload(phone="+975555555555"),
    )
    with patch.object(settings, "WHATSAPP_WEBHOOK_TOKEN", DIALOG_TOKEN):
        result, mock_ai, _ = await _run_dialog(sb, request)

    assert result["success"] is True
    mock_ai.assert_not_called()
    sb.table.return_value.insert.assert_not_called()
    sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_handed_over_lead_still_logged_but_not_auto_responded():
    sb = _mock_sb([_lead("lead-1", "agency-1", ai_handling=False)])
    request = FakeRequest(
        headers={"X-Webhook-Token": DIALOG_TOKEN},
        json_body=_dialog_payload(),
    )
    with patch.object(settings, "WHATSAPP_WEBHOOK_TOKEN", DIALOG_TOKEN):
        result, mock_ai, _ = await _run_dialog(sb, request)

    assert result["success"] is True
    # inbound message stored...
    assert sb.table.return_value.insert.call_count == 1
    # ...but no AI reply
    mock_ai.assert_not_called()


# ─── REJECTED / FAIL-SAFE ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_360dialog_missing_token_rejected():
    sb = _mock_sb([_lead("lead-1", "agency-1")])
    request = FakeRequest(json_body=_dialog_payload())  # no header, no query param
    with patch.object(settings, "WHATSAPP_WEBHOOK_TOKEN", DIALOG_TOKEN):
        with pytest.raises(HTTPException) as exc_info:
            await _run_dialog(sb, request)

    assert exc_info.value.status_code == 403
    sb.table.return_value.insert.assert_not_called()
    sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_360dialog_wrong_token_forgery_rejected():
    sb = _mock_sb([_lead("lead-1", "agency-1")])
    request = FakeRequest(
        headers={"X-Webhook-Token": "attacker-controlled-value"},
        json_body=_dialog_payload(),
    )
    with patch.object(settings, "WHATSAPP_WEBHOOK_TOKEN", DIALOG_TOKEN):
        with pytest.raises(HTTPException) as exc_info:
            await _run_dialog(sb, request)

    assert exc_info.value.status_code == 403
    sb.table.return_value.insert.assert_not_called()
    sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_360dialog_production_without_config_fails_closed():
    sb = _mock_sb([_lead("lead-1", "agency-1")])
    request = FakeRequest(json_body=_dialog_payload())
    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "WHATSAPP_WEBHOOK_TOKEN", ""):
        with pytest.raises(HTTPException) as exc_info:
            await _run_dialog(sb, request)

    assert exc_info.value.status_code == 403
    sb.table.return_value.insert.assert_not_called()
    sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_twilio_missing_signature_rejected():
    sb = _mock_sb([_lead("lead-1", "agency-1")])
    request = FakeRequest(form_data=_twilio_params())  # no X-Twilio-Signature
    with pytest.raises(HTTPException) as exc_info:
        await _run_twilio(sb, request)

    assert exc_info.value.status_code == 403
    sb.table.return_value.insert.assert_not_called()
    sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_twilio_tampered_body_forgery_rejected():
    sb = _mock_sb([_lead("lead-1", "agency-1")])
    params = _twilio_params()
    # Signature computed over a DIFFERENT body than the one delivered
    signature = RequestValidator(TWILIO_AUTH_TOKEN).compute_signature(
        WEBHOOK_URL, _twilio_params(body="legitimate message")
    )
    request = FakeRequest(headers={"X-Twilio-Signature": signature}, form_data=params)
    with pytest.raises(HTTPException) as exc_info:
        await _run_twilio(sb, request)

    assert exc_info.value.status_code == 403
    sb.table.return_value.insert.assert_not_called()
    sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_cross_tenant_phone_collision_mutates_nothing():
    """Same person is a lead at two agencies — refuse instead of picking one."""
    sb = _mock_sb([
        _lead("lead-agency-a", "agency-a"),
        _lead("lead-agency-b", "agency-b"),
    ])
    request = FakeRequest(
        headers={"X-Webhook-Token": DIALOG_TOKEN},
        json_body=_dialog_payload(),
    )
    with patch.object(settings, "WHATSAPP_WEBHOOK_TOKEN", DIALOG_TOKEN):
        result, mock_ai, _ = await _run_dialog(sb, request)

    assert result["success"] is True  # swallowed safely, provider gets 200
    mock_ai.assert_not_called()
    sb.table.return_value.insert.assert_not_called()
    sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_ambiguous_suffix_match_triggers_no_ai_response():
    """Two different stored numbers share the sender's last 9 digits and
    neither is an exact match — the system must refuse to guess."""
    sb = _mock_sb([
        _lead("lead-x", "agency-x", phone="+15501234567"),
        _lead("lead-y", "agency-y", phone="+20501234567"),
    ])
    request = FakeRequest(
        headers={"X-Webhook-Token": DIALOG_TOKEN},
        json_body=_dialog_payload(),
    )
    with patch.object(settings, "WHATSAPP_WEBHOOK_TOKEN", DIALOG_TOKEN):
        result, mock_ai, _ = await _run_dialog(sb, request)

    assert result["success"] is True
    mock_ai.assert_not_called()
    sb.table.return_value.insert.assert_not_called()
    sb.table.return_value.update.assert_not_called()
