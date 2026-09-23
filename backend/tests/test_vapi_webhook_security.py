"""
Vapi webhook authentication tests (Issue #4d).

Vapi's supported server-message auth mechanism is a shared secret delivered
as the 'x-vapi-secret' header (configured as server.secret in the Vapi
dashboard). These tests cover:
- valid secret -> event reaches process_vapi_webhook with the exact payload
- missing / wrong secret -> rejected, processor never invoked
- production without configured secret -> fails closed
- development tolerance preserved when unconfigured
- processing errors keep the 200 delivery contract but leak no internals

Tests invoke vapi_webhook directly (no TestClient / main import) so they
cannot interfere with the app-wide supabase mock binding other suites rely on.
Run: pytest tests/test_vapi_webhook_security.py -v
"""
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import HTTPException

from config import settings
import services.vapi_service  # noqa: F401  -- bind package attr for the patch target
from routers.webhooks import vapi_webhook

VAPI_SECRET = "vapi_server_shared_secret"

PAYLOAD = {
    "type": "end-of-call-report",
    "call": {
        "id": "vapi-call-abc",
        "metadata": {"campaign_id": "camp-1", "owner_id": "owner-9", "agency_id": "agency-1"},
        "endedReason": "customer-ended-call",
    },
    "transcript": "Hello, is the Marina unit still available?",
    "analysis": {"outcome": "Listing won"},
}


class _CIHeaders:
    def __init__(self, items=None):
        self._d = {k.lower(): v for k, v in (items or {}).items()}

    def get(self, key, default=None):
        return self._d.get(str(key).lower(), default)


class FakeRequest:
    def __init__(self, headers=None, payload=None):
        self.headers = _CIHeaders(headers)
        self._payload = payload if payload is not None else PAYLOAD

    async def json(self):
        return self._payload


async def _run(request: FakeRequest, app_env="production", secret=VAPI_SECRET,
               processor_result=None, processor_exc=None):
    with patch.object(settings, "APP_ENV", app_env), \
         patch.object(settings, "VAPI_WEBHOOK_SECRET", secret), \
         patch("services.vapi_service.process_vapi_webhook", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = processor_result or {"status": "processed", "outcome": "listing_won"}
        if processor_exc:
            mock_proc.side_effect = processor_exc
        result = await vapi_webhook(request)
        return result, mock_proc


# ─── ACCEPTED ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_valid_secret_reaches_processor_with_exact_payload():
    request = FakeRequest(headers={"X-Vapi-Secret": VAPI_SECRET})
    result, mock_proc = await _run(request)

    assert result["success"] is True
    assert result["data"] == {"status": "processed", "outcome": "listing_won"}
    mock_proc.assert_awaited_once_with(PAYLOAD)  # payload untouched


@pytest.mark.asyncio
async def test_development_without_secret_still_works_for_local_testing():
    request = FakeRequest()  # no header
    result, mock_proc = await _run(request, app_env="development", secret="")

    assert result["success"] is True
    mock_proc.assert_awaited_once()


# ─── REJECTED ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_secret_header_in_production_rejected():
    request = FakeRequest()
    with pytest.raises(HTTPException) as exc_info:
        await _run(request)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_wrong_secret_rejected_and_processor_not_called():
    request = FakeRequest(headers={"X-Vapi-Secret": "attacker-controlled"})
    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "VAPI_WEBHOOK_SECRET", VAPI_SECRET), \
         patch("services.vapi_service.process_vapi_webhook", new_callable=AsyncMock) as mock_proc:
        with pytest.raises(HTTPException) as exc_info:
            await vapi_webhook(request)

    assert exc_info.value.status_code == 403
    mock_proc.assert_not_called()


@pytest.mark.asyncio
async def test_production_without_configured_secret_fails_closed():
    """Attacker-supplied header cannot substitute for a configured secret."""
    request = FakeRequest(headers={"X-Vapi-Secret": "whatever-the-attacker-wants"})
    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "VAPI_WEBHOOK_SECRET", ""), \
         patch("services.vapi_service.process_vapi_webhook", new_callable=AsyncMock) as mock_proc:
        with pytest.raises(HTTPException) as exc_info:
            await vapi_webhook(request)

    assert exc_info.value.status_code == 403
    mock_proc.assert_not_called()


@pytest.mark.asyncio
async def test_missing_header_in_production_never_invokes_processor():
    request = FakeRequest()
    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "VAPI_WEBHOOK_SECRET", VAPI_SECRET), \
         patch("services.vapi_service.process_vapi_webhook", new_callable=AsyncMock) as mock_proc:
        with pytest.raises(HTTPException):
            await vapi_webhook(request)

    mock_proc.assert_not_called()


# ─── PROCESSING ERRORS ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_processing_error_keeps_200_contract_without_internal_leak():
    request = FakeRequest(headers={"X-Vapi-Secret": VAPI_SECRET})
    result, mock_proc = await _run(
        request,
        processor_exc=Exception("postgres connection lost to 10.0.0.5:5432"),
    )

    # Deliberate delivery contract preserved (Vapi treats non-2xx as failure)
    assert result["success"] is True
    assert result["data"]["status"] == "error"
    body_text = json.dumps(result)
    assert "postgres" not in body_text
    assert "10.0.0.5" not in body_text
