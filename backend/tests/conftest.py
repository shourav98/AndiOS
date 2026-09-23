import os
import sys
import socket
from cryptography.fernet import Fernet
from unittest.mock import MagicMock, AsyncMock
import pytest

# ─── 1. FORCE FAKE ENV CREDENTIALS BEFORE APPLICATION CODE LOADS ─────────────
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: None

# Runtime generated test key (never matches any .env or hardcoded secret)
TEST_FERNET_KEY = Fernet.generate_key().decode("utf-8")

FAKE_ENV = {
    "APP_ENV": "test",
    "SUPABASE_URL": "https://fake-supabase-project.supabase.co",
    "SUPABASE_ANON_KEY": "fake-anon-key-12345",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role-key-12345",
    "OPENAI_API_KEY": "fake-openai-test-key-12345",
    "OPENAI_MODEL": "gpt-4o",
    "TOKEN_ENCRYPTION_KEY": TEST_FERNET_KEY,
    "ALLOW_LEGACY_PLAINTEXT_TOKENS": "true",
    "SECRET_KEY": "fake-test-secret-key-12345",
    "META_APP_SECRET": "fake_meta_app_secret_12345",
    "META_APP_ID": "123456789012345",
    "META_GRAPH_API_VERSION": "v26.0",
    "WHATSAPP_PROVIDER": "meta",
    "WHATSAPP_API_KEY": "fake_whatsapp_api_key",
    "WHATSAPP_PHONE_NUMBER_ID": "100000000000001",
    "WHATSAPP_VERIFY_TOKEN": "andios_verify_token",
    "WHATSAPP_WEBHOOK_TOKEN": "fake_webhook_token",
    "TWILIO_ACCOUNT_SID": "ACfake0000000000000000000000000000",
    "TWILIO_AUTH_TOKEN": "fake_twilio_auth_token",
    "TWILIO_WHATSAPP_NUMBER": "whatsapp:+14155238886",
    "ENABLE_TWILIO_PROVISIONING": "false",
    "ENABLE_VOICE_BYON": "false",
    "QUOTA_ENFORCEMENT_ENABLED": "true",
    "GOOGLE_CLIENT_ID": "fake-google-client-id",
    "GOOGLE_CLIENT_SECRET": "fake-google-client-secret",
    "GOOGLE_REDIRECT_URI": "http://localhost:8000/connectors/google-calendar/callback",
    "GOOGLE_CALENDAR_MODE": "shared",
    "PROPERTY_FINDER_WEBHOOK_SECRET": "fake_pf_webhook_secret_test_123",
    "BAYUT_WEBHOOK_TOKEN": "fake_bayut_token",
    "DUBIZZLE_WEBHOOK_TOKEN": "fake_dubizzle_token",
    "DEFAULT_AGENCY_ID": "00000000-0000-0000-0000-000000000000",
    "VAPI_API_KEY": "fake_vapi_key",
    "VAPI_PHONE_NUMBER_ID": "fake_vapi_phone_id",
    "VAPI_ASSISTANT_ID": "fake_vapi_assistant_id",
    "VAPI_WEBHOOK_SECRET": "fake_vapi_secret",
    "STRIPE_SECRET_KEY": "fake_stripe_secret_key_test",
    "STRIPE_WEBHOOK_SECRET": "fake_stripe_webhook_secret_test",
    "FRONTEND_URL": "http://localhost:3000",
    "API_BASE_URL": "http://localhost:8000",
}

for k, v in FAKE_ENV.items():
    os.environ[k] = v

from config import get_settings, settings
get_settings.cache_clear()
for k, v in FAKE_ENV.items():
    if hasattr(settings, k):
        if isinstance(getattr(settings, k), bool):
            object.__setattr__(settings, k, v.lower() in ("1", "true"))
        else:
            object.__setattr__(settings, k, v)


# ─── 2. BLOCK OUTBOUND NETWORK (SOCKET / DNS) ─────────────────────────────────
_real_socket_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo

ALLOWED_LOOPBACK = {"127.0.0.1", "localhost", "::1", None, ""}
BLOCKED_NETWORK_ATTEMPTS = []


def _is_loopback(host):
    if host in ALLOWED_LOOPBACK:
        return True
    if isinstance(host, str) and (host.startswith("127.") or host == "0.0.0.0"):
        return True
    return False


def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) and len(address) > 0 else address
    if _is_loopback(host):
        return _real_socket_connect(self, address)
    attempt_msg = f"Outbound network connection to {address} attempted without a mock"
    BLOCKED_NETWORK_ATTEMPTS.append(attempt_msg)
    raise RuntimeError(
        f"[NETWORK BLOCKED] {attempt_msg}."
    )


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    if _is_loopback(host):
        return _real_getaddrinfo(host, port, *args, **kwargs)
    attempt_msg = f"Outbound DNS resolution for '{host}:{port}' attempted without a mock"
    BLOCKED_NETWORK_ATTEMPTS.append(attempt_msg)
    raise RuntimeError(
        f"[NETWORK BLOCKED] {attempt_msg}."
    )


socket.socket.connect = _guarded_connect
socket.getaddrinfo = _guarded_getaddrinfo


@pytest.fixture(autouse=True)
def fail_on_blocked_network(request):
    """
    Autouse fixture that clears blocked network attempts before each test
    and FAILS the test during teardown if any outbound connection was attempted
    (unless explicitly marked with @pytest.mark.allow_network).
    """
    BLOCKED_NETWORK_ATTEMPTS.clear()
    yield
    if "allow_network" not in request.keywords:
        if BLOCKED_NETWORK_ATTEMPTS:
            attempts = list(BLOCKED_NETWORK_ATTEMPTS)
            BLOCKED_NETWORK_ATTEMPTS.clear()
            pytest.fail(f"Network calls blocked during test: {attempts}")


# ─── 3. AUTOUSE MOCK SUPABASE CLIENT ──────────────────────────────────────────
def create_mock_supabase():
    """Create a fully fluent mock Supabase client."""
    mock_sb = MagicMock(name="AutoMockSupabaseClient")
    table_mock = MagicMock(name="AutoMockTable")
    mock_sb.table.return_value = table_mock
    rpc_mock = MagicMock(name="AutoMockRpc")
    rpc_mock.execute.return_value = MagicMock(data=True, count=1)
    mock_sb.rpc.return_value = rpc_mock

    # Fluent query chaining
    table_mock.select.return_value = table_mock
    table_mock.update.return_value = table_mock
    table_mock.delete.return_value = table_mock
    table_mock.upsert.return_value = table_mock
    table_mock.eq.return_value = table_mock
    table_mock.neq.return_value = table_mock
    table_mock.in_.return_value = table_mock
    table_mock.ilike.return_value = table_mock
    table_mock.like.return_value = table_mock
    table_mock.is_.return_value = table_mock
    table_mock.order.return_value = table_mock
    table_mock.limit.return_value = table_mock
    table_mock.range.return_value = table_mock
    table_mock.single.return_value = table_mock
    table_mock.maybe_single.return_value = table_mock
    table_mock.execute.return_value = MagicMock(data=[], count=0)

    def _default_insert(payload, *args, **kwargs):
        builder = MagicMock()
        for attr in ("select", "eq", "neq", "in_", "ilike", "like", "is_", "order", "limit", "range", "single", "maybe_single"):
            setattr(builder, attr, MagicMock(return_value=builder))
        if isinstance(payload, list) and payload:
            returned = [dict(item, id=item.get("id", f"mock-id-{i}")) if isinstance(item, dict) else {"id": f"mock-id-{i}"} for i, item in enumerate(payload)]
        elif isinstance(payload, dict):
            returned = [dict(payload, id=payload.get("id", "mock-inserted-id"))]
        else:
            returned = [{"id": "mock-inserted-id"}]
        builder.execute.return_value = MagicMock(data=returned, count=len(returned))
        return builder

    table_mock.insert.side_effect = _default_insert

    # Auth mock
    mock_sb.auth = MagicMock(name="AutoMockAuth")
    mock_sb.auth.sign_in_with_password.return_value = MagicMock(
        user=MagicMock(id="fake-user-id", email="test@example.com"),
        session=MagicMock(access_token="fake-token"),
    )
    mock_sb.auth.sign_up.return_value = MagicMock(
        user=MagicMock(id="fake-user-id", email="test@example.com")
    )
    return mock_sb


@pytest.fixture(autouse=True)
def autouse_mock_supabase(monkeypatch):
    """
    Autouse fixture that patches database.supabase_client.get_supabase
    and all module-level aliases to a safe MagicMock.
    Individual tests can still override this using patch() or monkeypatch.
    """
    mock_sb = create_mock_supabase()

    # Dynamic supplier so newly imported modules receive the current active mock
    def _current_mock_supabase():
        return mock_sb

    monkeypatch.setattr("database.supabase_client.get_supabase", _current_mock_supabase)

    # Patch in any already imported module that has get_supabase
    for mod_name, mod in list(sys.modules.items()):
        if mod and hasattr(mod, "get_supabase") and callable(getattr(mod, "get_supabase")):
            try:
                monkeypatch.setattr(mod, "get_supabase", _current_mock_supabase)
            except Exception:
                pass

    yield mock_sb
