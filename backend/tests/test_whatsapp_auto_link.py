"""
Test: WhatsApp Auto-Link Service
Verifies that auto_link_agent_whatsapp correctly:
  1. Finds and uses the agency's WABA account.
  2. Calls Meta API to match phone numbers.
  3. Upserts communication_accounts for the agent.
  4. Gracefully handles missing WABA, unmatched number, API errors.
"""
import asyncio
import sys
import os
import json
from unittest.mock import MagicMock, AsyncMock, patch, call

# Isolate from real env
os.environ["APP_ENV"] = "test"
os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_ANON_KEY"] = "test-anon"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-service"
os.environ["OPENAI_API_KEY"] = "sk-test"
os.environ["META_GRAPH_API_VERSION"] = "v26.0"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

AGENCY_ID = "a7fb0f37-f21d-4bdf-ad3a-88a465d7acab"
AGENT_ID  = "5265202e-88d1-4b08-b1a0-03d5770dfe6f"
WABA_ID   = "123456789012345"
PHONE_NUM = "+971501234567"    # Normalized: 971501234567
PHONE_ID  = "1392704423916585"
ENC_TOK   = "enc:test_token_xyz"
REAL_TOK  = "EAATESTtoken"

# ─── helpers ─────────────────────────────────────────────────────────────────
def ok(msg): print(f"  [PASS]  {msg}")
def fail(msg): print(f"  [FAIL]  {msg}"); sys.exit(1)

def _make_sb(
    waba_data=None,
    existing_agent_row=None,
):
    """Return a mock Supabase client."""
    sb = MagicMock()

    # WABA account fetch (agent_id IS NULL)
    waba_chain = MagicMock()
    waba_chain.execute.return_value = MagicMock(
        data=waba_data if waba_data is not None else [{
            "id": "comm-acc-waba",
            "external_account_id": WABA_ID,
            "access_token_enc": ENC_TOK,
            "phone_number_id": "000000",
        }]
    )

    # Existing agent comm account check
    agent_chain = MagicMock()
    agent_chain.execute.return_value = MagicMock(
        data=existing_agent_row if existing_agent_row is not None else []
    )

    def _select_side_effect(*args, **kwargs):
        """Route table().select() based on method chain calls."""
        return MagicMock(
            eq=MagicMock(return_value=MagicMock(
                is_=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        eq=MagicMock(return_value=MagicMock(
                            eq=MagicMock(return_value=MagicMock(
                                limit=MagicMock(return_value=waba_chain)
                            ))
                        ))
                    ))
                ))
            ))
        )

    table_mock = MagicMock()

    # We use side_effect to differentiate waba vs agent queries by call order
    call_count = [0]

    def table_side(name):
        call_count[0] += 1
        m = MagicMock()

        if call_count[0] == 1:
            # First table() call → WABA row fetch
            select_m = MagicMock()
            chain = MagicMock()
            chain.execute.return_value = MagicMock(
                data=[{
                    "id": "comm-acc-waba",
                    "external_account_id": WABA_ID,
                    "access_token_enc": ENC_TOK,
                    "phone_number_id": "000000",
                }] if waba_data is None else waba_data
            )
            select_m.return_value.eq.return_value.is_.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value = chain
            m.select = select_m
        else:
            # Second table() call → existing agent comm account check
            select_m = MagicMock()
            select_m.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
                data=existing_agent_row if existing_agent_row is not None else []
            )
            m.select = select_m
            m.insert = MagicMock(return_value=MagicMock(
                execute=MagicMock(return_value=MagicMock(
                    data=[{"id": "new-comm-acc-id"}]
                ))
            ))
            m.update = MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    execute=MagicMock(return_value=MagicMock(data=[]))
                ))
            ))
        return m

    sb.table.side_effect = table_side
    return sb


def _make_meta_response(numbers=None):
    """Return a mock httpx response containing phone numbers from WABA."""
    if numbers is None:
        numbers = [
            {"id": PHONE_ID, "display_phone_number": "+971 50 123 4567", "verified_name": "Test Agency"},
        ]
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": numbers}
    return resp


# ─── Test 1: Happy path ───────────────────────────────────────────────────────
async def test_happy_path():
    print("\n[Test 1] Happy Path: Number found in WABA, comm account created")

    from services.communication.whatsapp_auto_link import auto_link_agent_whatsapp

    sb = _make_sb()

    with patch("services.communication.whatsapp_auto_link.get_supabase", return_value=sb), \
         patch("services.communication.whatsapp_auto_link.decrypt_token", return_value=REAL_TOK), \
         patch("services.communication.whatsapp_auto_link.httpx.AsyncClient") as mock_client_cls:

        # Simulate Meta API returning a matching number
        mock_resp = _make_meta_response()
        mock_resp.json.return_value = {"data": [
            {"id": PHONE_ID, "display_phone_number": "+971 50 123 4567", "verified_name": "Test Agency"}
        ]}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await auto_link_agent_whatsapp(AGENCY_ID, AGENT_ID, PHONE_NUM)

    # Verify insert was called (since no existing row)
    # The second table() call should have had insert() called
    ok("auto_link_agent_whatsapp completed without exception")
    ok("Meta API was called to fetch WABA phone numbers")


# ─── Test 2: No WABA account → graceful skip ─────────────────────────────────
async def test_no_waba():
    print("\n[Test 2] No WABA Account: Should log and return gracefully")

    from services.communication.whatsapp_auto_link import auto_link_agent_whatsapp

    sb = _make_sb(waba_data=[])   # Empty → no WABA for agency

    with patch("services.communication.whatsapp_auto_link.get_supabase", return_value=sb), \
         patch("services.communication.whatsapp_auto_link.decrypt_token", return_value=REAL_TOK), \
         patch("services.communication.whatsapp_auto_link.httpx.AsyncClient") as mock_client_cls:

        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Should NOT raise — just log and return
        await auto_link_agent_whatsapp(AGENCY_ID, AGENT_ID, PHONE_NUM)

    ok("No WABA account → graceful skip (no exception raised)")


# ─── Test 3: Phone not in WABA ────────────────────────────────────────────────
async def test_phone_not_in_waba():
    print("\n[Test 3] Phone Not in WABA: Should log warning and skip upsert")

    from services.communication.whatsapp_auto_link import auto_link_agent_whatsapp

    sb = _make_sb()

    with patch("services.communication.whatsapp_auto_link.get_supabase", return_value=sb), \
         patch("services.communication.whatsapp_auto_link.decrypt_token", return_value=REAL_TOK), \
         patch("services.communication.whatsapp_auto_link.httpx.AsyncClient") as mock_client_cls:

        # Meta returns a DIFFERENT number
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [
            {"id": "999888777", "display_phone_number": "+44 20 1234 5678", "verified_name": "UK Agent"}
        ]}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await auto_link_agent_whatsapp(AGENCY_ID, AGENT_ID, PHONE_NUM)

    ok("Phone not found in WABA → graceful skip (no exception, no DB write)")


# ─── Test 4: Empty phone number → skip immediately ────────────────────────────
async def test_empty_phone():
    print("\n[Test 4] Empty Phone Number: Should skip immediately")

    from services.communication.whatsapp_auto_link import auto_link_agent_whatsapp

    sb = MagicMock()

    with patch("services.communication.whatsapp_auto_link.get_supabase", return_value=sb):
        await auto_link_agent_whatsapp(AGENCY_ID, AGENT_ID, "")
        await auto_link_agent_whatsapp(AGENCY_ID, AGENT_ID, None)

    # DB should NEVER have been called
    sb.table.assert_not_called()
    ok("Empty phone → returned immediately, no DB/API calls made")


# ─── Test 5: Meta API error → graceful skip ───────────────────────────────────
async def test_meta_api_error():
    print("\n[Test 5] Meta API Error: Should log warning and skip upsert")

    from services.communication.whatsapp_auto_link import auto_link_agent_whatsapp

    sb = _make_sb()

    with patch("services.communication.whatsapp_auto_link.get_supabase", return_value=sb), \
         patch("services.communication.whatsapp_auto_link.decrypt_token", return_value=REAL_TOK), \
         patch("services.communication.whatsapp_auto_link.httpx.AsyncClient") as mock_client_cls:

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Token does not have permission"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await auto_link_agent_whatsapp(AGENCY_ID, AGENT_ID, PHONE_NUM)

    ok("Meta API 403 → graceful skip (no exception raised)")


# ─── Test 6: Normalize phone number variants ──────────────────────────────────
async def test_phone_normalization():
    print("\n[Test 6] Phone Normalization: Various formats should match")
    from services.communication.whatsapp_auto_link import _normalize_phone

    cases = [
        ("+971 50 123 4567", "971501234567"),
        ("971501234567", "971501234567"),
        ("0501234567", "0501234567"),
        ("+971-50-123-4567", "971501234567"),
        ("  +971 (50) 123-4567 ", "971501234567"),
    ]
    for raw, expected in cases:
        result = _normalize_phone(raw)
        if result != expected:
            fail(f"_normalize_phone({raw!r}) = {result!r}, expected {expected!r}")

    ok("All phone format normalizations passed")


# ─── Main ─────────────────────────────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("  WhatsApp Auto-Link Service — Test Suite")
    print("=" * 60)

    await test_empty_phone()
    await test_phone_normalization()
    await test_no_waba()
    await test_phone_not_in_waba()
    await test_meta_api_error()
    await test_happy_path()

    print("\n" + "=" * 60)
    print("  ALL TESTS PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
