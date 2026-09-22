"""
tests/test_whatsapp_smart_send.py

Unit tests for services.whatsapp_service.send_whatsapp_smart,
_queue_and_notify_agent, and drain_outbound_queue_for_lead.

All external dependencies (DB, provider, template lookup) are mocked.
No real Supabase connection. No .env values read.

Coverage:
  1. In-window lead => free-form send via send_whatsapp_for_agency, no template logic.
  2. Out-of-window, approved template found => provider.send_message with template_name/params.
  3. Out-of-window, no approved template => message queued, agent notified.
  4. Out-of-window, no template, no assigned agent => admin fallback notified.
  5. Out-of-window, no template, no agent at all => queued but NO notification (no crash).
  6. drain_outbound_queue_for_lead: pending items present => sends and marks sent.
  7. drain_outbound_queue_for_lead: empty queue => returns 0 and makes no send calls.
  8. drain fires with correct args after last_inbound_at update (window reopen).

Patching notes:
  - get_supabase is lazily imported inside each function via
      from database.supabase_client import get_supabase
    Patch at: database.supabase_client.get_supabase
  - get_approved_template_or_none is lazily imported.
    Patch at: services.communication.template_service.get_approved_template_or_none
  - is_within_24h_window is lazily imported.
    Patch at: services.communication.template_service.is_within_24h_window
  - send_whatsapp_for_agency is a module-level name in whatsapp_service.
    Patch at: services.whatsapp_service.send_whatsapp_for_agency
  - get_whatsapp_provider_for_agency is lazily imported.
    Patch at: services.communication.provider_factory.get_whatsapp_provider_for_agency
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ---- Constants ---------------------------------------------------------------

AGENCY_ID   = "aaaa0000-0000-0000-0000-000000000001"
LEAD_ID     = "bbbb0000-0000-0000-0000-000000000002"
AGENT_ID    = "cccc0000-0000-0000-0000-000000000003"
ADMIN_ID    = "dddd0000-0000-0000-0000-000000000004"
PHONE       = "971501234567"
AGENT_PHONE = "971509999999"
ADMIN_PHONE = "971508888888"
BODY        = "Hi! Can we schedule a viewing?"
TMPL_NAME   = "andios_lead_first_contact"
WABA_ID     = "waba-test-id-001"

FAKE_SENT  = {"status": "sent",  "sid": "wamid.test001"}
FAKE_ERROR = {"status": "error", "error": "131047 - window closed"}


# ---- Mock Helpers ------------------------------------------------------------


def _make_sb_empty():
    ch = MagicMock()
    ch.table.return_value = ch
    ch.select.return_value = ch
    ch.eq.return_value = ch
    ch.limit.return_value = ch
    ch.insert.return_value = ch
    ch.update.return_value = ch
    ch.rpc.return_value = ch
    ch.execute.return_value = MagicMock(data=[])
    return ch


def _make_provider(*, success=True, message_id="wamid.tmpl001"):
    from services.communication.base import SendResult
    p = MagicMock()
    p.send_message = AsyncMock(return_value=SendResult(
        success=success,
        message_id=message_id if success else None,
        error=None if success else "failed",
    ))
    return p


def _make_account(waba_id=WABA_ID):
    from services.communication.base import CommunicationAccount
    return CommunicationAccount(
        id="acc-0001", agency_id=AGENCY_ID, channel="whatsapp",
        provider="meta", phone_number="971500000000",
        external_account_id=waba_id, phone_number_id="pnid-001", access_token="EAAtest",
    )


# ---- Test 1: In-Window -> Free-Form Send ------------------------------------


@pytest.mark.asyncio
async def test_smart_send_in_window_calls_free_form():
    """In-window lead: send_whatsapp_for_agency is called; provider is never resolved."""
    from services.whatsapp_service import send_whatsapp_smart

    mock_pf   = AsyncMock()
    mock_send = AsyncMock(return_value=FAKE_SENT)

    with patch("services.communication.template_service.is_within_24h_window", return_value=True), \
         patch("services.whatsapp_service.send_whatsapp_for_agency", new=mock_send), \
         patch("services.communication.provider_factory.get_whatsapp_provider_for_agency", new=mock_pf):

        result = await send_whatsapp_smart(
            AGENCY_ID, LEAD_ID, PHONE, BODY,
            agent_id=AGENT_ID,
            template_name=TMPL_NAME,
            last_inbound_at="2099-01-01T10:00:00+00:00",
        )

    assert result == FAKE_SENT
    mock_send.assert_awaited_once_with(AGENCY_ID, PHONE, BODY, agent_id=AGENT_ID)
    mock_pf.assert_not_awaited()


# ---- Test 2: Out-of-Window, Approved Template -> Template Send ---------------


@pytest.mark.asyncio
async def test_smart_send_out_of_window_approved_template():
    """Out-of-window + approved template: provider.send_message called with template args."""
    from services.whatsapp_service import send_whatsapp_smart

    provider     = _make_provider(success=True)
    account      = _make_account()
    fake_tmpl    = {"id": "t-001", "name": TMPL_NAME, "status": "APPROVED", "waba_id": WABA_ID}
    mock_free    = AsyncMock(return_value=FAKE_SENT)

    with patch("services.communication.template_service.is_within_24h_window", return_value=False), \
         patch("services.whatsapp_service.send_whatsapp_for_agency", new=mock_free), \
         patch("services.communication.provider_factory.get_whatsapp_provider_for_agency",
               new=AsyncMock(return_value=(provider, account))), \
         patch("services.communication.template_service.get_approved_template_or_none",
               new=AsyncMock(return_value=fake_tmpl)), \
         patch("database.supabase_client.get_supabase", return_value=_make_sb_empty()):

        params = ["Alice", "a great property"]
        result = await send_whatsapp_smart(
            AGENCY_ID, LEAD_ID, PHONE, BODY,
            agent_id=AGENT_ID,
            template_name=TMPL_NAME,
            template_params=params,
            last_inbound_at=None,
        )

    assert result["status"] == "sent"
    provider.send_message.assert_awaited_once_with(
        PHONE, BODY, template_name=TMPL_NAME, template_params=params
    )
    mock_free.assert_not_awaited()


# ---- Test 3: Out-of-Window, No Template -> Queue + Notify -------------------


@pytest.mark.asyncio
async def test_smart_send_out_of_window_no_template_queues_and_notifies():
    """Out-of-window + no approved template: message queued, agent notified, returns queued."""
    from services.whatsapp_service import send_whatsapp_smart

    provider = _make_provider()
    account  = _make_account()

    queue_inserts: list[dict] = []
    notify_calls:  list[dict] = []

    def _make_ch(name):
        ch = MagicMock()
        ch.select.return_value = ch
        ch.eq.return_value = ch
        ch.limit.return_value = ch

        def _ins(payload):
            if name == "whatsapp_outbound_queue":
                queue_inserts.append(payload)
            ins_ch = MagicMock()
            ins_ch.execute.return_value = MagicMock(data=[{"id": "q-001"}])
            return ins_ch

        ch.insert.side_effect = _ins

        if name == "leads":
            ch.execute.return_value = MagicMock(data=[{
                "name": "Alice T", "phone": PHONE, "assigned_agent_id": AGENT_ID,
            }])
        elif name == "agents":
            ch.execute.return_value = MagicMock(data=[{
                "name": "Bob", "phone": AGENT_PHONE, "whatsapp_number": AGENT_PHONE,
            }])
        else:
            ch.execute.return_value = MagicMock(data=[])
        return ch

    mock_sb = MagicMock()
    mock_sb.table.side_effect = _make_ch

    async def _fake_send(agency_id, phone, body, *, agent_id=None):
        notify_calls.append({"phone": phone, "body": body})
        return FAKE_SENT

    with patch("services.communication.template_service.is_within_24h_window", return_value=False), \
         patch("services.communication.provider_factory.get_whatsapp_provider_for_agency",
               new=AsyncMock(return_value=(provider, account))), \
         patch("services.communication.template_service.get_approved_template_or_none",
               new=AsyncMock(return_value=None)), \
         patch("database.supabase_client.get_supabase", return_value=mock_sb), \
         patch("services.whatsapp_service.send_whatsapp_for_agency", side_effect=_fake_send):

        result = await send_whatsapp_smart(
            AGENCY_ID, LEAD_ID, PHONE, BODY,
            agent_id=AGENT_ID, template_name=TMPL_NAME, last_inbound_at=None,
        )

    assert result["status"] == "queued"
    assert result["lead_id"] == LEAD_ID
    assert len(queue_inserts) == 1
    assert queue_inserts[0]["lead_id"] == LEAD_ID
    assert queue_inserts[0]["status"] == "pending"
    assert len(notify_calls) == 1
    assert notify_calls[0]["phone"] == AGENT_PHONE
    assert "Queued Message Alert" in notify_calls[0]["body"]


# ---- Test 4: No Assigned Agent -> Admin Fallback -----------------------------


@pytest.mark.asyncio
async def test_queue_notify_falls_back_to_admin_when_no_agent():
    """No assigned_agent_id on lead: admin is queried and notified instead."""
    from services.whatsapp_service import _queue_and_notify_agent

    admin_lookups: list[bool] = []

    def _make_ch(name):
        ch = MagicMock()
        ch.select.return_value = ch
        ch.eq.return_value = ch
        ch.limit.return_value = ch
        ins = MagicMock()
        ins.execute.return_value = MagicMock(data=[{"id": "q-002"}])
        ch.insert.return_value = ins

        if name == "leads":
            ch.execute.return_value = MagicMock(data=[{
                "name": "No-Agent Lead", "phone": PHONE, "assigned_agent_id": None,
            }])
        elif name == "agents":
            admin_lookups.append(True)
            ch.execute.return_value = MagicMock(data=[{
                "id": ADMIN_ID, "name": "Admin",
                "phone": ADMIN_PHONE, "whatsapp_number": ADMIN_PHONE,
            }])
        else:
            ch.execute.return_value = MagicMock(data=[])
        return ch

    mock_sb = MagicMock()
    mock_sb.table.side_effect = _make_ch

    notify_calls: list[dict] = []

    async def _fake_send(agency_id, phone, body, **kwargs):
        notify_calls.append({"phone": phone})
        return FAKE_SENT

    with patch("database.supabase_client.get_supabase", return_value=mock_sb), \
         patch("services.whatsapp_service.send_whatsapp_for_agency", side_effect=_fake_send):

        await _queue_and_notify_agent(AGENCY_ID, LEAD_ID, PHONE, BODY, agent_id=None)

    assert len(notify_calls) == 1
    assert notify_calls[0]["phone"] == ADMIN_PHONE
    assert len(admin_lookups) >= 1


# ---- Test 5: No Agent At All -> Queued, No Notification, No Crash -----------


@pytest.mark.asyncio
async def test_queue_notify_no_agent_no_crash():
    """No agent and no admin found: message queued silently, no exception raised."""
    from services.whatsapp_service import _queue_and_notify_agent

    mock_sb   = _make_sb_empty()
    mock_send = AsyncMock(return_value=FAKE_SENT)

    with patch("database.supabase_client.get_supabase", return_value=mock_sb), \
         patch("services.whatsapp_service.send_whatsapp_for_agency", new=mock_send):

        await _queue_and_notify_agent(AGENCY_ID, LEAD_ID, PHONE, BODY, agent_id=None)

    mock_send.assert_not_awaited()


# ---- Test 6: Drain -- Items Present -> Sends and Marks Sent ------------------


@pytest.mark.asyncio
async def test_drain_queue_sends_pending_and_marks_sent():
    """Two pending items: both claimed via RPC, both sent, both updated to 'sent', returns 2."""
    from services.whatsapp_service import drain_outbound_queue_for_lead

    pending = [
        {"id": "q-aaa", "agency_id": AGENCY_ID, "agent_id": AGENT_ID,
         "lead_id": LEAD_ID, "to_phone": PHONE, "body": "Queued: first"},
        {"id": "q-bbb", "agency_id": AGENCY_ID, "agent_id": None,
         "lead_id": LEAD_ID, "to_phone": PHONE, "body": "Queued: second"},
    ]

    update_statuses: list[str] = []

    def _make_ch(name):
        ch = MagicMock()
        ch.select.return_value = ch
        ch.eq.return_value = ch
        ch.limit.return_value = ch

        def _upd(payload):
            update_statuses.append(payload.get("status", "?"))
            u = MagicMock()
            u.eq.return_value = u
            u.execute.return_value = MagicMock(data=[])
            return u

        ch.update.side_effect = _upd
        ch.execute.return_value = MagicMock(data=[])
        return ch

    mock_sb = MagicMock()
    mock_sb.table.side_effect = _make_ch
    rpc_mock = MagicMock()
    rpc_mock.execute.return_value = MagicMock(data=pending)
    mock_sb.rpc.return_value = rpc_mock

    sent_bodies: list[str] = []

    async def _fake_send(agency_id, phone, body, *, agent_id=None):
        sent_bodies.append(body)
        return FAKE_SENT

    with patch("database.supabase_client.get_supabase", return_value=mock_sb), \
         patch("services.whatsapp_service.send_whatsapp_for_agency", side_effect=_fake_send):

        drained = await drain_outbound_queue_for_lead(LEAD_ID, agency_id=AGENCY_ID, agent_id=AGENT_ID)

    assert drained == 2
    assert sent_bodies == ["Queued: first", "Queued: second"]
    assert update_statuses.count("sent") == 2
    mock_sb.rpc.assert_called_once_with("claim_queued_messages", {"p_lead_id": LEAD_ID})


# ---- Test 7: Drain -- Empty Queue -> 0 --------------------------------------


@pytest.mark.asyncio
async def test_drain_queue_empty_returns_zero():
    """Empty queue: RPC returns empty list, no sends, returns 0."""
    from services.whatsapp_service import drain_outbound_queue_for_lead

    mock_send = AsyncMock(return_value=FAKE_SENT)
    mock_sb = _make_sb_empty()

    with patch("database.supabase_client.get_supabase", return_value=mock_sb), \
         patch("services.whatsapp_service.send_whatsapp_for_agency", new=mock_send):

        drained = await drain_outbound_queue_for_lead(LEAD_ID, agency_id=AGENCY_ID)

    assert drained == 0
    mock_send.assert_not_awaited()
    mock_sb.rpc.assert_called_once_with("claim_queued_messages", {"p_lead_id": LEAD_ID})


# ---- Test 8: Drain fires with correct args after window reopen ---------------


@pytest.mark.asyncio
async def test_drain_correct_args_after_window_reopen():
    """
    Mimics the exact drain call that webhooks.py makes after updating
    last_inbound_at: verifies lead_id, agency_id, and body are threaded correctly.
    """
    from services.whatsapp_service import drain_outbound_queue_for_lead

    queued_item = {
        "id": "q-ccc", "agency_id": AGENCY_ID, "agent_id": AGENT_ID,
        "lead_id": LEAD_ID, "to_phone": PHONE, "body": "Queued follow-up",
    }

    def _make_ch(name):
        ch = MagicMock()
        ch.select.return_value = ch
        ch.eq.return_value = ch
        ch.limit.return_value = ch
        u = MagicMock()
        u.eq.return_value = u
        u.execute.return_value = MagicMock(data=[])
        ch.update.return_value = u
        ch.execute.return_value = MagicMock(data=[])
        return ch

    mock_sb = MagicMock()
    mock_sb.table.side_effect = _make_ch
    rpc_mock = MagicMock()
    rpc_mock.execute.return_value = MagicMock(data=[queued_item])
    mock_sb.rpc.return_value = rpc_mock

    drained_calls: list[dict] = []

    async def _fake_send(agency_id, phone, body, *, agent_id=None):
        drained_calls.append({"agency_id": agency_id, "body": body})
        return FAKE_SENT

    with patch("database.supabase_client.get_supabase", return_value=mock_sb), \
         patch("services.whatsapp_service.send_whatsapp_for_agency", side_effect=_fake_send):

        drained = await drain_outbound_queue_for_lead(
            lead_id=LEAD_ID, agency_id=AGENCY_ID, agent_id=AGENT_ID
        )

    assert drained == 1
    assert drained_calls[0]["body"] == "Queued follow-up"
    assert drained_calls[0]["agency_id"] == AGENCY_ID
    mock_sb.rpc.assert_called_once_with("claim_queued_messages", {"p_lead_id": LEAD_ID})


# ---- Test 9: Concurrent Drain -- Sent Only Once (Item 1) ---------------------


@pytest.mark.asyncio
async def test_drain_concurrent_calls_send_exactly_once():
    """
    Simulates two concurrent drain calls for the same lead_id (e.g. Meta webhook retry).
    The atomic claim_queued_messages RPC returns the pending row to the first caller
    and an empty list to the second caller.
    Asserts send_whatsapp_for_agency is called exactly once total.
    """
    import asyncio
    from services.whatsapp_service import drain_outbound_queue_for_lead

    queued_item = {
        "id": "q-race-001", "agency_id": AGENCY_ID, "agent_id": AGENT_ID,
        "lead_id": LEAD_ID, "to_phone": PHONE, "body": "Race test message",
    }

    rpc_call_count = 0

    def _fake_rpc(rpc_name, params):
        nonlocal rpc_call_count
        rpc_call_count += 1
        r = MagicMock()
        if rpc_call_count == 1:
            r.execute.return_value = MagicMock(data=[queued_item])
        else:
            r.execute.return_value = MagicMock(data=[])
        return r

    mock_sb = MagicMock()
    mock_sb.rpc.side_effect = _fake_rpc
    u = MagicMock()
    u.eq.return_value = u
    u.execute.return_value = MagicMock(data=[])
    mock_sb.table.return_value.update.return_value = u

    mock_send = AsyncMock(return_value=FAKE_SENT)

    with patch("database.supabase_client.get_supabase", return_value=mock_sb), \
         patch("services.whatsapp_service.send_whatsapp_for_agency", new=mock_send):

        results = await asyncio.gather(
            drain_outbound_queue_for_lead(LEAD_ID, agency_id=AGENCY_ID, agent_id=AGENT_ID),
            drain_outbound_queue_for_lead(LEAD_ID, agency_id=AGENCY_ID, agent_id=AGENT_ID),
        )

    assert sum(results) == 1
    assert set(results) == {0, 1}
    mock_send.assert_awaited_once_with(
        AGENCY_ID, PHONE, "Race test message", agent_id=AGENT_ID
    )


# ---- Test 10: Case (a) Env-Fallback (account is None) -> Free-Form Send -----


@pytest.mark.asyncio
async def test_smart_send_env_fallback_account_none_sends_free_form():
    """
    Case (a): account is None (platform env fallback, no communication_accounts row).
    Free-form send via provider succeeds, preserving platform-level default behavior.
    """
    from services.whatsapp_service import send_whatsapp_smart

    provider = _make_provider(success=True, message_id="wamid.env_free")

    with patch("services.communication.template_service.is_within_24h_window", return_value=False), \
         patch("services.communication.provider_factory.get_whatsapp_provider_for_agency",
               new=AsyncMock(return_value=(provider, None))), \
         patch("database.supabase_client.get_supabase", return_value=_make_sb_empty()):

        result = await send_whatsapp_smart(
            AGENCY_ID, LEAD_ID, PHONE, BODY,
            agent_id=AGENT_ID, template_name=TMPL_NAME, last_inbound_at=None,
        )

    assert result["status"] == "sent"
    assert result["sid"] == "wamid.env_free"
    provider.send_message.assert_awaited_once_with(PHONE, BODY)


# ---- Test 11: Case (b) BYON Account Missing WABA ID -> Queues, Not Sends -----


@pytest.mark.asyncio
async def test_smart_send_byon_account_missing_waba_id_queues_not_sends():
    """
    Case (b): account is NOT None but has an empty external_account_id (broken/misconfigured BYON).
    Outside 24h window, sending free-form would fail on Meta (131047).
    Must NOT attempt free-form send. Must log critical and queue + notify agent.
    """
    from services.whatsapp_service import send_whatsapp_smart

    provider = _make_provider()
    account_no_waba = _make_account(waba_id="")  # BYON row, but empty external_account_id

    queue_inserts: list[dict] = []

    def _make_ch(name):
        ch = MagicMock()
        ch.select.return_value = ch
        ch.eq.return_value = ch
        ch.limit.return_value = ch

        def _ins(payload):
            if name == "whatsapp_outbound_queue":
                queue_inserts.append(payload)
            ins_ch = MagicMock()
            ins_ch.execute.return_value = MagicMock(data=[{"id": "q-byon-err"}])
            return ins_ch

        ch.insert.side_effect = _ins
        if name == "leads":
            ch.execute.return_value = MagicMock(data=[{
                "name": "Bob Lead", "phone": PHONE, "assigned_agent_id": AGENT_ID,
            }])
        elif name == "agents":
            ch.execute.return_value = MagicMock(data=[{
                "name": "Agent A", "phone": AGENT_PHONE, "whatsapp_number": AGENT_PHONE,
            }])
        else:
            ch.execute.return_value = MagicMock(data=[])
        return ch

    mock_sb = MagicMock()
    mock_sb.table.side_effect = _make_ch
    mock_free_send = AsyncMock(return_value=FAKE_SENT)

    with patch("services.communication.template_service.is_within_24h_window", return_value=False), \
         patch("services.communication.provider_factory.get_whatsapp_provider_for_agency",
               new=AsyncMock(return_value=(provider, account_no_waba))), \
         patch("database.supabase_client.get_supabase", return_value=mock_sb), \
         patch("services.whatsapp_service.send_whatsapp_for_agency", new=mock_free_send):

        result = await send_whatsapp_smart(
            AGENCY_ID, LEAD_ID, PHONE, BODY,
            agent_id=AGENT_ID, template_name=TMPL_NAME, last_inbound_at=None,
        )

    # Asserts
    assert result["status"] == "queued"
    assert result["lead_id"] == LEAD_ID
    # Must NOT call provider.send_message (no free-form send via provider)
    provider.send_message.assert_not_awaited()
    # Must NOT call send_whatsapp_for_agency to the lead (only for agent alert)
    for call in mock_free_send.await_args_list:
        args, _ = call
        # Arg 1 is phone; should be AGENT_PHONE for notification alert, NEVER lead PHONE
        assert args[1] != PHONE
    # Verify queued with reason "misconfigured_waba"
    assert len(queue_inserts) == 1
    assert queue_inserts[0]["reason"] == "misconfigured_waba"


# ---- Test 12: Drain Send Failure -> Marks Failed and Notifies Agent ----------


@pytest.mark.asyncio
async def test_drain_send_failure_marks_failed_and_notifies_agent():
    """
    When sending a claimed queued message fails during drain, the row status is
    updated to 'failed' and the assigned agent is sent a 'Queued Message Delivery Failed' alert.
    """
    from services.whatsapp_service import drain_outbound_queue_for_lead

    queued_item = {
        "id": "q-fail-001", "agency_id": AGENCY_ID, "agent_id": AGENT_ID,
        "lead_id": LEAD_ID, "to_phone": PHONE, "body": "Follow-up that will fail",
    }

    status_updates: list[dict] = []
    notify_calls: list[dict] = []

    def _make_ch(name):
        ch = MagicMock()
        ch.select.return_value = ch
        ch.eq.return_value = ch
        ch.limit.return_value = ch

        def _upd(payload):
            status_updates.append(payload)
            u = MagicMock()
            u.eq.return_value = u
            u.execute.return_value = MagicMock(data=[])
            return u

        ch.update.side_effect = _upd

        if name == "leads":
            ch.execute.return_value = MagicMock(data=[{
                "name": "Failed Lead", "phone": PHONE, "assigned_agent_id": AGENT_ID,
            }])
        elif name == "agents":
            ch.execute.return_value = MagicMock(data=[{
                "name": "Agent A", "phone": AGENT_PHONE, "whatsapp_number": AGENT_PHONE,
            }])
        else:
            ch.execute.return_value = MagicMock(data=[])
        return ch

    mock_sb = MagicMock()
    mock_sb.table.side_effect = _make_ch
    rpc_mock = MagicMock()
    rpc_mock.execute.return_value = MagicMock(data=[queued_item])
    mock_sb.rpc.return_value = rpc_mock

    async def _mock_send(agency_id, phone, body, *, agent_id=None):
        if phone == PHONE:
            # Lead send fails
            return {"status": "error", "error": "Provider connection timeout"}
        elif phone == AGENT_PHONE:
            # Agent alert succeeds
            notify_calls.append({"phone": phone, "body": body})
            return FAKE_SENT
        return FAKE_SENT

    with patch("database.supabase_client.get_supabase", return_value=mock_sb), \
         patch("services.whatsapp_service.send_whatsapp_for_agency", side_effect=_mock_send):

        drained = await drain_outbound_queue_for_lead(
            lead_id=LEAD_ID, agency_id=AGENCY_ID, agent_id=AGENT_ID
        )

    # 0 drained because send failed
    assert drained == 0
    # Status was marked as 'failed' in DB
    assert any(u.get("status") == "failed" for u in status_updates)
    # Agent was notified of the failure
    assert len(notify_calls) == 1
    assert notify_calls[0]["phone"] == AGENT_PHONE
    assert "Queued Message Delivery Failed" in notify_calls[0]["body"]
    assert "Provider connection timeout" in notify_calls[0]["body"]
