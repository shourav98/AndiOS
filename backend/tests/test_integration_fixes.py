"""
Integration-fixes verification suite.

Covers the fixes from the production-readiness pass:
- auth: change-password, profile-update (new endpoints)
- subscription: cancel endpoint, fail-closed checkout, role gates, honest
  contract/payment-method/invoice responses
- connectors: whatsapp/test management gate + agency-scoped write
- viewings: cross-agency calendar fallback removed, foreign agent rejected
- campaigns: run gated to management, quota enforced at creation
- dashboard: calling-performance + overview AI stats computed from real rows
- leads: sanitized search, total count, paginated envelope
- owners: PATCH/DELETE, bulk row caps
- reports: no fabricated demo data

Tests invoke route/service functions directly (no TestClient / main import).
Run: pytest tests/test_integration_fixes.py -v
"""
import csv
import io
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import HTTPException

from config import settings

OWNER = {"sub": "u-owner", "email": "o@a.com", "agency_id": "ag-1", "role": "owner", "agent_id": "a-1"}
MANAGER = {**OWNER, "sub": "u-mgr", "email": "m@a.com", "agent_id": "a-2"}
AGENT = {**OWNER, "sub": "u-agent", "email": "ag@a.com", "role": "agent", "agent_id": "a-3"}

def _sb():
    return MagicMock()

# ─── AUTH: change-password ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_change_password_happy_path():
    sb = _sb()
    from routers.auth import change_password, ChangePasswordRequest
    with patch("routers.auth.get_supabase", return_value=sb):
        result = await change_password(
            ChangePasswordRequest(current_password="OldPass123", new_password="NewPass456", confirm_password="NewPass456"),
            OWNER,
        )
    assert result["data"]["status"] == "password_changed"
    sb.auth.admin.update_user_by_id.assert_called_once_with(OWNER["sub"], {"password": "NewPass456"})
    sb.auth.sign_in_with_password.assert_called_once_with({"email": OWNER["email"], "password": "OldPass123"})


@pytest.mark.asyncio
async def test_change_password_wrong_current_rejected():
    sb = _sb()
    sb.auth.sign_in_with_password.side_effect = Exception("invalid credentials")
    from routers.auth import change_password, ChangePasswordRequest
    with patch("routers.auth.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as e:
            await change_password(
                ChangePasswordRequest(current_password="WrongPass", new_password="NewPass456"),
                OWNER,
            )
    assert e.value.status_code == 400
    sb.auth.admin.update_user_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_change_password_confirm_mismatch_and_short_password():
    from routers.auth import change_password, ChangePasswordRequest
    sb = _sb()
    with patch("routers.auth.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as e1:
            await change_password(
                ChangePasswordRequest(current_password="x", new_password="NewPass456", confirm_password="Different"),
                OWNER,
            )
        with pytest.raises(HTTPException) as e2:
            await change_password(
                ChangePasswordRequest(current_password="x", new_password="short"),
                OWNER,
            )
    assert e1.value.status_code == 400
    assert e2.value.status_code == 422
    sb.auth.admin.update_user_by_id.assert_not_called()


# ─── AUTH: profile update ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_profile_update_happy_scopes_to_caller():
    sb = _sb()
    updated = {"id": "a-1", "name": "New Name"}
    sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [updated]
    from routers.auth import update_my_profile, ProfileUpdateRequest
    with patch("routers.auth.get_supabase", return_value=sb):
        result = await update_my_profile(ProfileUpdateRequest(name="New Name", phone="+971500000000"), OWNER)
    assert result["data"]["name"] == "New Name"
    eq_mock = sb.table.return_value.update.return_value.eq
    eq_mock.assert_any_call("email", OWNER["email"])
    eq_mock.return_value.eq.assert_any_call("agency_id", OWNER["agency_id"])


@pytest.mark.asyncio
async def test_profile_update_empty_body_400():
    from routers.auth import update_my_profile, ProfileUpdateRequest
    with patch("routers.auth.get_supabase", return_value=_sb()):
        with pytest.raises(HTTPException) as e:
            await update_my_profile(ProfileUpdateRequest(), OWNER)
    assert e.value.status_code == 400


# ─── BILLING: fail-closed checkout ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_checkout_simulation_works_in_development_only():
    from services.billing_service import create_checkout_session
    with patch.object(settings, "APP_ENV", "development"), \
         patch.object(settings, "STRIPE_SECRET_KEY", ""), \
         patch("services.billing_service.get_or_create_stripe_price", return_value=None), \
         patch("services.billing_service._upsert_local_subscription") as mock_upsert:
        result = await create_checkout_session("ag-1", "grow", "e@a.com", "Agency", "https://fe/ok", "https://fe/ko")
    assert result["mode"] == "simulation"
    mock_upsert.assert_called_once()


@pytest.mark.asyncio
async def test_checkout_fails_closed_in_production_without_stripe():
    from services.billing_service import create_checkout_session
    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "STRIPE_SECRET_KEY", ""), \
         patch("services.billing_service.get_or_create_stripe_price", return_value=None), \
         patch("services.billing_service._upsert_local_subscription") as mock_upsert:
        with pytest.raises(ValueError):
            await create_checkout_session("ag-1", "grow", "e@a.com", "Agency", "https://fe/ok", "https://fe/ko")
    mock_upsert.assert_not_called()  # no plan activated


# ─── BILLING: cancel endpoint ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_subscription_immediate_dev_local():
    sb = _sb()
    sub_row = {"id": "s1", "stripe_sub_id": None, "billing_cycle_end": "2026-09-01T00:00:00Z", "status": "active"}
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = sub_row
    from services.billing_service import cancel_subscription
    with patch.object(settings, "APP_ENV", "development"), \
         patch.object(settings, "STRIPE_SECRET_KEY", ""), \
         patch("services.billing_service.get_supabase", return_value=sb), \
         patch("services.billing_service._upsert_local_subscription") as mock_upsert:
        result = await cancel_subscription("ag-1", at_period_end=False)
    assert result["status"] == "canceled"
    mock_upsert.assert_called_with("ag-1", {"status": "canceled"})


@pytest.mark.asyncio
async def test_cancel_subscription_period_end_via_stripe():
    sb = _sb()
    sub_row = {"id": "s1", "stripe_sub_id": "sub_123", "billing_cycle_end": "2026-09-01T00:00:00Z", "status": "active"}
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = sub_row
    from services.billing_service import cancel_subscription
    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "STRIPE_SECRET_KEY", "sk_test_x"), \
         patch("services.billing_service.get_supabase", return_value=sb), \
         patch("stripe.Subscription.modify") as mock_modify:
        result = await cancel_subscription("ag-1", at_period_end=True)
    mock_modify.assert_called_once_with("sub_123", cancel_at_period_end=True)
    assert result["status"] == "active_until_period_end"
    assert result["access_until"] == "2026-09-01T00:00:00Z"


@pytest.mark.asyncio
async def test_cancel_endpoint_requires_owner():
    from routers.subscription import cancel_subscription_handler, CancelSubscriptionRequest
    with pytest.raises(HTTPException) as e:
        await cancel_subscription_handler(CancelSubscriptionRequest(), AGENT)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_cancel_endpoint_maps_valueerror_to_400():
    from routers.subscription import cancel_subscription_handler, CancelSubscriptionRequest
    with patch("routers.subscription.cancel_subscription", side_effect=ValueError("No active Stripe subscription found for this agency.")), \
         patch("routers.subscription.require_agency_id", return_value="ag-1"):
        with pytest.raises(HTTPException) as e:
            await cancel_subscription_handler(CancelSubscriptionRequest(at_period_end=False), OWNER)
    assert e.value.status_code == 400


# ─── BILLING: role gates on previously ungated GETs ──────────────────────────

@pytest.mark.asyncio
async def test_single_invoice_agent_forbidden_owner_allowed():
    from routers.subscription import get_single_invoice
    with pytest.raises(HTTPException) as e:
        await get_single_invoice("139350-01", AGENT)
    assert e.value.status_code == 403

    sb = _sb()
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
        "invoice_number": "INV-1", "amount": 100,
    }
    with patch("routers.subscription.get_supabase", return_value=sb), \
         patch("routers.subscription.require_agency_id", return_value="ag-1"):
        result = await get_single_invoice("INV-1", OWNER)
    assert result["data"]["invoice_number"] == "INV-1"


@pytest.mark.asyncio
async def test_payment_method_get_role_gate_and_no_mock_card():
    from routers.subscription import get_saved_payment_method
    with pytest.raises(HTTPException) as e:
        await get_saved_payment_method(AGENT)
    assert e.value.status_code == 403

    with patch.object(settings, "STRIPE_SECRET_KEY", ""), \
         patch("routers.subscription.require_agency_id", return_value="ag-1"):
        result = await get_saved_payment_method(OWNER)
    body = json.dumps(result)
    assert result["data"]["saved"] is False
    assert "4242" not in body and "visa" not in body.lower()


@pytest.mark.asyncio
async def test_contract_overview_gate_and_no_fabricated_constants():
    from routers.subscription import get_subscription_contract_overview
    with pytest.raises(HTTPException) as e:
        req = type("R", (), {})()  # request unused in handler logic path
        await get_subscription_contract_overview(req, AGENT)
    assert e.value.status_code == 403

    sb = _sb()
    sub_row = {"plan_tier": "basic", "status": "active",
               "stripe_sub_id": None,
               "billing_cycle_start": "2026-08-01T00:00:00Z",
               "billing_cycle_end": "2026-09-01T00:00:00Z"}
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = sub_row
    fake_request = type("R", (), {"base_url": "http://testserver/"})()
    with patch.object(settings, "STRIPE_SECRET_KEY", "sk_test_x"), \
         patch("routers.subscription.get_supabase", return_value=sb), \
         patch("routers.subscription.require_agency_id", return_value="ag-1"):
        result = await get_subscription_contract_overview(fake_request, OWNER)

    data = result["data"]
    body = json.dumps(data)
    assert "Sara Al Owais" not in body and "139350" not in body
    assert data["price_details"]["gross_amount_aed"] == 1400 * 12  # basic plan × 12
    assert abs(data["price_details"]["vat_5_percent_aed"] - round(1400 * 12 * 0.05, 2)) < 0.01
    assert data["duration_start"] == "2026-08-01T00:00:00Z"


@pytest.mark.asyncio
async def test_invoices_list_has_no_mock_fallback():
    from routers.subscription import list_invoices
    sb = _sb()
    sb.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []
    with patch.object(settings, "STRIPE_SECRET_KEY", ""), \
         patch("routers.subscription.get_supabase", return_value=sb), \
         patch("routers.subscription.fetch_and_sync_live_invoices", new_callable=AsyncMock) as mock_sync, \
         patch("routers.subscription.require_agency_id", return_value="ag-1"):
        mock_sync.return_value = []
        result = await list_invoices(status=None, refresh=False, current_user=OWNER)
    assert result["data"]["invoices"] == []
    assert result["data"]["total"] == 0


# ─── CONNECTORS: whatsapp/test gate + scoped write ────────────────────────────

@pytest.mark.asyncio
async def test_whatsapp_test_agent_forbidden_owner_scoped_write():
    from routers.connectors import test_whatsapp
    with pytest.raises(HTTPException) as e:
        await test_whatsapp(to_phone="+971500000001", current_user=AGENT)
    assert e.value.status_code == 403

    sb = _sb()
    with patch("routers.connectors.send_whatsapp_message", new_callable=AsyncMock) as mock_send, \
         patch("routers.connectors.get_supabase", return_value=sb), \
         patch("routers.connectors.require_agency_id", return_value="ag-1"), \
         patch.object(settings, "WHATSAPP_PROVIDER", "twilio"):
        mock_send.return_value = {"status": "sent", "sid": "SM1"}
        result = await test_whatsapp(to_phone="+971500000001", current_user=OWNER)

    assert result["success"] is True
    upd_eq = sb.table.return_value.update.return_value.eq
    upd_eq.assert_any_call("name", "whatsapp")
    upd_eq.return_value.eq.assert_any_call("agency_id", "ag-1")


# ─── VIEWINGS: tenant isolation ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calendar_token_never_falls_back_cross_agency():
    """Even when an unscoped connector exists, a missing own-agency connector must 400."""
    from routers.viewings import _get_calendar_token
    sb = MagicMock()
    # Full scoped chain: select -> eq(name) -> eq(agency_id) -> eq(is_connected) -> limit -> execute
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    with pytest.raises(HTTPException) as e:
        _get_calendar_token(sb, "ag-1")  # sync helper — no await
    assert e.value.status_code == 400
    assert "not connected" in e.value.detail.lower()


@pytest.mark.asyncio
async def test_available_slots_foreign_agent_404_and_error_leak_fixed():
    from routers.viewings import available_slots
    sb = MagicMock()
    # Connector chain: select -> eq(name) -> eq(agency) -> eq(is_connected) -> limit -> execute
    conn_exec = (
        sb.table.return_value.select.return_value.eq.return_value
        .eq.return_value.eq.return_value.limit.return_value.execute
    )
    conn_exec.return_value.data = [{"auth_data": {"token": "tok"}}]
    # Agent lookup: select -> eq(id) -> eq(agency) -> execute (shares 2nd-eq node with connector)
    agent_exec = (
        sb.table.return_value.select.return_value.eq.return_value
        .eq.return_value.execute
    )
    agent_exec.return_value.data = []  # foreign agent → not found

    with patch("routers.viewings.get_supabase", return_value=sb), \
         patch.object(settings, "GOOGLE_SHARED_CALENDAR_ID", ""):
        with pytest.raises(HTTPException) as e:
            await available_slots(
                date_from="2026-09-01T00:00:00Z", date_to="2026-09-02T00:00:00Z",
                agent_id="foreign-agent-id", duration_minutes=60, current_user=OWNER,
            )
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_available_slots_generic_error_does_not_leak():
    from routers.viewings import available_slots
    sb = MagicMock()
    conn_exec = (
        sb.table.return_value.select.return_value.eq.return_value
        .eq.return_value.eq.return_value.limit.return_value.execute
    )
    conn_exec.return_value.data = [{"auth_data": {"token": "tok"}}]

    async def not_used():
        pass

    def boom(*a, **k):
        raise Exception("internal google secret xyz")

    with patch("routers.viewings.get_supabase", return_value=sb), \
         patch("routers.viewings.get_available_slots", side_effect=boom), \
         patch.object(settings, "GOOGLE_SHARED_CALENDAR_ID", "cal"):
        with pytest.raises(HTTPException) as e:
            await available_slots(
                date_from="2026-09-01T00:00:00Z", date_to="2026-09-02T00:00:00Z",
                agent_id=None, duration_minutes=60, current_user=OWNER,
            )
    assert e.value.status_code == 500
    assert "xyz" not in e.value.detail


# ─── CAMPAIGNS: gates + quota ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_campaign_create_enforces_quota():
    from routers.call_campaigns import create_call_campaign, CallCampaignCreate
    with patch("routers.call_campaigns.check_campaign_limit", side_effect=HTTPException(403, "Campaign limit reached")), \
         patch("routers.call_campaigns.require_agency_id", return_value="ag-1"):
        with pytest.raises(HTTPException) as e:
            await create_call_campaign(CallCampaignCreate(campaign_name="Q3 push", group="Marina"), OWNER)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_campaign_run_agent_forbidden_manager_allowed():
    from routers.call_campaigns import run_campaign
    with pytest.raises(HTTPException) as e:
        await run_campaign("camp-1", AGENT)
    assert e.value.status_code == 403

    sb = _sb()
    sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": "camp-1", "status": "Scheduled",
    }
    async def noop_batch(*a, **k):
        return None
    with patch("routers.call_campaigns.is_management_role", lambda r: r in ("owner", "manager")), \
         patch("routers.call_campaigns.require_agency_id", return_value="ag-1"), \
         patch("routers.call_campaigns.get_plan_limits", return_value={"plan": "starter"}), \
         patch("services.vapi_service.run_campaign_batch", new_callable=AsyncMock, side_effect=noop_batch), \
         patch("services.scheduler.scheduler.get_job", return_value=None), \
         patch("services.scheduler.scheduler.add_job"), \
         patch("routers.call_campaigns.get_supabase", return_value=sb):
        result = await run_campaign("camp-1", MANAGER)
    assert result["data"]["status"] == "Running"


@pytest.mark.asyncio
async def test_campaign_run_blocked_when_suspended():
    from routers.call_campaigns import run_campaign
    with patch("routers.call_campaigns.is_management_role", lambda r: True), \
         patch("routers.call_campaigns.require_agency_id", return_value="ag-1"), \
         patch("routers.call_campaigns.get_plan_limits", side_effect=HTTPException(403, "inactive")):
        with pytest.raises(HTTPException) as e:
            await run_campaign("camp-1", MANAGER)
    assert e.value.status_code == 403


# ─── DASHBOARD: real data ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calling_performance_computed_from_real_rows():
    from routers.dashboard import get_calling_performance
    rows = [
        {"id": "c1", "owner_name": "A", "property_location": "Marina", "status": "Listing won",
         "status_value": "listing-won", "duration_seconds": 260, "audio_url": "http://x/a.mp3",
         "call_time": "2026-08-20T09:12:00Z"},
        {"id": "c2", "owner_name": "B", "property_location": "JVC", "status": "No answer",
         "status_value": "no-answer", "duration_seconds": 0, "audio_url": None,
         "call_time": "2026-08-21T10:00:00Z"},
    ]
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value.data = rows
    with patch("routers.dashboard.get_supabase", return_value=sb):
        result = await get_calling_performance(OWNER)

    data = result["data"]
    assert data["callsThisWeek"] == "2"
    assert data["answerRate"] == "50.0%"
    assert data["callsToListings"] == "100.0%"
    assert data["funnel"][0]["value"] == 2
    assert data["funnel"][3]["value"] == 1
    assert data["recentCalls"][0]["name"] == "A"
    assert data["recentCalls"][0]["duration"] == "4:20"
    assert data["recentCalls"][1]["hasAudio"] is False
    body = json.dumps(data)
    assert "1,245" not in body and "Sarah Miller" not in body  # old fabrications gone


@pytest.mark.asyncio
async def test_dashboard_overview_ai_stats_real_and_avg_response_null():
    from routers.dashboard import get_dashboard_overview
    call_rows = [
        {"status_value": "listing-won"}, {"status_value": "no-answer"}, {"status_value": "interested"},
    ]
    sb = MagicMock()
    agent_row = {"id": "a-1", "name": "Owner One", "role": "owner", "agency_id": "ag-1"}
    # agents .single() chain
    sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = agent_row
    # generic execute chains → empty (leads/viewings/contracts/prev/agents-map)
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    sb.table.return_value.select.return_value.execute.return_value.data = []
    # calls chain (gte → lte → execute)
    sb.table.return_value.select.return_value.eq.return_value.gte.return_value.lte.return_value.execute.return_value.data = call_rows
    # agents map (owner): select id,name eq agency → reuse generic [] fine

    with patch("routers.dashboard.get_supabase", return_value=sb):
        result = await get_dashboard_overview(timeframe=None, start_date="2026-08-01", end_date="2026-08-24", current_user=OWNER)

    ai = result["data"]["ai_agent_stats"]
    assert ai["outbound_dials"] == 3
    assert ai["new_listings_won"] == 1
    assert result["data"]["metrics"]["avg_response_time"]["value"] is None


# ─── LEADS: sanitize + total + envelope ───────────────────────────────────────

@pytest.mark.asyncio
async def test_leads_search_sanitized_and_total_returned():
    from routers.leads import list_leads
    sb = MagicMock()
    main_rows = [{"id": "l1", "name": "John", "phone": "+9715", "source": "bayut", "status": "new"}]
    # Count query: select.rv -> eq(scope).rv -> execute   (fresh select, no order)
    count_resp = MagicMock(); count_resp.count = 42
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = count_resp
    # Main query: select.rv -> order.rv -> eq(scope).rv -> or_(search).rv -> range.rv -> execute
    or_node = (
        sb.table.return_value.select.return_value.order.return_value.eq.return_value.or_
    )
    range_resp = MagicMock(); range_resp.data = main_rows; range_resp.count = None
    or_node.return_value.range.return_value.execute.return_value = range_resp

    with patch("routers.leads.get_supabase", return_value=sb):
        result = await list_leads(
            status=None, source=None, agent_id=None, is_ai_handling=None,
            search="jo%,()", limit=50, offset=0, _=OWNER,
        )

    payload = result["data"]
    assert set(payload.keys()) == {"leads", "total", "limit", "offset"}
    assert payload["total"] == 42
    used = or_node.call_args[0][0]
    assert "name.ilike.%jo%" in used
    assert "(" not in used and ")" not in used and ",%" not in used


@pytest.mark.asyncio
async def test_leads_envelope_shape_is_paginated_dict():
    from routers.leads import list_leads
    sb = MagicMock()
    main_rows = [{"id": "l1", "name": "A", "phone": "+971500000001", "source": "direct", "status": "new"}]
    count_resp = MagicMock(); count_resp.count = 1
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = count_resp
    range_node = (
        sb.table.return_value.select.return_value.order.return_value.eq.return_value.range
    )
    range_resp = MagicMock(); range_resp.data = main_rows; range_resp.count = None
    range_node.return_value.execute.return_value = range_resp

    with patch("routers.leads.get_supabase", return_value=sb):
        result = await list_leads(status=None, source=None, agent_id=None, is_ai_handling=None,
                                  search=None, limit=50, offset=0, _=MANAGER)
    lead = result["data"]["leads"][0]
    assert lead["clientName"] == "A" and "stage" in lead and "dealValue" in lead
    assert result["data"]["total"] == 1


# ─── OWNERS: PATCH/DELETE/caps ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_patch_and_delete():
    from routers.owners import update_owner, delete_owner, OwnerUpdate
    sb = MagicMock()
    sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{"id": "o1", "name": "Renamed"}]
    with patch("routers.owners.get_supabase", return_value=sb):
        result = await update_owner("o1", OwnerUpdate(name="Renamed"), OWNER)
    assert result["data"]["name"] == "Renamed"

    with patch("routers.owners.get_supabase", return_value=_sb()):
        with pytest.raises(HTTPException) as e:
            await delete_owner("o1", AGENT)
    assert e.value.status_code == 403

    sb2 = MagicMock()
    sb2.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{"id": "o1"}]
    with patch("routers.owners.get_supabase", return_value=sb2):
        result = await delete_owner("o1", OWNER)
    assert result["success"] is True


@pytest.mark.asyncio
async def test_bulk_upload_row_cap_enforced():
    from routers.owners import bulk_upload_owners, BulkUploadRequest, OwnerCreate
    owners = [OwnerCreate(name=f"N{i}", phone=f"+97150{i:07d}") for i in range(5001)]
    with patch("routers.owners.get_supabase", return_value=_sb()):
        with pytest.raises(HTTPException) as e:
            await bulk_upload_owners(BulkUploadRequest(owners=owners), OWNER)
    assert e.value.status_code == 400
    assert "5000" in e.value.detail


@pytest.mark.asyncio
async def test_csv_parse_failure_no_internal_leak():
    from routers.owners import bulk_upload_owners_csv
    upload = MagicMock()
    upload.filename = "broken.csv"

    async def bad_read():
        raise Exception("binary garbage \x00\xff internal stack trace")

    upload.read = bad_read
    with patch("routers.owners.get_supabase", return_value=_sb()):
        with pytest.raises(HTTPException) as e:
            await bulk_upload_owners_csv(file=upload, current_user=OWNER)
    assert e.value.status_code == 500
    assert e.value.detail == "Failed to parse CSV file"
    assert "\x00" not in e.value.detail


# ─── REPORTS: fabricated data purged ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_dashboard_returns_no_demo_data_for_any_name():
    from routers.reports import get_owner_dashboard
    owner_row = {"id": "ow1", "name": "Khalifa Al Mansoori", "property_group": ""}
    sb = MagicMock()
    # owners query: select -> eq(id) -> eq(agency) -> execute
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [owner_row]
    # leads/viewings single-eq queries → empty
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    # owner_reports: eq → order → limit → execute
    sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []

    with patch("routers.reports.get_supabase", return_value=sb), \
         patch("routers.reports.require_agency_id", return_value="ag-1"):
        result = await get_owner_dashboard("ow1", OWNER)

    data = result["data"]
    assert data["listings"] == []          # khalifa demo block must NOT fire
    assert data["viewing_feedbacks"] == []
    blob = json.dumps(data)
    assert "rating" not in blob and "recommendation" not in blob and "insight" not in blob
    assert "Dubai Marina" not in blob and "Palm Jumeirah" not in blob  # demo properties gone
    assert data["weekly_message"]["owner_reply"] is None

# --- FINAL VERIFICATION ADDITIONS ---------------------------------------------

@pytest.mark.asyncio
async def test_payment_method_rejects_raw_pan_in_production():
    """C-1 guard: raw card numbers must be rejected in production."""
    from routers.subscription import update_payment_method_handler, PaymentMethodRequest
    body = PaymentMethodRequest(card_number="4242424242424242", cvc="123", expiry="12/30")
    with patch.object(settings, "APP_ENV", "production"), \
         patch("routers.subscription.require_agency_id", return_value="ag-1"), \
         patch("routers.subscription.update_saved_payment_method_info") as mock_update:
        with pytest.raises(HTTPException) as e:
            await update_payment_method_handler(body, OWNER)
    assert e.value.status_code == 400
    assert "tokenize" in e.value.detail.lower()
    mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_payment_method_accepts_tokenized_pm_in_production():
    from routers.subscription import update_payment_method_handler, PaymentMethodRequest
    body = PaymentMethodRequest(payment_method_id="pm_test_123")
    expected = {"card_brand": "visa", "card_last4": "4242"}
    with patch.object(settings, "APP_ENV", "production"), \
         patch("routers.subscription.require_agency_id", return_value="ag-1"), \
         patch("routers.subscription.update_saved_payment_method_info", new_callable=AsyncMock) as mock_update:
        mock_update.return_value = expected
        result = await update_payment_method_handler(body, OWNER)
    assert result["data"]["card_last4"] == "4242"
    mock_update.assert_awaited_once()
    kwargs = mock_update.await_args.kwargs
    assert kwargs["payment_method_id"] == "pm_test_123"


@pytest.mark.asyncio
async def test_contract_pdf_uses_signed_url_never_public(tmp_path):
    """C-4: generated contracts must produce SIGNED urls, never public ones."""
    from services.contract_service import _generate_pdf
    contract = {
        "id": "11111111-2222-3333-4444-555555555555",
        "owner_name": "Owner", "owner_emirates_id": "ID", "owner_phone": "+9715",
        "tenant_name": "Tenant", "tenant_emirates_id": "ID2",
        "property_address": "Tower A", "rent_amount": 100000,
        "start_date": "2026-01-01", "end_date": "2026-12-31", "security_deposit": 5000,
    }
    sb = MagicMock()
    storage = sb.storage.from_.return_value
    storage.upload.return_value = {"Key": "x"}
    storage.create_signed_url.return_value = {"signedURL": "/object/sign/contracts/contract_x.pdf?token=abc"}
    storage.get_public_url.return_value = "https://PUBLIC.example/contract_x.pdf"

    with patch("database.supabase_client.get_supabase", return_value=sb), \
         patch("services.contract_service.SUPABASE_STORAGE_BUCKET", "contracts"):
        url = await _generate_pdf(contract, {"name": "T", "phone": "+9715", "email": "t@x.com"})

    storage.create_signed_url.assert_called_once()
    storage.get_public_url.assert_not_called()          # public URLs are banned
    assert "/sign/" in url
    assert "PUBLIC.example" not in url


def test_cors_origins_environment_aware():
    from main import _cors_allow_origins
    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "FRONTEND_URL", "https://andi-os.vercel.app"):
        origins = _cors_allow_origins()
    assert origins == ["https://andi-os.vercel.app"]          # no localhost in prod

    with patch.object(settings, "APP_ENV", "development"), \
         patch.object(settings, "FRONTEND_URL", "http://localhost:3000"):
        dev = _cors_allow_origins()
    assert "http://localhost:5173" in dev and dev[0] == "http://localhost:3000"


def test_production_config_validator_reports_missing_secrets(caplog):
    import logging
    from main import _validate_production_config
    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "STRIPE_SECRET_KEY", ""), \
         patch.object(settings, "STRIPE_WEBHOOK_SECRET", ""), \
         patch.object(settings, "WHATSAPP_WEBHOOK_TOKEN", ""), \
         patch.object(settings, "VAPI_WEBHOOK_SECRET", ""), \
         patch.object(settings, "BAYUT_WEBHOOK_TOKEN", ""), \
         patch.object(settings, "DUBIZZLE_WEBHOOK_TOKEN", ""), \
         patch.object(settings, "PROPERTY_FINDER_WEBHOOK_SECRET", "set"), \
         caplog.at_level(logging.CRITICAL):
        _validate_production_config()
    joined = " ".join(r.getMessage() for r in caplog.records)
    for secret in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "WHATSAPP_WEBHOOK_TOKEN",
                   "VAPI_WEBHOOK_SECRET", "BAYUT_WEBHOOK_TOKEN", "DUBIZZLE_WEBHOOK_TOKEN"):
        assert secret in joined


@pytest.mark.asyncio
async def test_conversation_send_foreign_lead_blocked_before_provider_call():
    from routers.conversations import agent_send_message, SendMessageRequest
    with patch("routers.conversations.verify_lead_access",
               new_callable=AsyncMock,
               side_effect=HTTPException(403, "Access denied")), \
         patch("routers.conversations.send_whatsapp_message", new_callable=AsyncMock) as mock_send:
        with pytest.raises(HTTPException) as e:
            await agent_send_message(
                lead_id="00000000-0000-0000-0000-000000000001",
                body=SendMessageRequest(message_body="hi"),
                user_id="u1",
                current_user=AGENT,
            )
    assert e.value.status_code == 403
    mock_send.assert_not_called()                     # provider never touched cross-tenant

# --- INVOICE SUMMARY: global counts regardless of ?status= filter -------------

def _inv(num, status):
    return {"invoice_number": num, "status": status, "amount": 100, "due_date": "2026-01-01"}

UNPAID_ROWS = [_inv("U1", "unpaid"), _inv("U2", "unpaid"), _inv("U3", "upcoming")]
PAID_ROWS = [_inv("P1", "paid")]
ALL_STATUS_ROWS = [{"status": "paid"}] + [{"status": r["status"]} for r in UNPAID_ROWS]


async def _run_invoice_list(status, sb, user=None):
    from routers.subscription import list_invoices
    with patch.object(settings, "STRIPE_SECRET_KEY", ""), \
         patch("routers.subscription.get_supabase", return_value=sb), \
         patch("routers.subscription.require_agency_id", return_value="ag-1"), \
         patch("routers.subscription.get_saved_payment_method_info", new_callable=AsyncMock) as mock_pm:
        mock_pm.return_value = {"saved": True, "pm": "sentinel"}
        return await list_invoices(status=status, refresh=False, current_user=user or OWNER)


def _summary_nodes(sb):
    """A = builder node after the shared agency_id eq (used by BOTH queries):
      filtered list: A -> eq(status) -> order -> execute
      global summary: A -> execute
    """
    return sb.table.return_value.select.return_value.eq.return_value


@pytest.mark.asyncio
async def test_invoice_summary_global_on_unpaid_filter():
    sb = _sb()
    A = _summary_nodes(sb)
    A.eq.return_value.order.return_value.execute.return_value.data = UNPAID_ROWS  # filtered list
    A.execute.return_value.data = ALL_STATUS_ROWS                                 # global summary

    result = await _run_invoice_list("unpaid", sb)
    d = result["data"]
    assert d["total"] == 3 and len(d["invoices"]) == 3
    assert all(i["status"] in ("unpaid", "upcoming") for i in d["invoices"])
    assert d["summary"] == {"paid_count": 1, "unpaid_count": 3}   # GLOBAL, not filtered


@pytest.mark.asyncio
async def test_invoice_summary_global_on_paid_filter():
    sb = _sb()
    A = _summary_nodes(sb)
    A.eq.return_value.order.return_value.execute.return_value.data = PAID_ROWS
    A.execute.return_value.data = ALL_STATUS_ROWS

    result = await _run_invoice_list("paid", sb)
    d = result["data"]
    assert d["total"] == 1 and len(d["invoices"]) == 1
    assert d["invoices"][0]["status"] == "paid"
    assert d["summary"] == {"paid_count": 1, "unpaid_count": 3}


@pytest.mark.asyncio
async def test_invoice_summary_matches_full_list_when_unfiltered():
    sb = _sb()
    A = _summary_nodes(sb)
    A.order.return_value.execute.return_value.data = PAID_ROWS + UNPAID_ROWS   # no status eq applied
    A.execute.return_value.data = ALL_STATUS_ROWS

    result = await _run_invoice_list(None, sb)
    d = result["data"]
    assert d["total"] == 4
    assert d["summary"] == {"paid_count": 1, "unpaid_count": 3}


@pytest.mark.asyncio
async def test_invoice_summary_degrades_to_filtered_counts_on_query_error():
    sb = _sb()
    A = _summary_nodes(sb)
    A.eq.return_value.order.return_value.execute.return_value.data = UNPAID_ROWS
    A.execute.side_effect = Exception("summary db hiccup")                      # summary query fails

    result = await _run_invoice_list("unpaid", sb)
    d = result["data"]
    assert d["total"] == 3
    assert d["summary"] == {"paid_count": 0, "unpaid_count": 3}  # graceful fallback


@pytest.mark.asyncio
async def test_invoice_payment_method_passthrough_untouched():
    sb = _sb()
    A = _summary_nodes(sb)
    A.order.return_value.execute.return_value.data = []
    A.execute.return_value.data = []

    result = await _run_invoice_list(None, sb)
    assert result["data"]["payment_method"] == {"saved": True, "pm": "sentinel"}
