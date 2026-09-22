"""
tests/test_provider_factory.py

Unit tests for services.communication.provider_factory.get_whatsapp_provider_for_agency.

All DB calls are mocked. No real Supabase connection. No .env values read.
Settings are monkey-patched inline per-test.

Production-mirroring test (item 4):
  Shape: provider="meta", status="active", access_token_enc=NULL, access_token=NULL
  Asserts:
    - DEFAULT agency_id succeeds via WHATSAPP_API_KEY env fallback.
    - ANY OTHER agency_id with same shape raises ValueError.
"""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock, patch

OTHER_AGENCY_ID = "other-agency-00000000"
DEFAULT_AGENCY_ID = "default-agency-00000000"
FAKE_ENV_TOKEN = "EAAtest_env_token_REDACTED"
FAKE_DECRYPTED = "EAAtest_decrypted_REDACTED"
FAKE_ACCOUNT_ID = "aaaaaaaa-0000-0000-0000-000000000001"


def _make_row(*, agency_id=DEFAULT_AGENCY_ID, provider="meta", status="active",
              access_token_enc=None, access_token=None, agent_id=None):
    return {"id": FAKE_ACCOUNT_ID, "agency_id": agency_id, "agent_id": agent_id,
            "channel": "whatsapp", "provider": provider, "status": status,
            "phone_number": "971501234567", "phone_number_id": "1234567890",
            "external_account_id": "", "access_token_enc": access_token_enc,
            "access_token": access_token, "metadata": {}}


def _sb_returning(row):
    res = MagicMock(); res.data = [row] if row is not None else []
    ch = MagicMock()
    ch.select.return_value = ch; ch.eq.return_value = ch; ch.is_.return_value = ch
    ch.limit.return_value = ch; ch.maybe_single.return_value = ch
    ch.execute.return_value = res
    sb = MagicMock(); sb.table.return_value = ch
    return sb


def _sb_empty():
    return _sb_returning(None)


def _patch_settings(*, default_agency_id=DEFAULT_AGENCY_ID, whatsapp_api_key=FAKE_ENV_TOKEN,
                    allow_platform_default_fallback=False, allow_agency_number_fallback=False,
                    whatsapp_provider="meta"):
    from config import settings
    settings.DEFAULT_AGENCY_ID = default_agency_id
    settings.WHATSAPP_API_KEY = whatsapp_api_key
    settings.ALLOW_PLATFORM_DEFAULT_FALLBACK = allow_platform_default_fallback
    settings.ALLOW_AGENCY_NUMBER_FALLBACK_FOR_AGENTS = allow_agency_number_fallback
    settings.WHATSAPP_PROVIDER = whatsapp_provider
    return settings


# ── PRODUCTION LIVE-ROW SHAPE (item 4) ────────────────────────────────────────

class TestProductionLiveRowShape:
    @pytest.mark.asyncio
    async def test_default_agency_no_token_succeeds_via_env(self):
        row = _make_row(agency_id=DEFAULT_AGENCY_ID, provider="meta",
                        access_token_enc=None, access_token=None)
        sb = _sb_returning(row)
        s = _patch_settings(default_agency_id=DEFAULT_AGENCY_ID, whatsapp_api_key=FAKE_ENV_TOKEN)
        with patch("services.communication.provider_factory.settings", s), \
             patch("database.supabase_client.get_supabase", return_value=sb), \
             patch("services.communication.meta_adapter.MetaWhatsAppAdapter"):
            from services.communication.provider_factory import get_whatsapp_provider_for_agency
            provider, account = await get_whatsapp_provider_for_agency(DEFAULT_AGENCY_ID)
        assert account is not None
        assert account.access_token == FAKE_ENV_TOKEN
        assert account.provider == "meta"
        assert account.agency_id == DEFAULT_AGENCY_ID

    @pytest.mark.asyncio
    async def test_non_default_agency_no_token_raises(self):
        row = _make_row(agency_id=OTHER_AGENCY_ID, provider="meta",
                        access_token_enc=None, access_token=None)
        sb = _sb_returning(row)
        s = _patch_settings(default_agency_id=DEFAULT_AGENCY_ID, whatsapp_api_key=FAKE_ENV_TOKEN)
        with patch("services.communication.provider_factory.settings", s), \
             patch("database.supabase_client.get_supabase", return_value=sb):
            from services.communication.provider_factory import get_whatsapp_provider_for_agency
            with pytest.raises(ValueError, match=OTHER_AGENCY_ID):
                await get_whatsapp_provider_for_agency(OTHER_AGENCY_ID)

    @pytest.mark.asyncio
    async def test_default_agency_no_token_no_env_raises(self):
        row = _make_row(agency_id=DEFAULT_AGENCY_ID, provider="meta",
                        access_token_enc=None, access_token=None)
        sb = _sb_returning(row)
        s = _patch_settings(default_agency_id=DEFAULT_AGENCY_ID, whatsapp_api_key="")
        with patch("services.communication.provider_factory.settings", s), \
             patch("database.supabase_client.get_supabase", return_value=sb):
            from services.communication.provider_factory import get_whatsapp_provider_for_agency
            with pytest.raises(ValueError, match="WHATSAPP_API_KEY"):
                await get_whatsapp_provider_for_agency(DEFAULT_AGENCY_ID)

    @pytest.mark.asyncio
    async def test_real_default_agency_no_row_at_all_succeeds_via_step3_env_fallback(self):
        """
        REGRESSION TEST FOR REAL PRODUCTION AGENCY:
        Real agency id 'd8798ea7-1b47-40be-ba3e-8e9593871393' currently has NO row in communication_accounts.
        Matches DEFAULT_AGENCY_ID in .env.
        Under Round-6 logic, get_whatsapp_provider_for_agency must succeed via Step 3
        (DEFAULT_AGENCY_ID env fallback) returning (provider, None) without raising.
        """
        REAL_PRODUCTION_DEFAULT_AGENCY_ID = "d8798ea7-1b47-40be-ba3e-8e9593871393"
        sb = _sb_empty()
        s = _patch_settings(
            default_agency_id=REAL_PRODUCTION_DEFAULT_AGENCY_ID,
            whatsapp_api_key=FAKE_ENV_TOKEN,
            allow_platform_default_fallback=False,
        )
        with patch("services.communication.provider_factory.settings", s), \
             patch("database.supabase_client.get_supabase", return_value=sb), \
             patch("services.communication.meta_adapter.MetaWhatsAppAdapter"):
            from services.communication.provider_factory import get_whatsapp_provider_for_agency
            provider, account = await get_whatsapp_provider_for_agency(REAL_PRODUCTION_DEFAULT_AGENCY_ID)
        assert account is None
        assert provider is not None


# ── TOKEN DECRYPTION ──────────────────────────────────────────────────────────

class TestTokenDecryption:
    @pytest.mark.asyncio
    async def test_encrypted_token_is_decrypted(self):
        row = _make_row(agency_id=DEFAULT_AGENCY_ID, provider="meta", access_token_enc="hexenc")
        sb = _sb_returning(row)
        s = _patch_settings(default_agency_id=DEFAULT_AGENCY_ID)
        with patch("services.communication.provider_factory.settings", s), \
             patch("database.supabase_client.get_supabase", return_value=sb), \
             patch("utils.crypto.decrypt_token", return_value=FAKE_DECRYPTED) as mock_dec, \
             patch("services.communication.meta_adapter.MetaWhatsAppAdapter"):
            from services.communication.provider_factory import get_whatsapp_provider_for_agency
            _, account = await get_whatsapp_provider_for_agency(DEFAULT_AGENCY_ID)
        mock_dec.assert_called_once_with("hexenc", account_id=FAKE_ACCOUNT_ID)
        assert account.access_token == FAKE_DECRYPTED

    @pytest.mark.asyncio
    async def test_decrypt_failure_raises_token_decryption_error(self):
        from utils.crypto import TokenDecryptionError
        row = _make_row(agency_id=DEFAULT_AGENCY_ID, provider="meta", access_token_enc="bad")
        sb = _sb_returning(row)
        s = _patch_settings(default_agency_id=DEFAULT_AGENCY_ID)
        with patch("services.communication.provider_factory.settings", s), \
             patch("database.supabase_client.get_supabase", return_value=sb), \
             patch("utils.crypto.decrypt_token", side_effect=TokenDecryptionError("bad")):
            from services.communication.provider_factory import get_whatsapp_provider_for_agency
            with pytest.raises(TokenDecryptionError):
                await get_whatsapp_provider_for_agency(DEFAULT_AGENCY_ID)


# ── TWILIO NO-TOKEN ───────────────────────────────────────────────────────────

class TestTwilioProvider:
    @pytest.mark.asyncio
    async def test_twilio_non_default_no_token_succeeds(self):
        row = _make_row(agency_id=OTHER_AGENCY_ID, provider="twilio",
                        access_token_enc=None, access_token=None)
        sb = _sb_returning(row)
        s = _patch_settings(default_agency_id=DEFAULT_AGENCY_ID)
        with patch("services.communication.provider_factory.settings", s), \
             patch("database.supabase_client.get_supabase", return_value=sb), \
             patch("services.communication.twilio_adapter.TwilioWhatsAppAdapter"):
            from services.communication.provider_factory import get_whatsapp_provider_for_agency
            _, account = await get_whatsapp_provider_for_agency(OTHER_AGENCY_ID)
        assert account is not None
        assert account.provider == "twilio"
        assert account.access_token == ""


# ── RESOLUTION ORDER ──────────────────────────────────────────────────────────

class TestResolutionOrder:
    @pytest.mark.asyncio
    async def test_agent_no_row_fallback_disabled_raises(self):
        sb = _sb_empty()
        s = _patch_settings(default_agency_id=DEFAULT_AGENCY_ID, allow_agency_number_fallback=False)
        with patch("services.communication.provider_factory.settings", s), \
             patch("database.supabase_client.get_supabase", return_value=sb):
            from services.communication.provider_factory import get_whatsapp_provider_for_agency
            with pytest.raises(ValueError, match="ALLOW_AGENCY_NUMBER_FALLBACK_FOR_AGENTS"):
                await get_whatsapp_provider_for_agency(DEFAULT_AGENCY_ID, agent_id="agent-x")

    @pytest.mark.asyncio
    async def test_default_agency_env_fallback_before_legacy_twilio(self):
        sb = _sb_empty()
        s = _patch_settings(default_agency_id=DEFAULT_AGENCY_ID, whatsapp_api_key=FAKE_ENV_TOKEN)
        with patch("services.communication.provider_factory.settings", s), \
             patch("database.supabase_client.get_supabase", return_value=sb), \
             patch("services.communication.meta_adapter.MetaWhatsAppAdapter"):
            from services.communication.provider_factory import get_whatsapp_provider_for_agency
            _, account = await get_whatsapp_provider_for_agency(DEFAULT_AGENCY_ID)
        assert account is None
        for call in sb.table.call_args_list:
            assert call.args[0] != "agencies", "agencies table queried — step 4 fired before step 3"

    @pytest.mark.asyncio
    async def test_non_default_agency_no_row_raises(self):
        sb = _sb_empty()
        s = _patch_settings(default_agency_id=DEFAULT_AGENCY_ID)
        with patch("services.communication.provider_factory.settings", s), \
             patch("database.supabase_client.get_supabase", return_value=sb):
            from services.communication.provider_factory import get_whatsapp_provider_for_agency
            with pytest.raises(ValueError, match=OTHER_AGENCY_ID):
                await get_whatsapp_provider_for_agency(OTHER_AGENCY_ID)


# ── DB ERROR ISOLATION ────────────────────────────────────────────────────────

class TestDatabaseErrorIsolation:
    @pytest.mark.asyncio
    async def test_db_error_raises_runtime_not_value_error(self):
        ch = MagicMock()
        ch.select.return_value = ch; ch.eq.return_value = ch; ch.is_.return_value = ch
        ch.limit.return_value = ch; ch.execute.side_effect = Exception("timeout")
        sb = MagicMock(); sb.table.return_value = ch
        s = _patch_settings(default_agency_id=DEFAULT_AGENCY_ID)
        with patch("services.communication.provider_factory.settings", s), \
             patch("database.supabase_client.get_supabase", return_value=sb):
            from services.communication.provider_factory import get_whatsapp_provider_for_agency
            with pytest.raises(RuntimeError, match="Database error"):
                await get_whatsapp_provider_for_agency(DEFAULT_AGENCY_ID)


# ── STARTUP CHECK TESTS ───────────────────────────────────────────────────────

class TestStartupCheck:
    def test_startup_check_logs_critical_on_mismatched_agency_with_no_token(self):
        """When an active row has provider='meta' and no token, and agency_id != DEFAULT_AGENCY_ID, logs CRITICAL."""
        from main import _check_communication_accounts_configuration

        mismatched_row = {
            "id": "acc-1",
            "agency_id": "11111111-1111-1111-1111-111111111111",
            "provider": "meta",
            "status": "active",
            "access_token_enc": None,
            "access_token": None,
        }
        sb = _sb_returning(mismatched_row)

        from config import settings
        orig_env = settings.APP_ENV
        orig_default = settings.DEFAULT_AGENCY_ID
        orig_fallback = settings.ALLOW_PLATFORM_DEFAULT_FALLBACK
        try:
            settings.APP_ENV = "production"
            settings.DEFAULT_AGENCY_ID = "d8798ea7-different-agency"
            settings.ALLOW_PLATFORM_DEFAULT_FALLBACK = False

            with patch("database.supabase_client.get_supabase", return_value=sb), \
                 patch("main.logger.critical") as mock_critical:
                _check_communication_accounts_configuration()
                mock_critical.assert_called_once()
                assert "CRITICAL CONFIGURATION MISMATCH" in mock_critical.call_args[0][0]
                assert "11111111-1111-1111-1111-111111111111" in str(mock_critical.call_args[0][1])
        finally:
            settings.APP_ENV = orig_env
            settings.DEFAULT_AGENCY_ID = orig_default
            settings.ALLOW_PLATFORM_DEFAULT_FALLBACK = orig_fallback

