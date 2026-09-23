"""
Tests for connectors router security, routing precedence, and fail-closed behaviors:
- Route order: /connectors/meta-esu/disconnect and /connectors/google-calendar/disconnect
- Secret scrubbing in logs: token exchange never logs client_secret or code (caplog test)
- Fail-closed WABA and debug_token verification and pagination
- Upsert lookup by (agency_id, agent_id, channel) updating legacy Twilio/360dialog rows
- Cross-tenant agent_id rejection for manager requests
- 409 Conflict handling on DB unique constraint violations
- Registration fail-closed and PIN storage rules
- WABA unsubscribe on disconnect and nulling registration_pin_enc
"""
import logging
from unittest.mock import MagicMock, patch, AsyncMock
import pytest
from fastapi.testclient import TestClient

from main import app
from routers.connectors import (
    _exchange_code_for_token,
    _verify_waba_and_phone,
    _register_phone_number_if_needed,
)
from middleware.auth_middleware import verify_token


@pytest.fixture
def mock_auth_user():
    return {
        "id": "agent-user-uuid-1",
        "sub": "agent-user-uuid-1",
        "agency_id": "agency-uuid-1",
        "role": "agent",
    }


@pytest.fixture
def mock_manager_user():
    return {
        "id": "manager-user-uuid-1",
        "sub": "manager-user-uuid-1",
        "agency_id": "agency-uuid-1",
        "role": "owner",
    }


def test_route_precedence_meta_esu_disconnect(mock_auth_user):
    """Item 6: Verify POST /connectors/meta-esu/disconnect reaches the specific handler."""
    app.dependency_overrides[verify_token] = lambda: mock_auth_user
    mock_sb = MagicMock()
    mock_table = MagicMock()
    mock_select = MagicMock()
    mock_select.execute.return_value = MagicMock(data=[])
    mock_update = MagicMock()
    mock_update.execute.return_value = MagicMock(data=[])
    mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value = mock_select
    mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.is_.return_value.limit.return_value = mock_select
    mock_table.update.return_value.eq.return_value.eq.return_value.eq.return_value = mock_update
    mock_sb.table.return_value = mock_table

    with patch("routers.connectors.get_supabase", return_value=mock_sb), \
         patch("httpx.AsyncClient.delete", new_callable=AsyncMock) as mock_delete:
        mock_delete.return_value = MagicMock(status_code=200)
        client = TestClient(app)
        response = client.post("/connectors/meta-esu/disconnect")
        assert response.status_code == 200
        assert "disconnected successfully" in response.json()["message"]
    app.dependency_overrides.clear()


def test_route_precedence_google_calendar_disconnect(mock_auth_user):
    """Item 6: Verify POST /connectors/google-calendar/disconnect reaches the specific handler."""
    app.dependency_overrides[verify_token] = lambda: mock_auth_user
    mock_sb = MagicMock()
    mock_table = MagicMock()
    mock_update = MagicMock()
    mock_update.execute.return_value = MagicMock(data=[])
    mock_table.update.return_value.eq.return_value.eq.return_value = mock_update
    mock_sb.table.return_value = mock_table

    with patch("routers.connectors.get_supabase", return_value=mock_sb):
        client = TestClient(app)
        response = client.post("/connectors/google-calendar/disconnect")
        assert response.status_code == 200
        assert response.json()["message"] == "Google Calendar disconnected"
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_token_exchange_does_not_log_secrets(caplog, monkeypatch):
    """Item 2: Caplog test proving token exchange does not log client_secret or code."""
    from config import settings
    monkeypatch.setattr(settings, "META_APP_ID", "app-id-12345")
    monkeypatch.setattr(settings, "META_APP_SECRET", "super-confidential-secret-999")
    secret_code = "secret-oauth-authorization-code-777"

    caplog.set_level(logging.DEBUG)

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = '{"error":{"message":"Invalid verification code"}}'

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        with pytest.raises(Exception) as excinfo:
            await _exchange_code_for_token(secret_code, corr_id="corr-test-123")

        # Client receives generic error with correlation id, not raw Meta response
        assert "super-confidential-secret-999" not in str(excinfo.value)
        assert secret_code not in str(excinfo.value)
        assert "Invalid verification code" not in str(excinfo.value)
        assert "corr-test-123" in str(excinfo.value)

    # Inspect logs: neither client_secret nor authorization code must appear in logs
    for record in caplog.records:
        assert "super-confidential-secret-999" not in record.message
        assert secret_code not in record.message


@pytest.mark.asyncio
async def test_verify_waba_and_phone_fails_closed():
    """Item 4: Verify _verify_waba_and_phone rejects non-200 and handles pagination."""
    # Subtest A: debug_token invalid -> reject
    mock_debug_resp = MagicMock()
    mock_debug_resp.status_code = 200
    mock_debug_resp.json.return_value = {"data": {"is_valid": False}}

    with (
        patch("routers.connectors.getattr", side_effect=lambda obj, attr, default=None: "dummy" if "META" in attr else default),
        patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_debug_resp),
    ):
        with pytest.raises(Exception) as exc:
            await _verify_waba_and_phone("waba-1", "phone-1", "tok-1", corr_id="corr-1")
        assert "invalid or expired" in str(exc.value).lower() or "400" in str(exc.value)

    # Subtest B: WABA phone numbers query fails with non-200 -> rejects request
    mock_debug_valid = MagicMock(status_code=200)
    mock_debug_valid.json.return_value = {
        "data": {
            "is_valid": True,
            "granular_scopes": [{"scope": "whatsapp_business_management", "target_ids": ["waba-1"]}],
        }
    }


    mock_phone_resp = MagicMock(status_code=200)
    mock_phone_resp.json.return_value = {"id": "phone-1", "display_phone_number": "+1234567890"}

    mock_waba_fail = MagicMock(status_code=500, text="Internal Graph Error")

    with (
        patch("routers.connectors.getattr", side_effect=lambda obj, attr, default=None: "dummy" if "META" in attr else default),
        patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=[mock_debug_valid, mock_phone_resp, mock_waba_fail]),
    ):
        with pytest.raises(Exception) as exc:
            await _verify_waba_and_phone("waba-1", "phone-1", "tok-1", corr_id="corr-2")
        assert "400" in str(exc.value)

    # Subtest C: Pagination finds phone on second page
    mock_waba_p1 = MagicMock(status_code=200)
    mock_waba_p1.json.return_value = {
        "data": [{"id": "other-phone-99"}],
        "paging": {"next": "https://graph.facebook.com/v26.0/waba-1/phone_numbers?after=xyz"},
    }
    mock_waba_p2 = MagicMock(status_code=200)
    mock_waba_p2.json.return_value = {
        "data": [{"id": "phone-1"}],
        "paging": {},
    }

    with (
        patch("routers.connectors.getattr", side_effect=lambda obj, attr, default=None: "dummy" if "META" in attr else default),
        patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=[mock_debug_valid, mock_phone_resp, mock_waba_p1, mock_waba_p2]),
    ):
        phone_data = await _verify_waba_and_phone("waba-1", "phone-1", "tok-1", corr_id="corr-3")
        assert phone_data["id"] == "phone-1"


def test_cross_tenant_agent_rejection(mock_manager_user):
    """Item 8: Manager-supplied agent_id not in caller's agency must be rejected."""
    app.dependency_overrides[verify_token] = lambda: mock_manager_user

    mock_sb = MagicMock()
    # Mock agents table check returning empty (agent belongs to different agency)
    mock_agents_table = MagicMock()
    mock_agents_res = MagicMock(data=[])
    mock_agents_table.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = mock_agents_res

    def sb_table_router(table_name):
        if table_name == "agents":
            return mock_agents_table
        return MagicMock()

    mock_sb.table.side_effect = sb_table_router

    with patch("routers.connectors.get_supabase", return_value=mock_sb):
        client = TestClient(app)
        payload = {
            "code": "test-code",
            "waba_id": "waba-123",
            "phone_number_id": "p-123",
            "agent_id": "foreign-agent-uuid-999",
        }
        response = client.post("/connectors/meta-esu/callback", json=payload)
        assert response.status_code == 400
        assert "does not belong to your agency" in str(response.json())
    app.dependency_overrides.clear()


def test_upsert_updates_legacy_twilio_row(mock_auth_user):
    """Item 7 & 1: Upsert finds row by (agency_id, agent_id, channel) regardless of provider and writes no app_secret."""
    app.dependency_overrides[verify_token] = lambda: mock_auth_user

    mock_sb = MagicMock()
    # Mock agents table: not queried for standard agent
    mock_comm_table = MagicMock()

    # Phone uniqueness check (no other account owns this phone)
    mock_phone_check = MagicMock(data=[])
    # Existing row for this agent: legacy Twilio provider
    mock_existing_account = MagicMock(data=[{"id": "legacy-acc-123", "provider": "twilio"}])

    updated_payloads = []

    def mock_update_fn(data):
        updated_payloads.append(data)
        m = MagicMock()
        m.eq.return_value.execute.return_value = MagicMock(data=[{"id": "legacy-acc-123"}])
        return m

    mock_comm_table.select.return_value.eq.return_value.neq.return_value.limit.return_value.execute.return_value = mock_phone_check
    mock_comm_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = mock_existing_account
    mock_comm_table.update.side_effect = mock_update_fn

    mock_sb.table.return_value = mock_comm_table

    with (
        patch("routers.connectors.get_supabase", return_value=mock_sb),
        patch("routers.connectors.getattr", side_effect=lambda obj, attr, default=None: "dummy_val" if "META" in attr else default),
        patch("routers.connectors._exchange_code_for_token", new_callable=AsyncMock, return_value={"access_token": "EAA_test", "expires_in": 3600}),
        patch("routers.connectors._verify_waba_and_phone", new_callable=AsyncMock, return_value={"display_phone_number": "+971501234567", "verified_name": "Test Real Estate", "platform_type": "CLOUD_API", "is_pin_enabled": True}),
        patch("routers.connectors._register_phone_number_if_needed", new_callable=AsyncMock, return_value=(True, False, False)),
        patch("routers.connectors._subscribe_app_to_waba", new_callable=AsyncMock, return_value=True),
        patch("routers.connectors._bg_auto_create_templates", new_callable=AsyncMock),
    ):
        client = TestClient(app)
        payload = {
            "code": "auth-code",
            "waba_id": "waba-123",
            "phone_number_id": "phone-id-123",
        }
        resp = client.post("/connectors/meta-esu/callback", json=payload)
        assert resp.status_code == 200
        assert len(updated_payloads) == 1
        written = updated_payloads[0]
        assert written["provider"] == "meta"
        assert written["registration_pin_enc"] is None  # already registered, no new pin stored
        assert "app_secret" not in written["metadata"]
    app.dependency_overrides.clear()


def test_upsert_unique_violation_returns_409(mock_auth_user):
    """Item 10: Catch unique constraint violation on insert/update and return 409."""
    app.dependency_overrides[verify_token] = lambda: mock_auth_user
    mock_sb = MagicMock()
    mock_comm_table = MagicMock()
    mock_comm_table.select.return_value.eq.return_value.neq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    mock_comm_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    # Simulate Postgres unique violation error
    mock_comm_table.insert.side_effect = Exception("duplicate key value violates unique constraint 'uq_comm_accounts_phone_number_id'")
    mock_sb.table.return_value = mock_comm_table

    with (
        patch("routers.connectors.get_supabase", return_value=mock_sb),
        patch("routers.connectors.getattr", side_effect=lambda obj, attr, default=None: "dummy_val" if "META" in attr else default),
        patch("routers.connectors._exchange_code_for_token", new_callable=AsyncMock, return_value={"access_token": "EAA_test", "expires_in": 3600}),
        patch("routers.connectors._verify_waba_and_phone", new_callable=AsyncMock, return_value={"display_phone_number": "+971501234567", "verified_name": "Test Real Estate"}),
        patch("routers.connectors._register_phone_number_if_needed", new_callable=AsyncMock, return_value=(True, False, True)),
        patch("routers.connectors._subscribe_app_to_waba", new_callable=AsyncMock, return_value=True),
    ):
        client = TestClient(app)
        payload = {
            "code": "auth-code",
            "waba_id": "waba-123",
            "phone_number_id": "phone-id-123",
        }
        resp = client.post("/connectors/meta-esu/callback", json=payload)
        assert resp.status_code == 409
        assert "already registered" in str(resp.json())
    app.dependency_overrides.clear()


# ─── REVIEW ROUND 3 TESTS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_coexistence_detection_and_pin_rules():
    """
    Item 1: Coexistence detection per Meta official docs:
    - Normal number (platform_type NOT_APPLICABLE, is_on_biz_app False) -> registers, PIN stored
    - Coexistence number (platform_type CLOUD_API, is_on_biz_app True) -> no register call, no PIN stored
    - Already-registered Cloud API number (platform_type CLOUD_API, is_pin_enabled True, is_on_biz_app False) -> no register, no PIN
    - Register failure -> returns ok=False
    """
    # 1. Normal number: platform_type NOT_APPLICABLE, is_on_biz_app False
    normal_phone = {
        "platform_type": "NOT_APPLICABLE",
        "is_on_biz_app": False,
        "is_pin_enabled": False,
    }
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"success": True})
        reg_ok, is_coex, pin_stored = await _register_phone_number_if_needed(
            "p-123", "token", "waba-1", normal_phone, corr_id="corr-test"
        )
        assert reg_ok is True
        assert is_coex is False
        assert pin_stored is True
        assert mock_post.called

    # 2. Coexistence number: platform_type CLOUD_API, is_on_biz_app True
    coex_phone = {
        "platform_type": "CLOUD_API",
        "is_on_biz_app": True,
        "is_pin_enabled": False,
    }
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        reg_ok, is_coex, pin_stored = await _register_phone_number_if_needed(
            "p-123", "token", "waba-1", coex_phone, corr_id="corr-test"
        )
        assert reg_ok is True
        assert is_coex is True
        assert pin_stored is False
        assert not mock_post.called  # skipped registration

    # 3. Already-registered Cloud API number: platform_type CLOUD_API, is_pin_enabled True, is_on_biz_app False
    registered_phone = {
        "platform_type": "CLOUD_API",
        "is_on_biz_app": False,
        "is_pin_enabled": True,
    }
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        reg_ok, is_coex, pin_stored = await _register_phone_number_if_needed(
            "p-123", "token", "waba-1", registered_phone, corr_id="corr-test"
        )
        assert reg_ok is True
        assert is_coex is False
        assert pin_stored is False
        assert not mock_post.called

    # 4. Register failure
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MagicMock(
            status_code=400,
            json=lambda: {"error": {"message": "Invalid PIN format"}},
            text="Invalid PIN format"
        )
        reg_ok, is_coex, pin_stored = await _register_phone_number_if_needed(
            "p-123", "token", "waba-1", normal_phone, corr_id="corr-test"
        )
        assert reg_ok is False
        assert is_coex is False
        assert pin_stored is False


@pytest.mark.asyncio
async def test_verify_token_waba_permission_fails_closed_ambiguous():
    """Item 3: debug_token with empty target_ids or missing WABA must fail closed (raise 403)."""
    from routers.connectors import _verify_token_waba_permission
    from fastapi import HTTPException
    from config import settings

    # Case A: target_ids is empty
    debug_data_empty = {
        "data": {
            "is_valid": True,
            "granular_scopes": [
                {"scope": "whatsapp_business_management", "target_ids": []}
            ]
        }
    }
    with patch.object(settings, "META_APP_ID", "app-123"), \
         patch.object(settings, "META_APP_SECRET", "app-sec-456"), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: debug_data_empty)
        with pytest.raises(HTTPException) as excinfo:
            await _verify_token_waba_permission("debug-token", "waba-wanted-123", "corr-test")
        assert excinfo.value.status_code == 403
        assert "does not grant access" in str(excinfo.value.detail)

    # Case B: target_ids contains a different WABA, not the requested one
    debug_data_different = {
        "data": {
            "is_valid": True,
            "granular_scopes": [
                {"scope": "whatsapp_business_management", "target_ids": ["waba-other-999"]}
            ]
        }
    }
    with patch.object(settings, "META_APP_ID", "app-123"), \
         patch.object(settings, "META_APP_SECRET", "app-sec-456"), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: debug_data_different)
        with pytest.raises(HTTPException) as excinfo:
            await _verify_token_waba_permission("debug-token", "waba-wanted-123", "corr-test")
        assert excinfo.value.status_code == 403
        assert "does not grant access" in str(excinfo.value.detail)


def test_agency_default_protection_requires_confirmation(mock_manager_user):
    """Item 4: Legacy agency default protection requires confirm_replace_default=True, else 409."""
    app.dependency_overrides[verify_token] = lambda: mock_manager_user

    mock_sb = MagicMock()
    mock_comm_table = MagicMock()

    # Active agency default row with provider='twilio'
    existing_twilio_default = [
        {"id": "acc-twilio-def", "agency_id": "agency-uuid-1", "agent_id": None, "provider": "twilio", "status": "active"}
    ]

    mock_exec = MagicMock(data=existing_twilio_default)
    mock_comm_table.select.return_value.eq.return_value.is_.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = mock_exec
    mock_comm_table.select.return_value.eq.return_value.is_.return_value.eq.return_value.limit.return_value.execute.return_value = mock_exec
    mock_sb.table.return_value = mock_comm_table

    with (
        patch("routers.connectors.get_supabase", return_value=mock_sb),
        patch("routers.connectors.getattr", side_effect=lambda obj, attr, default=None: "dummy_val" if "META" in attr else default),
        patch("routers.connectors._exchange_code_for_token", new_callable=AsyncMock, return_value={"access_token": "EAA_test", "expires_in": 3600}),
        patch("routers.connectors._verify_waba_and_phone", new_callable=AsyncMock, return_value={"display_phone_number": "+971501234567", "verified_name": "Test Real Estate"}),
    ):
        client = TestClient(app)
        # Attempt connecting without confirm_replace_default -> must get 409
        payload = {
            "code": "auth-code",
            "waba_id": "waba-123",
            "phone_number_id": "phone-id-123",
            # agent_id omitted -> connecting as agency default
        }
        resp = client.post("/connectors/meta-esu/callback", json=payload)
        assert resp.status_code == 409
        assert "twilio" in str(resp.json())
        assert "confirm_replace_default=true" in str(resp.json())

        # Now include confirm_replace_default=True -> allows replacement
        with (
            patch("routers.connectors._register_phone_number_if_needed", new_callable=AsyncMock, return_value=(True, False, True)),
            patch("routers.connectors._subscribe_app_to_waba", new_callable=AsyncMock, return_value=True),
            patch("routers.connectors._bg_auto_create_templates", new_callable=AsyncMock),
        ):
            mock_comm_table.select.return_value.eq.return_value.neq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
            mock_comm_table.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{"id": "acc-twilio-def"}])

            payload["confirm_replace_default"] = True
            resp2 = client.post("/connectors/meta-esu/callback", json=payload)
            assert resp2.status_code == 200

    app.dependency_overrides.clear()


def test_registration_failure_records_state_and_returns_error(mock_auth_user):
    """Item 5: When phone registration fails, records status='pending' and state='registration_failed'."""
    app.dependency_overrides[verify_token] = lambda: mock_auth_user

    mock_sb = MagicMock()
    mock_comm_table = MagicMock()
    mock_comm_table.select.return_value.eq.return_value.neq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    mock_comm_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

    saved_rows = []
    def mock_save(data, **kwargs):
        saved_rows.append(data)
        m = MagicMock()
        m.execute.return_value = MagicMock(data=[data])
        return m
    mock_comm_table.insert.side_effect = mock_save
    mock_comm_table.update.side_effect = mock_save
    mock_sb.table.return_value = mock_comm_table

    with (
        patch("routers.connectors.get_supabase", return_value=mock_sb),
        patch("routers.connectors.getattr", side_effect=lambda obj, attr, default=None: "dummy_val" if "META" in attr else default),
        patch("routers.connectors._exchange_code_for_token", new_callable=AsyncMock, return_value={"access_token": "EAA_test", "expires_in": 3600}),
        patch("routers.connectors._verify_waba_and_phone", new_callable=AsyncMock, return_value={"display_phone_number": "+971501234567", "verified_name": "Test Real Estate"}),
        patch("routers.connectors._register_phone_number_if_needed", new_callable=AsyncMock, return_value=(False, False, False)),  # Fails!
    ):
        client = TestClient(app)
        payload = {
            "code": "auth-code",
            "waba_id": "waba-123",
            "phone_number_id": "phone-id-123",
        }
        resp = client.post("/connectors/meta-esu/callback", json=payload)
        assert resp.status_code == 400
        assert "Phone number registration with WhatsApp provider failed" in str(resp.json())

        assert len(saved_rows) == 1
        recorded = saved_rows[0]
        assert recorded["status"] == "pending"
        assert recorded["meta_onboarding_state"] == "registration_failed"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_scheduler_retry_waba_webhook_subscriptions():
    """Item 5: Scheduler retry job sweeps accounts with webhook_subscription_failed and retries."""
    from services.scheduler import retry_waba_webhook_subscriptions_job
    from config import settings

    mock_sb = MagicMock()
    failed_accounts = [
        {
            "id": "acc-fail-sub-1",
            "external_account_id": "waba-111",
            "access_token_enc": "enc_token_val",
        }
    ]

    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.neq.return_value.execute.return_value = MagicMock(data=failed_accounts)
    updated_records = []
    def mock_update(payload):
        updated_records.append(payload)
        m = MagicMock()
        m.eq.return_value.execute.return_value = MagicMock(data=[])
        return m
    mock_table.update.side_effect = mock_update
    mock_sb.table.return_value = mock_table

    # Test with ENABLE_META_WHATSAPP=True
    with (
        patch.object(settings, "ENABLE_META_WHATSAPP", True),
        patch("services.scheduler.get_supabase", return_value=mock_sb),
        patch("utils.crypto.decrypt_token", return_value="decrypted-system-token"),
        patch("routers.connectors._subscribe_app_to_waba", new_callable=AsyncMock, return_value=True),
    ):
        await retry_waba_webhook_subscriptions_job()
        assert len(updated_records) == 1
        assert updated_records[0]["meta_onboarding_state"] == "embedded_signup"

    # Test with ENABLE_META_WHATSAPP=False (flag respected, no work performed)
    updated_records.clear()
    with (
        patch.object(settings, "ENABLE_META_WHATSAPP", False),
        patch("services.scheduler.get_supabase", return_value=mock_sb),
    ):
        await retry_waba_webhook_subscriptions_job()
        assert len(updated_records) == 0


def test_disconnect_calls_unsubscribe_and_does_not_disconnect_twilio_row(mock_auth_user):
    """
    Item 3: Verify meta-esu/disconnect calls WABA unsubscribe with decrypted raw_tok,
    and never disconnects a legacy Twilio row.
    """
    app.dependency_overrides[verify_token] = lambda: mock_auth_user
    mock_sb = MagicMock()

    # Case A: Only Twilio row exists
    twilio_table = MagicMock()
    twilio_table.select.return_value = twilio_table
    twilio_table.eq.return_value = twilio_table
    twilio_table.is_.return_value = twilio_table
    twilio_table.order.return_value = twilio_table
    twilio_table.limit.return_value = twilio_table
    twilio_table.execute.return_value = MagicMock(data=[])
    mock_sb.table.return_value = twilio_table

    with (
        patch("routers.connectors.get_supabase", return_value=mock_sb),
        patch("httpx.AsyncClient.delete", new_callable=AsyncMock) as mock_delete,
    ):
        client = TestClient(app)
        resp = client.post("/connectors/meta-esu/disconnect")
        assert resp.status_code == 200
        # No Meta account was found, so unsubscribe was not called and no update executed
        mock_delete.assert_not_called()
        twilio_table.update.assert_not_called()

    # Case B: Meta row exists with access_token_enc
    meta_row = {
        "id": "meta-acc-1",
        "provider": "meta",
        "external_account_id": "waba-999",
        "access_token_enc": "fake_enc_token",
    }
    meta_table = MagicMock()
    meta_table.select.return_value = meta_table
    meta_table.eq.return_value = meta_table
    meta_table.is_.return_value = meta_table
    meta_table.order.return_value = meta_table
    meta_table.limit.return_value = meta_table
    meta_table.execute.return_value = MagicMock(data=[meta_row])
    update_payloads = []
    def record_update(payload):
        update_payloads.append(payload)
        m = MagicMock()
        m.eq.return_value = m
        m.execute.return_value = MagicMock(data=[])
        return m
    meta_table.update.side_effect = record_update
    mock_sb.table.return_value = meta_table

    mock_del_resp = MagicMock(status_code=200)
    with (
        patch("routers.connectors.get_supabase", return_value=mock_sb),
        patch("routers.connectors.decrypt_token", return_value="plain-meta-token"),
        patch("httpx.AsyncClient.delete", new_callable=AsyncMock, return_value=mock_del_resp) as mock_delete,
    ):
        client = TestClient(app)
        resp = client.post("/connectors/meta-esu/disconnect")
        assert resp.status_code == 200
        # Unsubscribe was called on the WABA endpoint
        mock_delete.assert_called_once()
        assert "waba-999/subscribed_apps" in str(mock_delete.call_args)
        assert len(update_payloads) == 1
        assert update_payloads[0]["status"] == "disconnected"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_registration_failure_keeps_active_row_intact():
    """
    Item 3: Registration failure must never overwrite an existing active row.
    Failure is recorded only in metadata.
    """
    from routers.connectors import meta_embedded_signup_callback, EmbeddedSignupCallbackRequest
    from fastapi import HTTPException, BackgroundTasks

    mock_user = {"id": "agent-1", "agency_id": "agency-1", "role": "agent"}
    req = EmbeddedSignupCallbackRequest(
        code="oauth-code-123",
        phone_number_id="phone-num-id-1",
        waba_id="waba-123",
    )

    mock_sb = MagicMock()
    existing_active = [{
        "id": "comm-acc-active-1",
        "agency_id": "agency-1",
        "agent_id": "agent-1",
        "status": "active",
        "metadata": {"waba_name": "Active Agency"},
    }]
    table_mock = MagicMock()
    table_mock.select.return_value = table_mock
    table_mock.eq.return_value = table_mock
    table_mock.neq.return_value = table_mock
    table_mock.is_.return_value = table_mock
    table_mock.limit.return_value = table_mock
    table_mock.execute.return_value = MagicMock(data=existing_active)
    
    metadata_updates = []
    def record_update(payload):
        metadata_updates.append(payload)
        m = MagicMock()
        m.eq.return_value.execute.return_value = MagicMock(data=[])
        return m
    table_mock.update.side_effect = record_update
    mock_sb.table.return_value = table_mock

    with (
        patch("routers.connectors.get_supabase", return_value=mock_sb),
        patch("routers.connectors._exchange_code_for_token", new_callable=AsyncMock, return_value={"access_token": "tok-1"}),
        patch("routers.connectors._verify_waba_and_phone", new_callable=AsyncMock, return_value={
            "id": "phone-num-id-1", "display_phone_number": "+971501112233", "verified_name": "Active Agency", "platform_type": "NOT_APPLICABLE"
        }),
        # Registration fails
        patch("routers.connectors._register_phone_number_if_needed", new_callable=AsyncMock, return_value=(False, False, False)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await meta_embedded_signup_callback(body=req, background_tasks=BackgroundTasks(), current_user=mock_user)
        assert exc_info.value.status_code == 400

    # Active row status was NOT overwritten to pending
    assert len(metadata_updates) == 1
    assert "status" not in metadata_updates[0]
    assert "metadata" in metadata_updates[0]
    assert "last_registration_failure" in metadata_updates[0]["metadata"]


@pytest.mark.asyncio
async def test_registration_failure_inserts_pending_row_when_no_row_exists():
    """
    Item 3: Registration failure inserts a pending row when no prior row exists.
    """
    from routers.connectors import meta_embedded_signup_callback, EmbeddedSignupCallbackRequest
    from fastapi import HTTPException, BackgroundTasks

    mock_user = {"id": "agent-1", "agency_id": "agency-1", "role": "agent"}
    req = EmbeddedSignupCallbackRequest(
        code="oauth-code-123",
        phone_number_id="phone-num-id-1",
        waba_id="waba-123",
    )

    mock_sb = MagicMock()
    table_mock = MagicMock()
    table_mock.select.return_value = table_mock
    table_mock.eq.return_value = table_mock
    table_mock.neq.return_value = table_mock
    table_mock.is_.return_value = table_mock
    table_mock.limit.return_value = table_mock
    # No existing row
    table_mock.execute.return_value = MagicMock(data=[])
    
    inserted_rows = []
    def record_insert(payload):
        inserted_rows.append(payload)
        m = MagicMock()
        m.execute.return_value = MagicMock(data=[payload])
        return m
    table_mock.insert.side_effect = record_insert
    mock_sb.table.return_value = table_mock

    with (
        patch("routers.connectors.get_supabase", return_value=mock_sb),
        patch("routers.connectors._exchange_code_for_token", new_callable=AsyncMock, return_value={"access_token": "tok-1"}),
        patch("routers.connectors._verify_waba_and_phone", new_callable=AsyncMock, return_value={
            "id": "phone-num-id-1", "display_phone_number": "+971501112233", "verified_name": "Agency", "platform_type": "NOT_APPLICABLE"
        }),
        # Registration fails
        patch("routers.connectors._register_phone_number_if_needed", new_callable=AsyncMock, return_value=(False, False, False)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await meta_embedded_signup_callback(body=req, background_tasks=BackgroundTasks(), current_user=mock_user)
        assert exc_info.value.status_code == 400

    # Pending row was inserted
    assert len(inserted_rows) == 1
    assert inserted_rows[0]["status"] == "pending"


def test_status_endpoint_returns_real_provider(mock_auth_user):
    """
    Item 3: Verify GET /connectors/meta-esu/status returns the real provider of the row.
    """
    app.dependency_overrides[verify_token] = lambda: mock_auth_user
    mock_sb = MagicMock()

    # Case A: Provider is twilio
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.is_.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.execute.return_value = MagicMock(
        data=[{
            "id": "acc-twilio-1",
            "provider": "twilio",
            "status": "active",
            "phone_number": "+971501234567",
            "phone_number_id": "pn-1",
            "external_account_id": "waba-1",
            "is_coexistence": False,
            "meta_onboarding_state": "registered",
            "connected_at": "2026-09-19T00:00:00Z",
            "token_expires_at": None,
            "metadata": {},
        }]
    )
    mock_sb.table.return_value = mock_table

    with patch("routers.connectors.get_supabase", return_value=mock_sb):
        client = TestClient(app)
        resp = client.get("/connectors/meta-esu/status")
        assert resp.status_code == 200
        assert resp.json()["data"]["provider"] == "twilio"

    app.dependency_overrides.clear()


