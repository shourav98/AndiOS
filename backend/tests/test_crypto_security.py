"""
Tests for crypto.py:
- is_encrypted detects \\x hex of base64 Fernet token starting with gAAAAA
- encrypt_token raises on failure / empty / double encryption
- invalid or missing key in TOKEN_ENCRYPTION_KEY is a hard failure
- ALLOW_LEGACY_PLAINTEXT_TOKENS flag enforcement
- legacy plaintext stored in BYTEA
- logging legacy read once per account (not once per call)
"""
import os
from unittest.mock import MagicMock, patch, AsyncMock
import pytest
from cryptography.fernet import Fernet
from utils import crypto



def test_is_encrypted_bytea_hex():
    """Verify is_encrypted decodes \\x hex and checks for gAAAAA prefix."""
    raw_fernet = Fernet.generate_key()
    f = Fernet(raw_fernet)
    token = "EAA_test_secret_123"
    cipher_bytes = f.encrypt(token.encode("utf-8"))
    assert cipher_bytes.startswith(b"gAAAAA")

    bytea_hex = "\\x" + cipher_bytes.hex()
    assert crypto.is_encrypted(bytea_hex) is True
    assert crypto.is_encrypted(bytea_hex.upper()) is True
    assert crypto.is_encrypted(cipher_bytes) is True
    assert crypto.is_encrypted(cipher_bytes.decode("utf-8")) is True

    # Plaintext starting with something else
    assert crypto.is_encrypted("EAA_plaintext_token") is False
    assert crypto.is_encrypted("\\x454141") is False  # hex for "EAA"
    assert crypto.is_encrypted("") is False
    assert crypto.is_encrypted(None) is False


def test_encrypt_token_double_encryption_guard():
    """Verify encrypt_token raises ValueError on empty or already-encrypted token."""
    with pytest.raises(ValueError, match="empty"):
        crypto.encrypt_token("")

    # Encrypt a token
    encrypted = crypto.encrypt_token("EAA_valid_token")
    assert encrypted.startswith("\\x")

    # Double encryption must raise ValueError
    with pytest.raises(ValueError, match="already-encrypted"):
        crypto.encrypt_token(encrypted)


def test_missing_or_invalid_key_hard_failure(monkeypatch):
    """Verify missing or invalid key causes a hard failure (no silent dev fallback)."""
    crypto._get_multi_fernet.cache_clear()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "")
    from config import settings
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")

    with pytest.raises(RuntimeError, match="must be configured"):
        crypto._get_multi_fernet()

    # Invalid key
    crypto._get_multi_fernet.cache_clear()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    with pytest.raises(ValueError, match="Invalid key"):
        crypto._get_multi_fernet()

    # Restore valid key
    crypto._get_multi_fernet.cache_clear()


def test_allow_legacy_plaintext_tokens_flag(monkeypatch):
    """Verify ALLOW_LEGACY_PLAINTEXT_TOKENS=False rejects unencrypted tokens."""
    token = "EAA_legacy_token_12345"

    # Default True allows it
    monkeypatch.setenv("ALLOW_LEGACY_PLAINTEXT_TOKENS", "true")
    assert crypto.decrypt_token(token) == token

    # False raises RuntimeError
    monkeypatch.setenv("ALLOW_LEGACY_PLAINTEXT_TOKENS", "false")
    with pytest.raises(RuntimeError, match="not allowed"):
        crypto.decrypt_token(token)


def test_legacy_plaintext_in_bytea(monkeypatch):
    """Verify decrypt_token decodes legacy plaintext stored in BYTEA."""
    monkeypatch.setenv("ALLOW_LEGACY_PLAINTEXT_TOKENS", "true")
    plaintext = "EAA_test_in_bytea"
    bytea_hex = "\\x" + plaintext.encode("utf-8").hex()

    decrypted = crypto.decrypt_token(bytea_hex)
    assert decrypted == plaintext


def test_legacy_plaintext_logged_once_per_account(caplog, monkeypatch):
    """Verify legacy plaintext read is logged once per account ID, not on every call."""
    import logging
    crypto._LOGGED_LEGACY_ACCOUNTS.clear()
    monkeypatch.setenv("ALLOW_LEGACY_PLAINTEXT_TOKENS", "true")
    caplog.set_level(logging.WARNING)

    token = "EAA_legacy_repeat_test"
    account_id = "acc-unique-uuid-999"

    # Call 1: should log
    val1 = crypto.decrypt_token(token, account_id=account_id)
    assert val1 == token
    assert any(account_id in r.message for r in caplog.records)

    records_count = len(caplog.records)

    # Call 2 with same account_id: should NOT log again
    val2 = crypto.decrypt_token(token, account_id=account_id)
    assert val2 == token
    assert len(caplog.records) == records_count


def test_backfill_idempotency_running_twice():
    """Verify scripts/backfill_encrypt_tokens.py is idempotent when run twice."""
    from scripts.backfill_encrypt_tokens import run_backfill
    from unittest.mock import MagicMock

    # Simulated in-memory DB state
    db_rows = [
        {
            "id": "acc-1",
            "agency_id": "agency-1",
            "provider": "meta",
            "access_token": "EAA_unencrypted_secret_1",
            "access_token_enc": None,
        },
        {
            "id": "acc-2",
            "agency_id": "agency-1",
            "provider": "twilio",
            "access_token": "AC_unencrypted_secret_2",
            "access_token_enc": None,
        },
    ]

    def mock_table(table_name):
        mock_tbl = MagicMock()

        def mock_select(*args, **kwargs):
            mock_sel = MagicMock()
            mock_exec = MagicMock(data=list(db_rows))
            mock_sel.execute.return_value = mock_exec
            mock_sel.order.return_value = mock_sel
            mock_sel.range.return_value.execute.return_value = mock_exec
            return mock_sel


        def mock_update(payload):
            mock_up = MagicMock()
            def mock_eq(col, val):
                mock_exec = MagicMock()
                for r in db_rows:
                    if r.get(col) == val:
                        r.update(payload)
                mock_exec.execute.return_value = MagicMock(data=[])
                return mock_exec
            mock_up.eq.side_effect = mock_eq
            return mock_up

        mock_tbl.select.side_effect = mock_select
        mock_tbl.update.side_effect = mock_update
        return mock_tbl

    mock_sb = MagicMock()
    mock_sb.table.side_effect = mock_table

    # Run 1: Should encrypt unencrypted tokens
    res1 = run_backfill(execute=True, sb_client=mock_sb)
    assert res1["unencrypted_found"] == 2
    assert res1["updated"] == 2
    assert res1["verified"] == 2
    assert res1["errors"] == 0

    # Verify rows in mock DB are now encrypted and access_token is None
    for r in db_rows:
        assert crypto.is_encrypted(r["access_token_enc"]) is True
        assert r["access_token"] is None

    # Run 2: Should find 0 unencrypted tokens, make 0 updates
    res2 = run_backfill(execute=True, sb_client=mock_sb)
    assert res2["total"] == 2
    assert res2["already_encrypted"] == 2
    assert res2["unencrypted_found"] == 0
    assert res2["updated"] == 0
    assert res2["errors"] == 0


def test_backfill_bytea_hex_row_and_row_shapes(caplog):
    """
    Item 2a, 2b, 2c:
    - 2a: Legacy plaintext stored in BYTEA arrives as '\\x...' hex of ASCII.
      Decodes BEFORE encrypting; after backfill, decrypt_token returns original token exactly.
    - 2b: Row shape A (with access_token key) and Row shape B (schema_v9 without access_token key).
      Payload only includes 'access_token': None if key exists in fetched row.
    - 2c: Never logs token prefix/suffix; only length and account id.
    """
    import logging
    from scripts.backfill_encrypt_tokens import run_backfill
    from unittest.mock import MagicMock

    caplog.set_level(logging.DEBUG)

    original_plaintext = "EAA_test_secret_token_value_12345"
    bytea_hex_of_ascii = "\\x" + original_plaintext.encode("utf-8").hex()

    # Row 1: Shape A with access_token column and BYTEA-hex plaintext
    row_shape_a = {
        "id": "acc-shape-a",
        "agency_id": "agency-1",
        "provider": "meta",
        "access_token": bytea_hex_of_ascii,
        "access_token_enc": None,
    }
    # Row 2: Shape B without access_token column in fetched row (schema_v9 / safe view)
    row_shape_b = {
        "id": "acc-shape-b",
        "agency_id": "agency-1",
        "provider": "meta",
        "access_token_enc": "unencrypted_ascii_token_67890",
    }

    db_rows = [row_shape_a, row_shape_b]
    captured_updates = {}

    def mock_table(table_name):
        mock_tbl = MagicMock()
        def mock_select(*args, **kwargs):
            mock_sel = MagicMock()
            mock_exec = MagicMock(data=list(db_rows))
            mock_sel.execute.return_value = mock_exec
            mock_sel.order.return_value = mock_sel
            mock_sel.range.return_value.execute.return_value = mock_exec
            return mock_sel

        def mock_update(payload):
            mock_up = MagicMock()
            def mock_eq(col, val):
                captured_updates[val] = dict(payload)
                for r in db_rows:
                    if r.get(col) == val:
                        r.update(payload)
                mock_exec = MagicMock()
                mock_exec.execute.return_value = MagicMock(data=[])
                return mock_exec
            mock_up.eq.side_effect = mock_eq
            return mock_up
        mock_tbl.select.side_effect = mock_select
        mock_tbl.update.side_effect = mock_update
        return mock_tbl

    mock_sb = MagicMock()
    mock_sb.table.side_effect = mock_table

    res = run_backfill(execute=True, sb_client=mock_sb)
    assert res["unencrypted_found"] == 2
    assert res["updated"] == 2
    assert res["verified"] == 2
    assert res["errors"] == 0

    # 2a verification: decrypt_token returns original token exactly, NOT hex string
    shape_a_enc = row_shape_a["access_token_enc"]
    assert crypto.is_encrypted(shape_a_enc) is True
    decrypted_token = crypto.decrypt_token(shape_a_enc)
    assert decrypted_token == original_plaintext

    # 2b verification:
    # Shape A update includes 'access_token': None because key was present
    assert "access_token" in captured_updates["acc-shape-a"]
    assert captured_updates["acc-shape-a"]["access_token"] is None

    # Shape B update does NOT include 'access_token' key
    assert "access_token" not in captured_updates["acc-shape-b"]

    # 2c verification: Check caplog — token prefix/suffix never logged
    for record in caplog.records:
        msg = record.message
        assert original_plaintext not in msg
        assert "EAA_" not in msg
        assert "12345" not in msg
        assert "67890" not in msg


def test_crypto_is_encrypted_rejects_raw_bytes_80():
    """Item 6: crypto.is_encrypted must reject b'\\x80' (removed acceptance)."""
    assert crypto.is_encrypted(b"\x80") is False
    assert crypto.is_encrypted(b"\x80abc") is False


def test_decrypt_token_typed_exception_on_failure(monkeypatch):
    """Item 6: on decrypt failure raise typed TokenDecryptionError (not empty string)."""
    from utils.crypto import TokenDecryptionError

    monkeypatch.setenv("ALLOW_LEGACY_PLAINTEXT_TOKENS", "false")
    # Corrupted ciphertext
    corrupted_enc = "\\x" + b"gAAAAABinvalidciphertextformat".hex()
    with pytest.raises(TokenDecryptionError):
        crypto.decrypt_token(corrupted_enc)


def test_validate_token_encryption_key_production_fail_fast(monkeypatch):
    """Item 6: validate_token_encryption_key fails fast in production if key is missing or invalid."""
    from config import settings
    from main import _validate_production_config

    # In development, missing key produces warning but doesn't raise
    crypto._get_multi_fernet.cache_clear()
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "")
    _validate_production_config()  # Should not raise in development

    # In production, missing key raises RuntimeError
    crypto._get_multi_fernet.cache_clear()
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "")
    with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY startup validation failed"):
        _validate_production_config()

    # In production, invalid fernet key raises RuntimeError
    crypto._get_multi_fernet.cache_clear()
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "not-valid-base64")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "not-valid-base64")
    with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY startup validation failed"):
        _validate_production_config()

    crypto._get_multi_fernet.cache_clear()


@pytest.mark.asyncio
async def test_provider_factory_decrypt_failure_marks_token_error_no_fallback():
    """
    Item 6: When decrypt fails in provider_factory:
    - account status is updated to 'token_error'
    - TokenDecryptionError is raised
    - does NOT silently fall back to global WHATSAPP_API_KEY
    """
    from unittest.mock import patch, MagicMock
    from services.communication.provider_factory import get_whatsapp_provider_for_agency
    from utils.crypto import TokenDecryptionError

    mock_account = {
        "id": "acc-corrupted-token",
        "agency_id": "agency-test-1",
        "agent_id": None,
        "provider": "meta",
        "phone_number_id": "phone-111",
        "access_token_enc": "\\x" + b"gAAAAABcorrupted".hex(),
        "status": "active",
    }

    mock_sb = MagicMock()
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.is_.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[mock_account])

    status_updates = []
    def mock_update(payload):
        status_updates.append(payload)
        m = MagicMock()
        m.eq.return_value = m
        m.execute.return_value = MagicMock(data=[])
        return m
    mock_table.update.side_effect = mock_update
    mock_sb.table.return_value = mock_table

    with (
        patch("database.supabase_client.get_supabase", return_value=mock_sb),
        patch("utils.crypto.decrypt_token", side_effect=TokenDecryptionError("Decrypt failed")),
    ):
        with pytest.raises(TokenDecryptionError):
            await get_whatsapp_provider_for_agency("agency-test-1")

        # Item 2: Decrypt failure must NOT mutate status; records non-destructive marker in metadata
        assert len(status_updates) == 1
        assert "status" not in status_updates[0]
        assert "metadata" in status_updates[0]
        assert "token_error_at" in status_updates[0]["metadata"]


def test_settings_repr_contains_no_secrets():
    """Item 0a: Verify repr(settings) and str(settings) redact all credentials and secrets."""
    from config import settings
    repr_str = repr(settings)
    str_str = str(settings)

    sensitive_keywords = (
        "KEY", "SECRET", "TOKEN", "PASSWORD", "AUTH", "CREDENTIAL",
        "PRIVATE", "SID", "URL", "URI"
    )
    # Check that every sensitive field in settings is redacted
    for field_name in type(settings).model_fields.keys():
        if any(kw in field_name.upper() for kw in sensitive_keywords):
            val = getattr(settings, field_name, "")
            # Ensure the raw secret value does not appear in repr or str
            if isinstance(val, str) and len(val) > 4:
                assert val not in repr_str, f"Credential {field_name} leaked in repr(settings)"
                assert val not in str_str, f"Credential {field_name} leaked in str(settings)"
            assert f"{field_name}='***REDACTED***'" in repr_str
            assert f"{field_name}='***REDACTED***'" in str_str


@pytest.mark.asyncio
async def test_provider_factory_legacy_row_without_token_default_agency():
    """Item 2: Legacy row without token for DEFAULT_AGENCY_ID works via environment key fallback."""
    from services.communication.provider_factory import get_whatsapp_provider_for_agency
    from config import settings

    default_agency = "00000000-0000-0000-0000-000000000000"
    mock_account = {
        "id": "acc-legacy-no-token",
        "agency_id": default_agency,
        "agent_id": None,
        "provider": "meta",
        "phone_number_id": "phone-111",
        "access_token_enc": None,
        "access_token": None,
        "status": "active",
    }

    mock_sb = MagicMock()
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.is_.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[mock_account])
    mock_sb.table.return_value = mock_table

    with (
        patch("database.supabase_client.get_supabase", return_value=mock_sb),
        patch.object(settings, "DEFAULT_AGENCY_ID", default_agency),
        patch.object(settings, "WHATSAPP_API_KEY", "env_wa_api_key_test_123"),
    ):
        provider, acc = await get_whatsapp_provider_for_agency(default_agency)
        assert provider is not None
        assert acc is not None
        assert acc.access_token == "env_wa_api_key_test_123"


@pytest.mark.asyncio
async def test_provider_factory_other_agency_without_token_raises():
    """Item 2: Non-default agency with a row lacking an access token must raise ValueError."""
    from services.communication.provider_factory import get_whatsapp_provider_for_agency
    from config import settings

    other_agency = "11111111-2222-3333-4444-555555555555"
    mock_account = {
        "id": "acc-other-no-token",
        "agency_id": other_agency,
        "agent_id": None,
        "provider": "meta",
        "phone_number_id": "phone-111",
        "access_token_enc": None,
        "access_token": None,
        "status": "active",
    }

    mock_sb = MagicMock()
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.is_.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[mock_account])
    mock_sb.table.return_value = mock_table

    with (
        patch("database.supabase_client.get_supabase", return_value=mock_sb),
        patch.object(settings, "DEFAULT_AGENCY_ID", "00000000-0000-0000-0000-000000000000"),
    ):
        with pytest.raises(ValueError, match="no valid access token"):
            await get_whatsapp_provider_for_agency(other_agency)


@pytest.mark.asyncio
async def test_provider_factory_other_agency_no_account_raises():
    """Item 2: Non-default agency with no account row must raise ValueError (never send from shared)."""
    from services.communication.provider_factory import get_whatsapp_provider_for_agency
    from config import settings

    other_agency = "11111111-2222-3333-4444-555555555555"
    mock_sb = MagicMock()
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.is_.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])
    mock_sb.table.return_value = mock_table

    with (
        patch("database.supabase_client.get_supabase", return_value=mock_sb),
        patch.object(settings, "DEFAULT_AGENCY_ID", "00000000-0000-0000-0000-000000000000"),
    ):
        with pytest.raises(ValueError, match="No active WhatsApp communication account found"):
            await get_whatsapp_provider_for_agency(other_agency)


@pytest.mark.asyncio
async def test_provider_factory_db_error_raises_and_never_falls_back():
    """Item 2: Database query errors must raise RuntimeError and never fall back to platform default."""
    from services.communication.provider_factory import get_whatsapp_provider_for_agency

    mock_sb = MagicMock()
    mock_sb.table.side_effect = RuntimeError("PostgREST connection failed")

    with patch("database.supabase_client.get_supabase", return_value=mock_sb):
        with pytest.raises(RuntimeError, match="Database error resolving communication account"):
            await get_whatsapp_provider_for_agency("any-agency-id")


def test_decrypt_token_undecodable_bytes_raises():
    """Item 6: decrypt_token with undecodable UTF-8 bytes must raise TokenDecryptionError."""
    from utils.crypto import decrypt_token, TokenDecryptionError

    # Invalid UTF-8 sequence in hex BYTEA literal
    undecodable_hex = "\\xfffe0000"
    with pytest.raises(TokenDecryptionError, match="Cannot decode stored token bytes as UTF-8"):
        decrypt_token(undecodable_hex)

    # Invalid UTF-8 bytes directly
    with pytest.raises(TokenDecryptionError, match="Cannot decode stored token bytes as UTF-8"):
        decrypt_token(b"\xff\xfe\x80")


@pytest.mark.asyncio
async def test_provider_factory_legacy_dedicated_number_agency():
    """Item 4: Legacy agency with dedicated_whatsapp_number and no comm_accounts row routes via Twilio."""
    from services.communication.provider_factory import get_whatsapp_provider_for_agency

    legacy_agency_id = "22222222-3333-4444-5555-666666666666"
    mock_sb = MagicMock()
    mock_comm_table = MagicMock()
    mock_comm_table.select.return_value.eq.return_value.is_.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

    mock_agencies_table = MagicMock()
    mock_agencies_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
        data={"id": legacy_agency_id, "dedicated_whatsapp_number": "+97144001234", "whatsapp_number_status": "active"}
    )

    def table_router(tname):
        if tname == "communication_accounts":
            return mock_comm_table
        elif tname == "agencies":
            return mock_agencies_table
        return MagicMock()

    mock_sb.table.side_effect = table_router

    with patch("database.supabase_client.get_supabase", return_value=mock_sb):
        provider, acc = await get_whatsapp_provider_for_agency(legacy_agency_id)
        assert provider is not None
        assert provider.provider_name == "twilio"
        assert acc is not None
        assert acc.phone_number == "97144001234"
        assert acc.provider == "twilio"


def test_test_isolation_does_not_read_temp_env(tmp_path, monkeypatch):
    """Item 3a: Prove .env is not read under pytest; Settings with APP_ENV=test does not pick up canary."""
    from config import Settings
    temp_env = tmp_path / ".env"
    temp_env.write_text("CANARY_TEST_SECRET_VARIABLE=should_not_be_loaded\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "test")

    s = Settings()
    assert not hasattr(s, "CANARY_TEST_SECRET_VARIABLE") or getattr(s, "CANARY_TEST_SECRET_VARIABLE") != "should_not_be_loaded"




