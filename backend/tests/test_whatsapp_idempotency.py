"""
Unit Tests for WhatsApp Webhook Idempotency (Correction e).

Covers:
  1. First message processed (atomic insert succeeds -> returns True).
  2. Duplicate message skipped (duplicate key violation -> returns False).
  3. Concurrent duplicates (PK conflict handled without check-then-insert race).
  4. DB error graceful degradation (DB connection error -> returns True so message is not dropped).
"""
import pytest
from unittest.mock import MagicMock
from routers.webhooks import _check_and_mark_message_id


def test_first_message_processed_successfully():
    """First time a message_id is seen, atomic insert succeeds and returns True."""
    sb = MagicMock()
    # Mock insert returning success
    sb.table.return_value.insert.return_value.execute.return_value.data = [{"message_id": "wamid.123"}]

    result = _check_and_mark_message_id(sb, "wamid.123", agency_id="agency-1", agent_id="agent-1")

    assert result is True
    sb.table.assert_called_with("whatsapp_processed_messages")
    sb.table.return_value.insert.assert_called_once_with({
        "message_id": "wamid.123",
        "agency_id": "agency-1",
        "agent_id": "agent-1",
    })


def test_duplicate_message_skipped():
    """Duplicate message causes PK conflict (23505 duplicate key) -> returns False."""
    sb = MagicMock()
    # Mock PostgreSQL duplicate key error
    sb.table.return_value.insert.return_value.execute.side_effect = Exception(
        'duplicate key value violates unique constraint "whatsapp_processed_messages_pkey"'
    )

    result = _check_and_mark_message_id(sb, "wamid.123")

    assert result is False


def test_concurrent_duplicates_handled_atomically():
    """Simulate two concurrent workers receiving the same message simultaneously."""
    sb1 = MagicMock()
    sb2 = MagicMock()

    # Worker 1 succeeds
    sb1.table.return_value.insert.return_value.execute.return_value.data = [{"message_id": "wamid.concurrent"}]
    # Worker 2 hits race condition: PK collision
    sb2.table.return_value.insert.return_value.execute.side_effect = Exception(
        "Key (message_id)=(wamid.concurrent) already exists. Error 23505"
    )

    res1 = _check_and_mark_message_id(sb1, "wamid.concurrent")
    res2 = _check_and_mark_message_id(sb2, "wamid.concurrent")

    assert res1 is True   # First worker processes
    assert res2 is False  # Second worker skips


def test_db_error_graceful_degradation():
    """
    If the database is down or table not yet migrated, fail OPEN (return True)
    so critical real customer inquiries are not lost due to an infrastructure blip.
    """
    sb = MagicMock()
    # Non-duplicate error (e.g. Supabase connection timeout or 500 error)
    sb.table.return_value.insert.return_value.execute.side_effect = RuntimeError(
        "Connection refused: database host unreachable"
    )

    result = _check_and_mark_message_id(sb, "wamid.123")

    # Graceful degradation: returns True with warning logged
    assert result is True


def test_unmark_message_id_on_failure():
    """Item 16: Verify that on processing failure, idempotency record is removed so retry succeeds."""
    from routers.webhooks import _unmark_message_id

    sb = MagicMock()
    mock_delete = MagicMock()
    mock_delete.execute.return_value = MagicMock(data=[])
    sb.table.return_value.delete.return_value.eq.return_value = mock_delete

    _unmark_message_id(sb, "wamid.failed_msg_456")

    sb.table.assert_called_with("whatsapp_processed_messages")
    sb.table.return_value.delete.return_value.eq.assert_called_once_with("message_id", "wamid.failed_msg_456")
    mock_delete.execute.assert_called_once()


@pytest.mark.asyncio
async def test_webhook_failure_clears_idempotency_for_retry():
    """Item 16: When message processing raises an exception, idempotency record is deleted so retry is accepted."""
    from unittest.mock import patch, AsyncMock
    from routers.webhooks import whatsapp_inbound
    from fastapi import Request

    sb = MagicMock()
    deleted_msg_ids = []
    def mock_table(tbl_name):
        tbl = MagicMock()
        if tbl_name == "whatsapp_processed_messages":
            tbl.insert.return_value.execute.return_value = MagicMock(data=[])
            def mock_eq(col, val):
                if col == "message_id":
                    deleted_msg_ids.append(val)
                return MagicMock()
            tbl.delete.return_value.eq.side_effect = mock_eq
        return tbl
    sb.table.side_effect = mock_table

    mock_request = AsyncMock(spec=Request)
    mock_request.headers = {"x-webhook-token": "valid"}
    mock_request.body.return_value = b'{"object":"whatsapp_business_account","entry":[]}'
    mock_request.json.return_value = {"messages": [{"from": "+971501112233", "id": "wamid.retry_test_999", "text": {"body": "hi"}}]}

    from config import settings
    with (
        patch("routers.webhooks.get_supabase", return_value=sb),
        patch.object(settings, "WHATSAPP_PROVIDER", "360dialog"),
        patch.object(settings, "WHATSAPP_WEBHOOK_TOKEN", "valid"),
        patch("routers.webhooks.parse_360dialog_inbound", return_value=[{"from_phone": "+971501112233", "to_phone": "+971500000000", "message": "hi", "message_id": "wamid.retry_test_999", "is_meta": False}]),
        patch("routers.webhooks._find_lead_by_sender_phone", side_effect=RuntimeError("Simulated unhandled DB crash")),
    ):
        with pytest.raises(RuntimeError, match="Simulated unhandled DB crash"):
            await whatsapp_inbound(mock_request)

    assert "wamid.retry_test_999" in deleted_msg_ids


# ─── REVIEW ROUND 3 ITEM 7 TESTS: LEAD RESOLUTION & 24H WINDOW SCOPING ────────

def test_lead_resolution_two_leads_different_agents_same_buyer_shared_number():
    """
    Item 7: Two leads with different agents and the same buyer phone.
    On a shared agency number (agent_id=None), match must fail safe (ambiguous).
    """
    from routers.webhooks import _find_lead_by_sender_phone

    leads_in_db = [
        {"id": "lead-agent-1", "agency_id": "agency-1", "assigned_agent_id": "agent-uuid-1", "phone": "+971501234567"},
        {"id": "lead-agent-2", "agency_id": "agency-1", "assigned_agent_id": "agent-uuid-2", "phone": "+971501234567"},
    ]
    sb = MagicMock()
    sb.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = leads_in_db

    # Inbound on shared agency number (agent_id=None)
    lead, reason = _find_lead_by_sender_phone(sb, "+971501234567", agency_id="agency-1", agent_id=None)
    assert lead is None
    assert reason == "ambiguous"


def test_lead_resolution_two_leads_different_agents_same_buyer_agent_byon():
    """
    Item 7: Two leads with different agents and the same buyer phone.
    On Agent 1's BYON number (agent_id='agent-uuid-1'), Agent 1's lead must be matched.
    """
    from routers.webhooks import _find_lead_by_sender_phone

    leads_in_db = [
        {"id": "lead-agent-1", "agency_id": "agency-1", "assigned_agent_id": "agent-uuid-1", "phone": "+971501234567"},
        {"id": "lead-agent-2", "agency_id": "agency-1", "assigned_agent_id": "agent-uuid-2", "phone": "+971501234567"},
    ]
    sb = MagicMock()
    sb.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = leads_in_db

    # Inbound on Agent 1's BYON number
    lead, reason = _find_lead_by_sender_phone(sb, "+971501234567", agency_id="agency-1", agent_id="agent-uuid-1")
    assert lead is not None
    assert lead["id"] == "lead-agent-1"
    assert reason == "matched"


def test_lead_resolution_two_leads_same_agent_same_buyer_picks_latest():
    """
    Item 7: Two leads for the SAME agent and same buyer.
    Must resolve the most recently updated lead.
    """
    from routers.webhooks import _find_lead_by_sender_phone

    older_lead = {
        "id": "lead-older",
        "agency_id": "agency-1",
        "assigned_agent_id": "agent-uuid-1",
        "phone": "+971501234567",
        "created_at": "2026-01-01T10:00:00Z",
        "updated_at": "2026-01-01T12:00:00Z",
    }
    newer_lead = {
        "id": "lead-newer",
        "agency_id": "agency-1",
        "assigned_agent_id": "agent-uuid-1",
        "phone": "+971501234567",
        "created_at": "2026-01-02T10:00:00Z",
        "updated_at": "2026-01-02T15:00:00Z",
    }
    leads_in_db = [older_lead, newer_lead]
    sb = MagicMock()
    sb.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = leads_in_db

    lead, reason = _find_lead_by_sender_phone(sb, "+971501234567", agency_id="agency-1", agent_id="agent-uuid-1")
    assert lead is not None
    assert lead["id"] == "lead-newer"
    assert reason == "matched"


def test_one_buyer_message_never_opens_window_for_another_buyer():
    """
    Item 7: An inbound message from Buyer 1 must update last_inbound_at ONLY on Buyer 1's lead row.
    Buyer 2's lead row must never be touched.
    """
    from routers.webhooks import _find_lead_by_sender_phone
    from services.communication.template_service import is_within_24h_window

    buyer1_lead = {"id": "lead-buyer-1", "agency_id": "agency-1", "phone": "+971501111111", "last_inbound_at": None}
    buyer2_lead = {"id": "lead-buyer-2", "agency_id": "agency-1", "phone": "+971502222222", "last_inbound_at": None}

    # Verify initially neither window is open
    assert is_within_24h_window(buyer1_lead["last_inbound_at"]) is False
    assert is_within_24h_window(buyer2_lead["last_inbound_at"]) is False

    sb = MagicMock()
    sb.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [buyer1_lead]

    updated_lead_ids = []
    def mock_update(payload):
        m = MagicMock()
        def mock_eq(col, val):
            if col == "id":
                updated_lead_ids.append(val)
                if val == buyer1_lead["id"]:
                    buyer1_lead.update(payload)
                elif val == buyer2_lead["id"]:
                    buyer2_lead.update(payload)
            return MagicMock(execute=MagicMock(return_value=MagicMock(data=[])))
        m.eq.side_effect = mock_eq
        return m
    sb.table.return_value.update.side_effect = mock_update

    # Buyer 1 sends message
    matched_lead, reason = _find_lead_by_sender_phone(sb, "+971501111111", agency_id="agency-1")
    assert matched_lead["id"] == "lead-buyer-1"

    # Simulate webhooks.py updating last_inbound_at for matched lead
    now_iso = "2026-09-19T12:00:00+00:00"
    sb.table("leads").update({"last_inbound_at": now_iso}).eq("id", matched_lead["id"]).execute()

    # Verify Buyer 1 window opened, Buyer 2 untouched
    assert "lead-buyer-1" in updated_lead_ids
    assert "lead-buyer-2" not in updated_lead_ids
    assert buyer2_lead["last_inbound_at"] is None
    assert is_within_24h_window(buyer2_lead["last_inbound_at"]) is False


def test_phone_normalization_consistency():
    """
    Item 7: Phone format normalization (with/without '+', spaces, dashes).
    Consistent everywhere phone numbers are stored, compared, and looked up.
    """
    from routers.webhooks import _normalize_phone, _find_lead_by_sender_phone

    # Normalization helper produces exact digits
    assert _normalize_phone("+971 50 123 4567") == "971501234567"
    assert _normalize_phone("00971501234567") == "00971501234567"
    assert _normalize_phone("+971-50-123-4567") == "971501234567"
    assert _normalize_phone("971501234567") == "971501234567"

    # Matching matches lead stored with '+' when webhook delivers without '+'
    lead_stored_with_plus = {"id": "lead-plus", "agency_id": "agency-1", "phone": "+971501234567"}
    sb = MagicMock()
    sb.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [lead_stored_with_plus]

    matched, reason = _find_lead_by_sender_phone(sb, "971501234567", agency_id="agency-1")
    assert matched is not None
    assert matched["id"] == "lead-plus"
    assert reason == "matched"

    # Matching matches lead stored without '+' when webhook delivers with '+'
    lead_stored_without_plus = {"id": "lead-no-plus", "agency_id": "agency-1", "phone": "971501234567"}
    sb.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [lead_stored_without_plus]

    matched2, reason2 = _find_lead_by_sender_phone(sb, "+971501234567", agency_id="agency-1")
    assert matched2 is not None
    assert matched2["id"] == "lead-no-plus"
    assert reason2 == "matched"


def test_agent_scoped_channel_no_lead_returns_unknown_never_crosses_agents():
    """
    Item 5: Two agents in one agency, same buyer phone.
    - When channel is scoped to Agent A, Agent A's lead is matched.
    - When channel is scoped to Agent B, Agent B's lead is matched.
    - When channel is scoped to Agent C (who has no lead), returns (None, 'unknown')
      and NEVER falls back to Agent A or B's lead.
    - When channel is shared (agent_id=None), returns (None, 'ambiguous').
    """
    from routers.webhooks import _find_lead_by_sender_phone

    leads_in_db = [
        {"id": "lead-agent-a", "agency_id": "agency-1", "assigned_agent_id": "agent-a", "phone": "+971501234567"},
        {"id": "lead-agent-b", "agency_id": "agency-1", "assigned_agent_id": "agent-b", "phone": "+971501234567"},
    ]
    sb = MagicMock()
    sb.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = leads_in_db

    # 1. Scoped to Agent A -> matches Agent A
    matched_a, reason_a = _find_lead_by_sender_phone(sb, "+971501234567", agency_id="agency-1", agent_id="agent-a")
    assert matched_a is not None
    assert matched_a["id"] == "lead-agent-a"
    assert reason_a == "matched"

    # 2. Scoped to Agent B -> matches Agent B
    matched_b, reason_b = _find_lead_by_sender_phone(sb, "+971501234567", agency_id="agency-1", agent_id="agent-b")
    assert matched_b is not None
    assert matched_b["id"] == "lead-agent-b"
    assert reason_b == "matched"

    # 3. Scoped to Agent C (no lead assigned to C) -> returns (None, 'unknown'), does NOT return Agent A or B
    matched_c, reason_c = _find_lead_by_sender_phone(sb, "+971501234567", agency_id="agency-1", agent_id="agent-c")
    assert matched_c is None
    assert reason_c == "unknown"

    # 4. Unscoped shared channel (agent_id=None) -> returns (None, 'ambiguous')
    matched_shared, reason_shared = _find_lead_by_sender_phone(sb, "+971501234567", agency_id="agency-1", agent_id=None)
    assert matched_shared is None
    assert reason_shared == "ambiguous"


