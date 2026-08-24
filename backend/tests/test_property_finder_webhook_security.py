"""
Property Finder webhook signature-verification tests (Issue #4a).

Covers:
- valid HMAC-SHA256 signatures accepted and processed by the existing handler
- invalid / tampered / missing signatures rejected in production
- missing PROPERTY_FINDER_WEBHOOK_SECRET fails closed in production
- APP_ENV=development can NEVER disable verification when a secret is set
- development tolerance for unsigned requests only while no secret is set
- rejected requests perform zero database mutations

Tests invoke property_finder_webhook directly (no TestClient / main import)
so they cannot interfere with the app-wide supabase mock binding other
suites rely on.
Run: pytest tests/test_property_finder_webhook_security.py -v
"""
import hashlib
import hmac
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import HTTPException

from config import settings
from routers.webhooks import property_finder_webhook

PF_SECRET = "pf_webhook_shared_secret"


class _CIHeaders:
    def __init__(self, items=None):
        self._d = {k.lower(): v for k, v in (items or {}).items()}

    def get(self, key, default=None):
        return self._d.get(str(key).lower(), default)


class FakeRequest:
    def __init__(self, headers=None, body: bytes = b""):
        self.headers = _CIHeaders(headers)
        self._body = body

    async def body(self) -> bytes:
        return self._body

    async def json(self):
        return json.loads(self._body.decode("utf-8"))


def _payload_bytes(phone="+971501234567") -> bytes:
    return json.dumps({
        "lead": {
            "id": "PF-SEC-1",
            "name": "John Doe",
            "phone": phone,
        }
    }).encode("utf-8")


def _sign(body: bytes, secret=PF_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _run(request: FakeRequest, app_env="production", secret=PF_SECRET):
    """Invoke the endpoint with downstream processing mocked out."""
    with patch.object(settings, "APP_ENV", app_env), \
         patch.object(settings, "PROPERTY_FINDER_WEBHOOK_SECRET", secret), \
         patch("routers.webhooks.get_supabase") as mock_get_sb, \
         patch("routers.webhooks.resolve_agency_and_agent", new_callable=AsyncMock) as mock_route, \
         patch("routers.webhooks.is_duplicate", new_callable=AsyncMock) as mock_dup:
        sb = MagicMock()
        mock_get_sb.return_value = sb
        sb.table.return_value.insert.return_value.execute.return_value.data = [{"id": "log-1"}]
        mock_route.return_value = ("agency-1", "agent-1")
        mock_dup.return_value = True  # short-circuit at dedup; nothing further runs
        result = await property_finder_webhook(request)
        return result, sb


# ─── ACCEPTED ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_valid_signature_reaches_existing_handler_unchanged():
    body = _payload_bytes()
    request = FakeRequest(headers={"X-Hub-Signature-256": _sign(body)}, body=body)
    result, sb = await _run(request)

    assert result["success"] is True
    assert result["data"]["status"] == "duplicate"
    # got past authentication: raw payload was logged
    sb.table.return_value.insert.assert_called_once()


@pytest.mark.asyncio
async def test_development_without_secret_still_works_for_local_testing():
    request = FakeRequest(body=_payload_bytes())  # unsigned
    result, _ = await _run(request, app_env="development", secret="")

    assert result["success"] is True
    assert result["data"]["status"] == "duplicate"


# ─── REJECTED ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_signature_rejected_in_production():
    body = _payload_bytes()
    request = FakeRequest(headers={"X-Hub-Signature-256": _sign(body, secret="whsec_attacker")}, body=body)
    with pytest.raises(HTTPException) as exc_info:
        await _run(request)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_tampered_body_rejected():
    legit_sig = _sign(_payload_bytes(phone="+971509999999"))  # signed different body
    request = FakeRequest(headers={"X-Hub-Signature-256": legit_sig}, body=_payload_bytes())
    with pytest.raises(HTTPException) as exc_info:
        await _run(request)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_signature_in_production_rejected():
    request = FakeRequest(body=_payload_bytes())
    with pytest.raises(HTTPException) as exc_info:
        await _run(request)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_secret_in_production_fails_closed():
    body = _payload_bytes()
    # attacker even supplies a syntactically valid-looking header
    request = FakeRequest(headers={"X-Hub-Signature-256": _sign(body)}, body=body)
    with pytest.raises(HTTPException) as exc_info:
        await _run(request, app_env="production", secret="")

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_development_cannot_disable_verification_when_secret_configured():
    """THE audit case: APP_ENV=development used to bypass verification even
    when a real secret was configured. It must now enforce strictly."""
    body = _payload_bytes()
    bad_request = FakeRequest(
        headers={"X-Hub-Signature-256": _sign(body, secret="whsec_wrong")},
        body=body,
    )
    with pytest.raises(HTTPException) as exc_bad:
        await _run(bad_request, app_env="development", secret=PF_SECRET)
    assert exc_bad.value.status_code == 403

    unsigned_request = FakeRequest(body=body)
    with pytest.raises(HTTPException) as exc_unsigned:
        await _run(unsigned_request, app_env="development", secret=PF_SECRET)
    assert exc_unsigned.value.status_code == 403


@pytest.mark.asyncio
async def test_rejected_requests_perform_zero_database_mutations():
    body = _payload_bytes()
    request = FakeRequest(
        headers={"X-Hub-Signature-256": _sign(body, secret="whsec_attacker")},
        body=body,
    )
    with pytest.raises(HTTPException):
        result, sb = None, None
        # capture mocks inside the same context
        with patch.object(settings, "APP_ENV", "production"), \
             patch.object(settings, "PROPERTY_FINDER_WEBHOOK_SECRET", PF_SECRET), \
             patch("routers.webhooks.get_supabase") as mock_get_sb, \
             patch("routers.webhooks.resolve_agency_and_agent", new_callable=AsyncMock), \
             patch("routers.webhooks.is_duplicate", new_callable=AsyncMock):
            sb = MagicMock()
            mock_get_sb.return_value = sb
            await property_finder_webhook(request)

    sb.table.return_value.insert.assert_not_called()
    sb.table.return_value.update.assert_not_called()
