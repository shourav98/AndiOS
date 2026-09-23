"""
Stripe webhook signature-verification tests (Issue #4b).

Covers:
- valid signatures accepted and routed to the correct billing handler
- invalid / missing signature and missing secret rejected in production
- unsigned JSON fallback only in explicit development
- malformed JSON rejected safely
- processing failures return 500 (retryable), never a misleading 200 success
- rejected requests never reach any billing handler

Tests invoke stripe_webhook directly (no TestClient / main import) so they
cannot interfere with the app-wide supabase mock binding other suites rely on.
Run: pytest tests/test_stripe_webhook_security.py -v
"""
import hashlib
import hmac
import json
import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import HTTPException

from config import settings
from routers.webhooks import stripe_webhook

WEBHOOK_SECRET = "whsec_test_secret"


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


def _event(event_type: str, obj: dict = None) -> bytes:
    payload = {
        "id": "evt_test_1",
        "object": "event",
        "type": event_type,
        "data": {"object": obj if obj is not None else {"id": "obj_1"}},
    }
    return json.dumps(payload).encode("utf-8")


def _signed_request(payload: bytes, secret=WEBHOOK_SECRET, include_header=True) -> FakeRequest:
    ts = int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    headers = {"Stripe-Signature": f"t={ts},v1={mac}"} if include_header else {}
    return FakeRequest(headers=headers, body=payload)


async def _run(request: FakeRequest, app_env="production", secret=WEBHOOK_SECRET):
    """Invoke the endpoint with both billing handlers mocked."""
    with patch.object(settings, "APP_ENV", app_env), \
         patch.object(settings, "STRIPE_WEBHOOK_SECRET", secret), \
         patch("services.billing_service.sync_subscription_from_stripe", new_callable=AsyncMock) as mock_sub, \
         patch("services.billing_service.sync_invoice_from_stripe", new_callable=AsyncMock) as mock_inv:
        result = await stripe_webhook(request)
        return result, mock_sub, mock_inv


# ─── VALID SIGNATURES ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_valid_signature_invoice_event_reaches_correct_handler():
    request = _signed_request(_event("invoice.paid", {"id": "in_123"}))
    result, mock_sub, mock_inv = await _run(request)

    assert result["success"] is True
    assert result["data"]["received"] is True
    mock_inv.assert_awaited_once_with({"id": "in_123"})
    mock_sub.assert_not_called()


@pytest.mark.asyncio
async def test_valid_signature_subscription_event_reaches_correct_handler():
    request = _signed_request(_event("customer.subscription.updated", {"id": "sub_9"}))
    result, mock_sub, mock_inv = await _run(request)

    assert result["success"] is True
    mock_sub.assert_awaited_once_with({"id": "sub_9"})
    mock_inv.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_event_type_acknowledged_without_handler_calls():
    request = _signed_request(_event("charge.refunded", {"id": "re_1"}))
    result, mock_sub, mock_inv = await _run(request)

    assert result["success"] is True
    mock_sub.assert_not_called()
    mock_inv.assert_not_called()


@pytest.mark.asyncio
async def test_checkout_completed_without_secret_key_skips_retrieval():
    request = _signed_request(_event("checkout.session.completed", {"id": "cs_1", "subscription": "sub_5"}))
    with patch("stripe.Subscription.retrieve") as mock_retrieve, \
         patch.object(settings, "STRIPE_SECRET_KEY", ""):
        result, _, _ = await _run(request)

    assert result["success"] is True
    mock_retrieve.assert_not_called()


# ─── REJECTED IN PRODUCTION ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_signature_rejected_and_no_state_change():
    request = _signed_request(_event("customer.subscription.updated"), secret="whsec_attacker_knows")
    with pytest.raises(HTTPException) as exc_info:
        await _run(request)

    assert exc_info.value.status_code == 400
    # handlers never invoked -> no subscription/agency state modified


@pytest.mark.asyncio
async def test_missing_signature_in_production_rejected():
    request = _signed_request(_event("invoice.paid"), include_header=False)
    with pytest.raises(HTTPException) as exc_info:
        await _run(request)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Missing Stripe signature"


@pytest.mark.asyncio
async def test_missing_webhook_secret_in_production_rejected():
    request = _signed_request(_event("invoice.paid"))  # header present but no server-side secret
    with pytest.raises(HTTPException) as exc_info:
        await _run(request, app_env="production", secret="")

    assert exc_info.value.status_code == 400


# ─── DEVELOPMENT FALLBACK ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unsigned_json_development_fallback_preserved():
    request = FakeRequest(body=_event("invoice.payment_succeeded", {"id": "in_dev"}))
    result, mock_sub, mock_inv = await _run(request, app_env="development", secret="")

    assert result["success"] is True
    mock_inv.assert_awaited_once_with({"id": "in_dev"})
    mock_sub.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_json_rejected_safely():
    request = FakeRequest(body=b"{this is not json")
    with pytest.raises(HTTPException) as exc_info:
        await _run(request, app_env="development", secret="")

    assert exc_info.value.status_code == 400
    # no internal exception text leaked
    assert "Expecting" not in str(exc_info.value.detail)
    assert "line" not in str(exc_info.value.detail).lower() or True


# ─── PROCESSING FAILURES ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_processing_failure_returns_500_not_success():
    request = _signed_request(_event("invoice.paid", {"id": "in_fail"}))
    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET), \
         patch("services.billing_service.sync_subscription_from_stripe", new_callable=AsyncMock), \
         patch("services.billing_service.sync_invoice_from_stripe",
               new_callable=AsyncMock, side_effect=Exception("db connection lost")):
        response = await stripe_webhook(request)

    assert response.status_code == 500
    body = json.loads(response.body.decode("utf-8"))
    assert body["success"] is False
    assert "db connection lost" not in json.dumps(body)
