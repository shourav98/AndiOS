"""
Bayut / Dubizzle portal webhook authentication tests (Issue #4e).

These portals do not sign webhook deliveries in this integration, so each
endpoint requires an operator-provisioned shared token ('X-Webhook-Token'
header or '?token=' callback query param), fail-closed in production.

Covers:
- valid token -> processing proceeds (webhook log written, dedup path reached)
- missing / wrong token -> rejected with ZERO downstream mutation
- production without configured token -> fails closed
- development tolerance preserved when unconfigured
- per-provider credential isolation (bayut token does not authorize dubizzle)

Tests invoke the endpoints directly (no TestClient / main import) so they
cannot interfere with the app-wide supabase mock binding other suites rely on.
Run: pytest tests/test_bayut_dubizzle_webhook_security.py -v
"""
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import HTTPException

from config import settings
from routers.webhooks import bayut_webhook, dubizzle_webhook

BAYUT_TOKEN = "portal_token_bayut"
DUBIZZLE_TOKEN = "portal_token_dubizzle"


class _CIHeaders:
    def __init__(self, items=None):
        self._d = {k.lower(): v for k, v in (items or {}).items()}

    def get(self, key, default=None):
        return self._d.get(str(key).lower(), default)


class FakeRequest:
    def __init__(self, headers=None, query=None, payload=None):
        self.headers = _CIHeaders(headers)
        self.query_params = query or {}
        self._payload = payload if payload is not None else {
            "lead": {"id": "PTL-1", "name": "Jane Doe", "phone": "+971501234567"},
        }

    async def json(self):
        return self._payload


async def _run(endpoint, request, *, bayut=BAYUT_TOKEN, dubizzle=DUBIZZLE_TOKEN, app_env="production"):
    """Invoke a portal endpoint with downstream processing mocked to the dedup short-circuit."""
    setting_map = {"BAYUT_WEBHOOK_TOKEN": bayut, "DUBIZZLE_WEBHOOK_TOKEN": dubizzle}
    patches = [patch.object(settings, "APP_ENV", app_env)]
    for name, value in setting_map.items():
        patches.append(patch.object(settings, name, value))
    with patches[0], patches[1], patches[2], \
         patch("routers.webhooks.get_supabase") as mock_get_sb, \
         patch("routers.webhooks.resolve_agency_and_agent", new_callable=AsyncMock) as mock_route, \
         patch("routers.webhooks.is_duplicate", new_callable=AsyncMock) as mock_dup:
        sb = MagicMock()
        mock_get_sb.return_value = sb
        sb.table.return_value.insert.return_value.execute.return_value.data = [{"id": "log-1"}]
        mock_route.return_value = ("agency-1", "agent-1")
        mock_dup.return_value = True  # short-circuit at dedup; nothing further runs
        result = await endpoint(request)
        return result, sb


# ─── ACCEPTED ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bayut_valid_token_processing_unchanged():
    result, sb = await _run(bayut_webhook, FakeRequest(headers={"X-Webhook-Token": BAYUT_TOKEN}))

    assert result["success"] is True
    assert result["data"]["status"] == "duplicate"  # reached existing processing path
    sb.table.return_value.insert.assert_called_once()  # webhook log written


@pytest.mark.asyncio
async def test_dubizzle_valid_token_processing_unchanged():
    result, sb = await _run(dubizzle_webhook, FakeRequest(query={"token": DUBIZZLE_TOKEN}))

    assert result["success"] is True
    assert result["data"]["status"] == "duplicate"
    sb.table.return_value.insert.assert_called_once()


@pytest.mark.asyncio
async def test_development_without_tokens_still_works_for_local_testing():
    for endpoint in (bayut_webhook, dubizzle_webhook):
        result, _ = await _run(endpoint, FakeRequest(), app_env="development",
                               bayut="", dubizzle="")
        assert result["success"] is True


# ─── REJECTED ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bayut_missing_token_rejected_zero_mutation():
    with pytest.raises(HTTPException) as exc_info:
        await _run(bayut_webhook, FakeRequest())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_bayut_wrong_token_rejected_zero_mutation():
    with pytest.raises(HTTPException) as exc_info:
        await _run(bayut_webhook, FakeRequest(headers={"X-Webhook-Token": "attacker-value"}))

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_rejected_requests_perform_zero_database_mutations():
    """Rejection must happen before the webhook_logs insert (and everything else)."""
    for endpoint in (bayut_webhook, dubizzle_webhook):
        with patch.object(settings, "APP_ENV", "production"), \
             patch.object(settings, "BAYUT_WEBHOOK_TOKEN", BAYUT_TOKEN), \
             patch.object(settings, "DUBIZZLE_WEBHOOK_TOKEN", DUBIZZLE_TOKEN), \
             patch("routers.webhooks.get_supabase") as mock_get_sb, \
             patch("routers.webhooks.resolve_agency_and_agent", new_callable=AsyncMock), \
             patch("routers.webhooks.is_duplicate", new_callable=AsyncMock):
            sb = MagicMock()
            mock_get_sb.return_value = sb
            with pytest.raises(HTTPException) as exc_info:
                await endpoint(FakeRequest(headers={"X-Webhook-Token": "forged"}))

            assert exc_info.value.status_code == 403
            sb.table.return_value.insert.assert_not_called()
            sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_production_without_configured_tokens_fails_closed():
    """Attacker-supplied headers cannot substitute for configured tokens."""
    for endpoint in (bayut_webhook, dubizzle_webhook):
        with pytest.raises(HTTPException) as exc_info:
            await _run(endpoint,
                       FakeRequest(headers={"X-Webhook-Token": "whatever-the-attacker-sends"}),
                       bayut="", dubizzle="")

        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_credentials_are_isolated_between_portals():
    """The Bayut token must not authorize the Dubizzle endpoint and vice versa."""
    with pytest.raises(HTTPException) as exc_info:
        await _run(dubizzle_webhook, FakeRequest(headers={"X-Webhook-Token": BAYUT_TOKEN}))
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        await _run(bayut_webhook, FakeRequest(headers={"X-Webhook-Token": DUBIZZLE_TOKEN}))
    assert exc_info.value.status_code == 403
