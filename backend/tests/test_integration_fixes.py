"""
Integration-fixes verification suite (regenerated).

Covers: auth (change-password / profile), billing fail-closed + cancel +
role gates + honest responses, connectors scoping, calendar tenant fixes,
campaign gates/quota, real dashboard data, leads pagination/sanitization,
owners CRUD/caps, reports de-fabrication, Stripe webhook-era regressions,
and Overview B-1/B-2/B-5 targeted fixes.

Direct-call style (no TestClient / main import).
Run: pytest tests/test_integration_fixes.py -v
"""
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock, call
from fastapi import HTTPException
from twilio.request_validator import RequestValidator

from config import settings

OWNER = {"sub": "u-owner", "email": "o@a.com", "agency_id": "ag-1", "role": "owner", "agent_id": "a-1"}
MANAGER = {**OWNER, "sub": "u-mgr", "email": "m@a.com", "role": "manager", "agent_id": "a-2"}
AGENT = {**OWNER, "sub": "u-agent", "email": "ag@a.com", "role": "agent", "agent_id": "a-3"}


def _sb():
    return MagicMock()


def _tables_sb():
    """Supabase mock with an independent MagicMock per table name."""
    sb = MagicMock()
    tables = {}
    sb.table.side_effect = lambda name: tables.setdefault(name, MagicMock())
    return sb, tables


# ─── AUTH: change-password ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_change_password_happy_path():
    sb = _sb()
    from routers.auth import change_password, ChangePasswordRequest
    with patch("routers.auth.get_supabase", return_value=sb):
        result = await change_password(
            ChangePasswordRequest(current_password="OldPass123",
                                  new_password="NewPass456",
                                  confirm_password="NewPass456"),
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
                ChangePasswordRequest(current_password="x", new_password="NewPass456",
                                      confirm_password="Different"),
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
    sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "a-1", "name": "New Name"},
    ]
    from routers.auth import update_my_profile, ProfileUpdateRequest
    with patch("routers.auth.get_supabase", return_value=sb):
        result = await update_my_profile(
            ProfileUpdateRequest(name="New Name", phone="+971500000000"), OWNER,
        )
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
        result = await create_checkout_session("ag-1", "grow", "e@a.com", "Agency",
                                               "https://fe/ok", "https://fe/ko")
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
            await create_checkout_session("ag-1", "grow", "e@a.com", "Agency",
                                          "https://fe/ok", "https://fe/ko")
    mock_upsert.assert_not_called()


# ─── BILLING: subscription cancellation ───────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_subscription_immediate_dev_local():
    sb = _sb()
    sub_row = {"id": "s1", "stripe_sub_id": None,
               "billing_cycle_end": "2026-09-01T00:00:00Z", "status": "active"}
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
    sub_row = {"id": "s1", "stripe_sub_id": "sub_123",
               "billing_cycle_end": "2026-09-01T00:00:00Z", "status": "active"}
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
    with patch("routers.subscription.cancel_subscription",
               side_effect=ValueError("No active Stripe subscription found for this agency.")), \
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
        fake_request = type("R", (), {})()
        await get_subscription_contract_overview(fake_request, AGENT)
    assert e.value.status_code == 403

    sb = _sb()
    sub_row = {"plan_tier": "basic", "status": "active", "stripe_sub_id": None,
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
    assert data["price_details"]["gross_amount_aed"] == 1400 * 12
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
    from routers.viewings import _get_calendar_token
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    with pytest.raises(HTTPException) as e:
        _get_calendar_token(sb, "ag-1")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_available_slots_foreign_agent_404():
    from routers.viewings import available_slots
    sb = MagicMock()
    conn_exec = (sb.table.return_value.select.return_value.eq.return_value
                 .eq.return_value.eq.return_value.limit.return_value.execute)
    conn_exec.return_value.data = [{"auth_data": {"token": "tok"}}]
    agent_exec = (sb.table.return_value.select.return_value.eq.return_value
                  .eq.return_value.execute)
    agent_exec.return_value.data = []  # foreign agent

    with patch("routers.viewings.get_supabase", return_value=sb), \
         patch.object(settings, "GOOGLE_SHARED_CALENDAR_ID", ""):
        with pytest.raises(HTTPException) as e:
            await available_slots(date_from="2026-09-01T00:00:00Z",
                                  date_to="2026-09-02T00:00:00Z",
                                  agent_id="foreign-agent-id",
                                  duration_minutes=60, current_user=OWNER)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_available_slots_generic_error_does_not_leak():
    from routers.viewings import available_slots
    sb = MagicMock()
    conn_exec = (sb.table.return_value.select.return_value.eq.return_value
                 .eq.return_value.eq.return_value.limit.return_value.execute)
    conn_exec.return_value.data = [{"auth_data": {"token": "tok"}}]

    def boom(*a, **k):
        raise Exception("internal google secret xyz")

    with patch("routers.viewings.get_supabase", return_value=sb), \
         patch("routers.viewings.get_available_slots", side_effect=boom), \
         patch.object(settings, "GOOGLE_SHARED_CALENDAR_ID", "cal"):
        with pytest.raises(HTTPException) as e:
            await available_slots(date_from="2026-09-01T00:00:00Z",
                                  date_to="2026-09-02T00:00:00Z",
                                  agent_id=None, duration_minutes=60,
                                  current_user=OWNER)
    assert e.value.status_code == 500
    assert "xyz" not in e.value.detail


# ─── CAMPAIGNS: gates + quota ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_campaign_create_enforces_quota():
    from routers.call_campaigns import create_call_campaign, CallCampaignCreate
    with patch("routers.call_campaigns.check_campaign_limit",
               side_effect=HTTPException(403, "Campaign limit reached")), \
         patch("routers.call_campaigns.require_agency_id", return_value="ag-1"):
        with pytest.raises(HTTPException) as e:
            await create_call_campaign(CallCampaignCreate(campaign_name="Q3 push",
                                                          group="Marina"), OWNER)
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
         patch("routers.call_campaigns.get_plan_limits",
               side_effect=HTTPException(403, "inactive")):
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
    assert "1,245" not in body and "Sarah Miller" not in body


@pytest.mark.asyncio
async def test_dashboard_overview_ai_stats_real_and_avg_response_null():
    from routers.dashboard import get_dashboard_overview
    call_rows = [
        {"status_value": "listing-won"}, {"status_value": "no-answer"}, {"status_value": "interested"},
    ]
    sb = MagicMock()
    agent_row = {"id": "a-owner", "name": "Owner One", "role": "owner", "agency_id": "ag-1"}
    sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = agent_row
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    sb.table.return_value.select.return_value.execute.return_value.data = []
    sb.table.return_value.select.return_value.eq.return_value.gte.return_value.lte.return_value.execute.return_value.data = call_rows

    with patch("routers.dashboard.get_supabase", return_value=sb):
        result = await get_dashboard_overview(timeframe=None, start_date="2026-08-01",
                                              end_date="2026-08-24", current_user=OWNER)

    ai = result["data"]["ai_agent_stats"]
    assert ai["outbound_dials"] == 3
    assert ai["new_listings_won"] == 1
    assert result["data"]["metrics"]["avg_response_time"]["value"] is None


# ─── LEADS: sanitize + total + envelope ───────────────────────────────────────

@pytest.mark.asyncio
async def test_leads_search_sanitized_and_total_returned():
    from routers.leads import list_leads
    sb = _sb()
    row = [{"id": "l1", "name": "John", "phone": "+9715", "source": "bayut", "status": "new"}]
    S = sb.table.return_value.select.return_value
    count_resp = MagicMock(); count_resp.count = 42
    S.eq.return_value.or_.return_value.execute.return_value = count_resp
    or_node = S.eq.return_value.or_
    main = or_node.return_value.order.return_value.range.return_value.execute
    main.return_value.data = row
    main.return_value.count = None

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
    for bad in ("(%", "%)", ",%"):                 # sanitized metacharacters
        assert bad not in used


@pytest.mark.asyncio
async def test_leads_envelope_shape_is_paginated_dict():
    from routers.leads import list_leads
    sb = _sb()
    main_rows = [{"id": "l1", "name": "A", "phone": "+971500000001", "source": "direct", "status": "new"}]
    S = sb.table.return_value.select.return_value
    count_resp = MagicMock(); count_resp.count = 1
    S.eq.return_value.execute.return_value = count_resp
    main = S.eq.return_value.order.return_value.range.return_value.execute
    main.return_value.data = main_rows
    main.return_value.count = None

    with patch("routers.leads.get_supabase", return_value=sb):
        result = await list_leads(status=None, source=None, agent_id=None,
                                  is_ai_handling=None, search=None,
                                  limit=50, offset=0, _=MANAGER)
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
        raise Exception("binary garbage internal stack trace")

    upload.read = bad_read
    with patch("routers.owners.get_supabase", return_value=_sb()):
        with pytest.raises(HTTPException) as e:
            await bulk_upload_owners_csv(file=upload, current_user=OWNER)
    assert e.value.status_code == 500
    assert e.value.detail == "Failed to parse CSV file"


# ─── REPORTS: fabricated data purged ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_dashboard_returns_no_demo_data_for_any_name():
    from routers.reports import get_owner_dashboard
    owner_row = {"id": "ow1", "name": "Khalifa Al Mansoori", "property_group": ""}
    sb = _sb()
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [owner_row]
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []

    with patch("routers.reports.get_supabase", return_value=sb), \
         patch("routers.reports.require_agency_id", return_value="ag-1"):
        result = await get_owner_dashboard("ow1", OWNER)

    data = result["data"]
    assert data["listings"] == []
    assert data["viewing_feedbacks"] == []
    blob = json.dumps(data)
    assert "rating" not in blob and "recommendation" not in blob and "insight" not in blob
    assert "Dubai Marina" not in blob and "Palm Jumeirah" not in blob
    assert data["weekly_message"]["owner_reply"] is None


# ─── STRIPE PAYMENT METHOD: PAN guard (C-1) ───────────────────────────────────

@pytest.mark.asyncio
async def test_payment_method_rejects_raw_pan_in_production():
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
         patch("routers.subscription.update_saved_payment_method_info",
               new_callable=AsyncMock) as mock_update:
        mock_update.return_value = expected
        result = await update_payment_method_handler(body, OWNER)
    assert result["data"]["card_last4"] == "4242"
    kwargs = mock_update.await_args.kwargs
    assert kwargs["payment_method_id"] == "pm_test_123"


# ─── CONTRACT PDF: signed URL (C-4) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_contract_pdf_uses_signed_url_never_public(tmp_path):
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
    storage.get_public_url.assert_not_called()
    assert "/sign/" in url
    assert "PUBLIC.example" not in url


# ─── CORS + production config validator ──────────────────────────────────────

def test_cors_origins_environment_aware():
    from main import _cors_allow_origins
    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "FRONTEND_URL", "https://andi-os.vercel.app"):
        origins = _cors_allow_origins()
    assert origins == ["https://andi-os.vercel.app"]

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
    mock_send.assert_not_called()


# ─── INVOICE SUMMARY: global counts regardless of ?status= filter ─────────────

def _inv(num, status):
    return {"invoice_number": num, "status": status, "amount": 100, "due_date": "2026-01-01"}

UNPAID_ROWS = [_inv("U1", "unpaid"), _inv("U2", "unpaid"), _inv("U3", "upcoming")]
PAID_ROWS = [_inv("P1", "paid")]
ALL_STATUS_ROWS = [{"status": "paid"}] + [{"status": r["status"]} for r in UNPAID_ROWS]


def _invoice_nodes(sb):
    """A = builder node after shared agency_id eq (used by BOTH queries)."""
    return sb.table.return_value.select.return_value.eq.return_value


async def _run_invoice_list(status, sb, user=None):
    from routers.subscription import list_invoices
    with patch.object(settings, "STRIPE_SECRET_KEY", ""), \
         patch("routers.subscription.get_supabase", return_value=sb), \
         patch("routers.subscription.require_agency_id", return_value="ag-1"), \
         patch("routers.subscription.get_saved_payment_method_info", new_callable=AsyncMock) as mock_pm:
        mock_pm.return_value = {"saved": True, "pm": "sentinel"}
        return await list_invoices(status=status, refresh=False, current_user=user or OWNER)


@pytest.mark.asyncio
async def test_invoice_summary_global_on_unpaid_filter():
    sb = _sb(); A = _invoice_nodes(sb)
    A.eq.return_value.order.return_value.execute.return_value.data = UNPAID_ROWS
    A.execute.return_value.data = ALL_STATUS_ROWS

    result = await _run_invoice_list("unpaid", sb)
    d = result["data"]
    assert d["total"] == 3 and len(d["invoices"]) == 3
    assert all(i["status"] in ("unpaid", "upcoming") for i in d["invoices"])
    assert d["summary"] == {"paid_count": 1, "unpaid_count": 3}


@pytest.mark.asyncio
async def test_invoice_summary_global_on_paid_filter():
    sb = _sb(); A = _invoice_nodes(sb)
    A.eq.return_value.order.return_value.execute.return_value.data = PAID_ROWS
    A.execute.return_value.data = ALL_STATUS_ROWS

    result = await _run_invoice_list("paid", sb)
    d = result["data"]
    assert d["total"] == 1 and d["invoices"][0]["status"] == "paid"
    assert d["summary"] == {"paid_count": 1, "unpaid_count": 3}


@pytest.mark.asyncio
async def test_invoice_summary_matches_full_list_when_unfiltered():
    sb = _sb(); A = _invoice_nodes(sb)
    A.order.return_value.execute.return_value.data = PAID_ROWS + UNPAID_ROWS
    A.execute.return_value.data = ALL_STATUS_ROWS

    result = await _run_invoice_list(None, sb)
    d = result["data"]
    assert d["total"] == 4
    assert d["summary"] == {"paid_count": 1, "unpaid_count": 3}


@pytest.mark.asyncio
async def test_invoice_summary_degrades_to_filtered_counts_on_query_error():
    sb = _sb(); A = _invoice_nodes(sb)
    A.eq.return_value.order.return_value.execute.return_value.data = UNPAID_ROWS
    A.execute.side_effect = Exception("summary db hiccup")

    result = await _run_invoice_list("unpaid", sb)
    d = result["data"]
    assert d["total"] == 3
    assert d["summary"] == {"paid_count": 0, "unpaid_count": 3}


@pytest.mark.asyncio
async def test_invoice_payment_method_passthrough_untouched():
    sb = _sb(); A = _invoice_nodes(sb)
    A.order.return_value.execute.return_value.data = []
    A.execute.return_value.data = []

    result = await _run_invoice_list(None, sb)
    assert result["data"]["payment_method"] == {"saved": True, "pm": "sentinel"}


# ─── OVERVIEW: B-1 closed-deals | B-2 lead info | B-5 branch filter ───────────

from datetime import datetime as _dt, timedelta as _td

def _overview_tables():
    """Independent MagicMock per table name (auto-created on first access).

    Aliases table.return_value.select onto table.select so tests can configure
    chains from either path; the endpoint uses table.select.<chain>.
    """
    class _TableDict(dict):
        def __missing__(self, name):
            t = MagicMock()
            canonical = t.select.return_value
            t.return_value.select = t.select
            t.return_value.select.return_value = canonical
            self[name] = t
            return t
    sb = MagicMock()
    tables = _TableDict()
    sb.table.side_effect = lambda name: tables[name]
    return sb, tables

OWNER_ROW = {"id": "a-owner", "name": "Owner One", "role": "owner",
             "agency_id": "ag-1", "email": OWNER["email"]}
NULL_UUID = "00000000-0000-0000-0000-000000000000"
BRANCH_IDS = ["a1", "a2"]
AGENT_MAP_ROWS = [{"id": "a1", "name": "Aisha Rahman"},
                  {"id": "a2", "name": "Omar Khalid"}]


def _configure_agents(tables, branch_lookup_rows):
    A = tables["agents"]
    # role lookup: legacy .single() path AND current .maybe_single() path
    A.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = OWNER_ROW
    A.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = OWNER_ROW
    A.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": i} for i in branch_lookup_rows
    ]
    A.return_value.select.return_value.eq.return_value.execute.return_value.data = AGENT_MAP_ROWS


@pytest.mark.asyncio
async def test_b5_branch_filter_applies_to_all_three_tables():
    from routers.dashboard import get_dashboard_overview
    sb, T = _overview_tables()
    _configure_agents(T, BRANCH_IDS)
    leads_rows = [{"id": "l1", "status": "new", "source": "bayut", "is_ai_handling": True}]
    L_post = T["leads"].return_value.select.return_value.eq.return_value.in_.return_value
    L_post.ilike.return_value.gte.return_value.lte.return_value.execute.return_value.data = leads_rows
    for tbl in ("viewings", "contracts"):
        P = T[tbl].return_value.select.return_value.eq.return_value.in_.return_value
        P.gte.return_value.lte.return_value.execute.return_value.data = []
    K = T["calls"].return_value.select.return_value.eq.return_value
    K.gte.return_value.lte.return_value.execute.return_value.data = []

    with patch("routers.dashboard.get_supabase", return_value=sb):
        result = await get_dashboard_overview(
            branch_id="Dubai Marina", agent_id=None, timeframe=None,
            start_date="2026-08-01", end_date="2026-08-31", platform="bayut",
            current_user=OWNER,
        )

    assert result["success"] is True
    A_sel = T["agents"].return_value.select.return_value.eq
    A_sel.assert_any_call("agency_id", "ag-1")
    A_sel.return_value.eq.assert_any_call("branch", "Dubai Marina")
    L_in = T["leads"].return_value.select.return_value.eq.return_value.in_
    # called for current AND previous period (both branch-scoped)
    assert L_in.call_count == 2
    assert L_in.call_args_list[0].args == ("assigned_agent_id", BRANCH_IDS)
    assert L_in.call_args_list[1].args == ("assigned_agent_id", BRANCH_IDS)
    L_ilike = (T["leads"].return_value.select.return_value.eq.return_value
               .in_.return_value.ilike)
    # platform filter propagates to current AND previous period
    assert L_ilike.call_count == 2
    assert L_ilike.call_args_list[0].args == ("source", "%bayut%")
    assert L_ilike.call_args_list[1].args == ("source", "%bayut%")
    assert result["data"]["funnel"][0]["count"] == 1


@pytest.mark.asyncio
async def test_b5_branch_plus_matching_agent_uses_agent_eq():
    from routers.dashboard import get_dashboard_overview
    sb, T = _overview_tables()
    _configure_agents(T, BRANCH_IDS)
    for tbl in ("leads", "viewings", "contracts"):
        E = T[tbl].return_value.select.return_value.eq
        E.return_value.eq.return_value.execute.return_value.data = []
        E.return_value.in_ = MagicMock()

    with patch("routers.dashboard.get_supabase", return_value=_sb()), \
         patch("routers.dashboard.get_supabase", return_value=sb):
        result = await get_dashboard_overview(
            branch_id="Dubai Marina", agent_id="a1", timeframe=None,
            start_date=None, end_date=None, platform=None,
            current_user=OWNER,
        )

    assert result["success"] is True
    L_eq = T["leads"].return_value.select.return_value.eq
    L_eq.return_value.eq.assert_any_call("assigned_agent_id", "a1")
    for tbl in ("leads", "viewings", "contracts"):
        T[tbl].return_value.select.return_value.eq.return_value.in_.assert_not_called()


@pytest.mark.asyncio
async def test_b5_branch_plus_foreign_agent_yields_empty_via_sentinel():
    from routers.dashboard import get_dashboard_overview
    sb, T = _overview_tables()
    _configure_agents(T, BRANCH_IDS)
    for tbl in ("leads", "viewings", "contracts"):
        E = T[tbl].return_value.select.return_value.eq.return_value.in_
        E.return_value.execute.return_value.data = []

    with patch("routers.dashboard.get_supabase", return_value=sb):
        result = await get_dashboard_overview(
            branch_id="Dubai Marina", agent_id="agent-NOT-in-branch", timeframe=None,
            start_date=None, end_date=None, platform=None,
            current_user=OWNER,
        )

    d = result["data"]
    assert d["funnel"][0]["count"] == 0
    assert d["live_leads"] == []
    for tbl in ("leads", "viewings", "contracts"):
        col = "assigned_agent_id" if tbl == "leads" else "agent_id"
        T[tbl].return_value.select.return_value.eq.return_value.in_.assert_called_once_with(
            col, [NULL_UUID]
        )


@pytest.mark.asyncio
async def test_b5_nonexistent_branch_returns_empty_metrics():
    from routers.dashboard import get_dashboard_overview
    sb, T = _overview_tables()
    _configure_agents(T, [])
    for tbl in ("leads", "viewings", "contracts"):
        E = T[tbl].return_value.select.return_value.eq.return_value.in_
        E.return_value.execute.return_value.data = []
    K = T["calls"].return_value.select.return_value.eq.return_value
    K.gte.return_value.lte.return_value.execute.return_value.data = []

    with patch("routers.dashboard.get_supabase", return_value=sb):
        result = await get_dashboard_overview(
            branch_id="Ghost Branch", agent_id=None, timeframe=None,
            start_date=None, end_date=None, platform=None,
            current_user=OWNER,
        )

    d = result["data"]
    assert d["funnel"][0]["count"] == 0
    assert d["closed_deals"]["count"] == 0
    assert d["live_leads"] == []


@pytest.mark.asyncio
async def test_b5_cross_agency_branch_isolated_by_scoped_lookup():
    from routers.dashboard import get_dashboard_overview
    sb, T = _overview_tables()
    _configure_agents(T, [])
    for tbl in ("leads", "viewings", "contracts"):
        E = T[tbl].return_value.select.return_value.eq.return_value.in_
        E.return_value.execute.return_value.data = []

    with patch("routers.dashboard.get_supabase", return_value=sb):
        result = await get_dashboard_overview(
            branch_id="other-agencys-branch", agent_id=None, timeframe=None,
            start_date=None, end_date=None, platform=None,
            current_user=OWNER,
        )

    A_sel = T["agents"].return_value.select.return_value.eq
    A_sel.assert_any_call("agency_id", "ag-1")
    assert result["data"]["funnel"][0]["count"] == 0


@pytest.mark.asyncio
async def test_b5_branch_with_timeframe_computes_previous_period():
    from routers.dashboard import get_dashboard_overview
    sb, T = _overview_tables()
    _configure_agents(T, BRANCH_IDS)
    leads_rows = [{"id": "l1", "status": "closed", "source": "bayut", "is_ai_handling": False}]
    L_post = T["leads"].return_value.select.return_value.eq.return_value.in_.return_value
    L_post.ilike.return_value.gte.return_value.lte.return_value.execute.return_value.data = leads_rows
    L_post.gte.return_value.lte.return_value.execute.return_value.data = []
    for tbl in ("viewings", "contracts"):
        P = T[tbl].return_value.select.return_value.eq.return_value.in_.return_value
        P.gte.return_value.lte.return_value.execute.return_value.data = []
    K = T["calls"].return_value.select.return_value.eq.return_value
    K.gte.return_value.lte.return_value.execute.return_value.data = []

    with patch("routers.dashboard.get_supabase", return_value=sb):
        result = await get_dashboard_overview(
            branch_id="Dubai Marina", agent_id=None, timeframe="this_month",
            start_date=None, end_date=None, platform=None,
            current_user=OWNER,
        )

    m = result["data"]["metrics"]["lead_to_viewing"]
    assert m["trend"] is not None


@pytest.mark.asyncio
async def test_b1_closed_contract_included_in_closed_deals():
    from routers.dashboard import get_dashboard_overview
    sb, T = _overview_tables()
    _configure_agents(T, [])
    T["leads"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    T["viewings"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    T["contracts"].return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": "c1", "status": "signed", "rent_amount": 100000, "agent_id": "a1"},
        {"id": "c2", "status": "closed", "rent_amount": 200000, "agent_id": "a2"},
        {"id": "c3", "status": "draft", "rent_amount": 50000, "agent_id": "a2"},
    ]
    T["calls"].return_value.select.return_value.eq.return_value.execute.return_value.data = []

    with patch("routers.dashboard.get_supabase", return_value=sb):
        result = await get_dashboard_overview(
            branch_id=None, agent_id=None, timeframe=None,
            start_date=None, end_date=None, platform=None,
            current_user=OWNER)

    cd = result["data"]["closed_deals"]
    assert cd["count"] == 2
    assert cd["total_rent_value"] == 300000
    assert cd["agency_fees_earned"] == 15000
    by_agent = {d["firstName"]: d for d in cd["deals_by_agent"]}
    assert by_agent["Aisha"]["revenue"] == 5000
    assert by_agent["Omar"]["revenue"] == 10000


@pytest.mark.asyncio
async def test_b2_todays_viewings_include_lead_name_and_source():
    from routers.dashboard import get_dashboard_overview
    sb, T = _overview_tables()
    _configure_agents(T, [])
    tomorrow = (datetime.utcnow() + timedelta(days=1)).isoformat()
    T["leads"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    V_sel = T["viewings"].return_value.select
    V_sel.return_value.eq.return_value.execute.return_value.data = [
        {"id": "v1", "lead_id": "l1", "agent_id": "a1",
         "property_address": "Apartment 101, Downtown",
         "viewing_datetime": datetime.utcnow().isoformat(), "status": "scheduled",
         "leads": {"name": "John Doe", "source": "property_finder"}},
        {"id": "v2", "lead_id": "l2", "agent_id": "a2", "property_address": "Villa 4",
         "viewing_datetime": tomorrow, "status": "scheduled",
         "leads": {"name": "Future Person", "source": "bayut"}},
    ]
    T["contracts"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    T["calls"].return_value.select.return_value.eq.return_value.execute.return_value.data = []

    with patch("routers.dashboard.get_supabase", return_value=sb):
        result = await get_dashboard_overview(
            branch_id=None, agent_id=None, timeframe=None,
            start_date=None, end_date=None, platform=None,
            current_user=OWNER)

    tv = result["data"]["todays_viewings"]
    assert len(tv) == 1
    row = tv[0]
    assert row["lead_name"] == "John Doe"
    assert row["lead_source"] == "property_finder"
    assert set(row["leads"].keys()) <= {"name", "source"}
    assert row["agent_name"] == "Aisha Rahman"

    sel_arg = V_sel.call_args[0][0]
    assert "leads(name, source)" in sel_arg
    V_eq = T["viewings"].return_value.select.return_value.eq
    V_eq.assert_any_call("agency_id", "ag-1")

# ─── LEAD/CONTRACT/CHEQUE/DOCUMENT CREATE: UUID serialization boundary ────────

@pytest.mark.asyncio
async def test_lead_create_serializes_uuid_and_stamps_agency():
    """assigned_agent_id must reach PostgREST as a JSON string, agency stamped."""
    from routers.leads import create_lead, LeadCreate
    sb = _sb()
    # same-agency agent validation lookup
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "252c459c-c347-4b1b-ab6d-1d1fe0ed4364"}
    ]
    inserted = {"id": "lead-1", "name": "L1", "assigned_agent_id": "252c459c-c347-4b1b-ab6d-1d1fe0ed4364"}
    sb.table.return_value.insert.return_value.execute.return_value.data = [inserted]

    body = LeadCreate(
        name="Postman E2E Lead L1", phone="+971500001001",
        source="property_finder", assigned_agent_id="252c459c-c347-4b1b-ab6d-1d1fe0ed4364",
    )
    with patch("routers.leads.get_supabase", return_value=sb), \
         patch("routers.leads.require_agency_id", return_value="ag-1"):
        result = await create_lead(body, OWNER)

    assert result["status"] == 201
    payload = sb.table.return_value.insert.call_args[0][0]
    assert isinstance(payload["assigned_agent_id"], str)      # JSON-safe, not UUID object
    assert payload["agency_id"] == "ag-1"                      # server-stamped
    assert payload["status"] == "new"


@pytest.mark.asyncio
async def test_lead_create_rejects_agent_from_other_agency():
    from routers.leads import create_lead, LeadCreate
    sb = _sb()
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
    sb.table.return_value.insert = MagicMock()

    body = LeadCreate(name="Bad Assign", phone="+971500002002",
                      assigned_agent_id="99999999-9999-9999-9999-999999999999")
    with patch("routers.leads.get_supabase", return_value=sb), \
         patch("routers.leads.require_agency_id", return_value="ag-1"):
        with pytest.raises(HTTPException) as e:
            await create_lead(body, OWNER)

    assert e.value.status_code == 400
    assert "your agency" in e.value.detail
    sb.table.return_value.insert.assert_not_called()          # nothing persisted


@pytest.mark.asyncio
async def test_lead_create_without_assignment_skips_agent_check():
    from routers.leads import create_lead, LeadCreate
    sb = _sb()
    sb.table.return_value.insert.return_value.execute.return_value.data = [{"id": "lead-2"}]

    body = LeadCreate(name="Unassigned Lead", phone="+971500003003")
    with patch("routers.leads.get_supabase", return_value=sb), \
         patch("routers.leads.require_agency_id", return_value="ag-1"):
        result = await create_lead(body, OWNER)

    assert result["status"] == 201
    sel_eq = sb.table.return_value.select.return_value.eq
    sel_eq.assert_not_called()                                # validation skipped


@pytest.mark.asyncio
async def test_contract_create_serializes_lead_uuid():
    from routers.contracts import create_contract, ContractCreate
    sb = _sb()
    sb.table.return_value.insert.return_value.execute.return_value.data = [{
        "id": "c-1", "lead_id": "00000000-0000-0000-0000-000000000001", "status": "draft",
    }]
    body = ContractCreate(lead_id="00000000-0000-0000-0000-000000000001", rent_amount=120000)
    with patch("database.supabase_client.get_supabase", return_value=sb), \
         patch("routers.contracts.require_agency_id", return_value="ag-1"):
        result = create_contract(body, OWNER)

    assert result["data"]["status"] == "draft"
    payload = sb.table.return_value.insert.call_args[0][0]
    assert isinstance(payload["lead_id"], str)
    assert payload["agency_id"] == "ag-1"


@pytest.mark.asyncio
async def test_cheque_create_serializes_contract_uuid():
    from routers.cheques import create_cheque, ChequeCreate
    sb = _sb()
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = {"id": "c-1"}

    async def fake_log(contract_id, data):
        return {"id": "ch-1", "contract_id": contract_id}

    body = ChequeCreate(contract_id="00000000-0000-0000-0000-000000000009",
                        cheque_number="CH-01", bank_name="ENBD",
                        amount=10000, due_date="2026-10-01")
    with patch("routers.cheques.supabase_client.get_supabase", return_value=sb), \
         patch("routers.cheques.require_agency_id", return_value="ag-1"), \
         patch("routers.cheques.log_cheque", new_callable=AsyncMock, side_effect=fake_log):
        result = await create_cheque(body, OWNER)

    assert result["data"]["contract_id"] == "00000000-0000-0000-0000-000000000009"


@pytest.mark.asyncio
async def test_document_create_serializes_lead_uuid():
    from routers.documents import create_document, DocumentCreate
    sb = _sb()
    sb.table.return_value.insert.return_value.execute.return_value.data = [{
        "id": "d-1", "lead_id": "00000000-0000-0000-0000-000000000001", "status": "pending",
    }]

    async def fake_ocr(doc_id):
        return {"full_name": "John"}

    body = DocumentCreate(lead_id="00000000-0000-0000-0000-000000000001",
                          document_type="passport",
                          file_url="https://storage.example/doc.jpg")
    with patch("routers.documents.supabase_client.get_supabase", return_value=sb), \
         patch("routers.documents.require_agency_id", return_value="ag-1"), \
         patch("routers.documents.verify_lead_access", new_callable=AsyncMock), \
         patch("routers.documents.extract_document_data", new_callable=AsyncMock, side_effect=fake_ocr):
        result = await create_document(body, OWNER)

    payload = sb.table.return_value.insert.call_args[0][0]
    assert isinstance(payload["lead_id"], str)
    assert payload["agency_id"] == "ag-1"
    assert result["data"]["extracted_data"] == {"full_name": "John"}


# ─── LEADS SEARCH: count/results parity + expanded searchable fields ─────────

L1_ROW = {
    "id": "23595b87-6269-44fa-ab21-68a8df927cf1",
    "name": "Postman E2E Lead L1", "phone": "+971500001001",
    "email": "e2e.lead.l1@e2etest.com", "external_lead_id": "POSTMAN-E2E-001",
    "property_ref": "POSTMAN-PROP-001", "property_address": "Marina Tower",
    "location_pref": "Dubai Marina", "status": "new", "source": "property_finder",
    "bedrooms": 2, "budget_max": 120000, "purpose": "rent",
    "assigned_agent_id": "252c459c-c347-4b1b-ab6d-1d1fe0ed4364",
    "agents": {"name": "Postman Agent A"},
}


def _leads_search_mock(rows, total, agent_user=False, with_search=True):
    """Chain map AFTER the refactor (scope first, then filters, then order):
      owner, search : S.eq(scope).or_(search).order.range.execute
                      S.eq(scope).or_(search).execute            <- count
      owner, no srch: S.eq(scope).order.range.execute
                      S.eq(scope).execute                        <- count
      agent variants add one more .eq(scope) link before or_/execute.
    """
    sb = _sb()
    S = sb.table.return_value.select.return_value
    s1 = S.eq                                     # agency eq (shared)
    if agent_user:
        s2 = s1.return_value.eq                   # assigned_agent eq
        base = s2.return_value
    else:
        base = s1.return_value
    if with_search:
        orr = base.or_
        or_node = orr
        count_exec = orr.return_value.execute
        main_exec = orr.return_value.order.return_value.range.return_value.execute
    else:
        or_node = None
        count_exec = base.execute
        main_exec = base.order.return_value.range.return_value.execute
    main_exec.return_value.data = rows
    main_exec.return_value.count = None
    count_resp = MagicMock(); count_resp.count = total
    count_exec.return_value = count_resp
    return sb, or_node, s1, (s2 if agent_user else None)


async def _run_leads(search=None, limit=50, offset=0, user=None, sb=None):
    from routers.leads import list_leads
    with patch("routers.leads.get_supabase", return_value=sb):
        return await list_leads(status=None, source=None, agent_id=None,
                                is_ai_handling=None, search=search,
                                limit=limit, offset=offset, _=user or OWNER)


def _lead_ids(result):
    return [l["id"] for l in result["data"]["leads"]]


@pytest.mark.asyncio
async def test_search_A_no_filter_returns_lead():
    sb, _, scope1, _ = _leads_search_mock([L1_ROW], 1, with_search=False)
    result = await _run_leads(search=None, sb=sb)
    assert _lead_ids(result) == [L1_ROW["id"]]
    assert result["data"]["total"] == 1
    scope1.assert_called_with("agency_id", "ag-1")


@pytest.mark.asyncio
async def test_search_B_exact_name():
    sb, _, _, _ = _leads_search_mock([L1_ROW], 1, with_search=True)
    result = await _run_leads(search="Postman E2E Lead L1", sb=sb)
    assert result["data"]["total"] == 1
    assert _lead_ids(result) == [L1_ROW["id"]]


@pytest.mark.asyncio
async def test_search_C_and_G_partial_name_case_insensitive():
    for term in ("Postman E2E", "postman e2e"):
        sb, or_node, _, _ = _leads_search_mock([L1_ROW], 1, with_search=True)
        result = await _run_leads(search=term, sb=sb)
        assert _lead_ids(result) == [L1_ROW["id"]]
        assert result["data"]["total"] == 1
        used = or_node.call_args[0][0]
        assert f"name.ilike.%{term}%" in used


@pytest.mark.asyncio
async def test_search_D_external_id_full():
    sb, _, _, _ = _leads_search_mock([L1_ROW], 1, with_search=True)
    result = await _run_leads(search="POSTMAN-E2E-001", sb=sb)
    assert _lead_ids(result) == [L1_ROW["id"]]


@pytest.mark.asyncio
async def test_search_E_partial_external_id_REGRESSION():
    """THE bug: search=POSTMAN-E2E must return the lead AND a matching total."""
    sb, or_node, _, _ = _leads_search_mock([L1_ROW], 1, with_search=True)
    result = await _run_leads(search="POSTMAN-E2E", sb=sb)
    assert result["data"]["total"] == 1
    assert _lead_ids(result) == [L1_ROW["id"]]
    used = or_node.call_args[0][0]
    assert "external_lead_id.ilike.%POSTMAN-E2E%" in used
    assert used.count(".ilike.") == 7                # all searchable fields covered


@pytest.mark.asyncio
async def test_search_F_property_ref():
    sb, or_node, _, _ = _leads_search_mock([L1_ROW], 1, with_search=True)
    result = await _run_leads(search="POSTMAN-PROP-001", sb=sb)
    assert _lead_ids(result) == [L1_ROW["id"]]
    assert "property_ref.ilike.%POSTMAN-PROP-001%" in or_node.call_args[0][0]


@pytest.mark.asyncio
async def test_search_H_pagination_limit_offset():
    row2 = {**L1_ROW, "id": "aaaa0000-0000-0000-0000-000000000002", "name": "Second Lead"}
    sb, _, _, _ = _leads_search_mock([L1_ROW], 2, with_search=False)
    result = await _run_leads(search=None, limit=1, offset=0, sb=sb)
    assert len(result["data"]["leads"]) == 1
    assert result["data"]["total"] == 2
    assert result["data"]["limit"] == 1 and result["data"]["offset"] == 0


@pytest.mark.asyncio
async def test_search_I_J_tenant_and_agent_scoping_on_both_queries():
    sb, or_node, scope1, scope2 = _leads_search_mock([L1_ROW], 1,
                                                     agent_user=True, with_search=True)
    result = await _run_leads(search="POSTMAN-E2E", user=AGENT, sb=sb)

    scope1.assert_called_with("agency_id", "ag-1")                       # main query
    scope2.assert_called_with("assigned_agent_id", AGENT["agent_id"])    # main query
    S = sb.table.return_value.select.return_value
    S.eq.assert_any_call("agency_id", "ag-1")                            # count query
    S.eq.return_value.eq.assert_any_call(
        "assigned_agent_id", AGENT["agent_id"])                          # count query
    assert _lead_ids(result) == [L1_ROW["id"]]


@pytest.mark.asyncio
async def test_search_L_no_match_zero_everywhere():
    sb, _, _, _ = _leads_search_mock([], 0, with_search=True)
    result = await _run_leads(search="THIS-DOES-NOT-EXIST", sb=sb)
    assert result["data"]["total"] == 0
    assert result["data"]["leads"] == []

@pytest.mark.asyncio
async def test_document_ocr_failure_returns_created_not_500():
    """OCR is best-effort: extraction failure must not fail document creation."""
    from routers.documents import create_document, DocumentCreate
    sb = _tables_sb() if False else _sb()
    sb.table.return_value.insert.return_value.execute.return_value.data = [{
        "id": "d-9", "lead_id": "00000000-0000-0000-0000-000000000001",
        "status": "pending",
    }]
    upd = sb.table.return_value.update.return_value.eq.return_value.execute

    async def boom(doc_id):
        raise Exception("OpenAI invalid_image_url")

    body = DocumentCreate(lead_id="00000000-0000-0000-0000-000000000001",
                          document_type="passport",
                          file_url="https://example.com/unreadable.jpg")
    with patch("routers.documents.supabase_client.get_supabase", return_value=sb), \
         patch("routers.documents.require_agency_id", return_value="ag-1"), \
         patch("routers.documents.verify_lead_access", new_callable=AsyncMock), \
         patch("routers.documents.extract_document_data", new_callable=AsyncMock, side_effect=boom):
        result = await create_document(body, OWNER)

    assert result["data"]["status"] == "failed"
    assert result["data"]["extracted_data"] is None
    upd.return_value.data  # update issued (status=failed persisted)
    sb.table.return_value.update.assert_called_once_with(
        {"status": "failed", "error_message": "AI extraction failed"}
    )

# ─── CONTRACT SIGN: timezone-safe expiry check (M-15 fix) ─────────────────────

from datetime import datetime as _sig_dt, timedelta as _sig_td, timezone as _sig_tz

def _sign_mock(expires_iso):
    class _TD(dict):
        def __missing__(self, name):
            t = MagicMock()
            canonical = t.select.return_value
            t.return_value.select = t.select
            t.return_value.select.return_value = canonical
            self[name] = t
            return t
    sb = MagicMock()
    T = _TD()
    sb.table.side_effect = lambda n: T[n]
    C = T["contracts"]
    C.return_value.select.return_value.eq.return_value.execute.return_value.data = [{
        "id": "c-1", "status": "generated",
        "landlord_sign_token": "tok-ll", "tenant_sign_token": "tok-tt",
        "sign_token_expires_at": expires_iso,
        "landlord_signed_at": None, "tenant_signed_at": None,
    }]
    C.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": "c-1"}]
    return sb, C


@pytest.mark.asyncio
async def test_sign_with_aware_expiry_does_not_crash_and_records_signature():
    from services.contract_service import verify_and_record_signature
    future = (_sig_dt.utcnow().replace(tzinfo=_sig_tz.utc) + _sig_td(days=1)).isoformat()
    sb, C = _sign_mock(future)
    with patch("database.supabase_client.get_supabase", return_value=sb):
        result = await verify_and_record_signature("c-1", "tok-ll", "landlord")
    assert result["signed_at"] is not None
    C.update.assert_called_once_with({"landlord_signed_at": result["signed_at"]})


@pytest.mark.asyncio
async def test_sign_expired_aware_token_rejected():
    from services.contract_service import verify_and_record_signature
    past = (_sig_dt.utcnow().replace(tzinfo=_sig_tz.utc) - _sig_td(minutes=5)).isoformat()
    sb, C = _sign_mock(past)
    with patch("database.supabase_client.get_supabase", return_value=sb):
        with pytest.raises(ValueError) as e:
            await verify_and_record_signature("c-1", "tok-ll", "landlord")
    assert "expired" in str(e.value)

@pytest.mark.asyncio
async def test_get_contract_foreign_returns_404_not_500():
    """Foreign contract must be 404 (maybe_single), never 500/PGRST116 leak."""
    from routers.contracts import get_contract
    sb = _sb()
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = None
    with patch("database.supabase_client.get_supabase", return_value=sb), \
         patch("routers.contracts.require_agency_id", return_value="ag-1"), \
         patch("routers.contracts.apply_agency_scope", side_effect=lambda q, u: q.eq("agency_id", "ag-1")):
        with pytest.raises(HTTPException) as e:
            get_contract("foreign-contract-id", OWNER)
    assert e.value.status_code == 404
    assert e.value.detail == "Contract not found"

@pytest.mark.asyncio
async def test_overview_no_agent_profile_returns_400_not_500():
    """Auth user without an agents row must get 400, never 500 (PGRST116)."""
    from routers.dashboard import get_dashboard_overview
    sb = _sb()
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = None
    ghost = {"sub": "ghost", "email": "ghost@x.com", "agency_id": "ag-x",
             "role": "owner", "agent_id": None}
    with patch("routers.dashboard.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as e:
            await get_dashboard_overview(current_user=ghost)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_overview_funnel_consistent_with_leads_stats():
    """Cross-API consistency: funnel Leads count == /leads/stats.total for same data."""
    from routers.dashboard import get_dashboard_overview
    from routers.leads import get_lead_stats
    sb, T = _overview_tables()
    lead_rows = [
        {"id": "l1", "status": "closed", "source": "bayut", "is_ai_handling": False,
         "assigned_agent_id": "a-owner", "created_at": "2026-08-20T10:00:00Z",
         "updated_at": "2026-08-20T10:00:00Z"},
        {"id": "l2", "status": "qualifying", "source": "bayut", "is_ai_handling": True,
         "assigned_agent_id": "a-owner", "created_at": "2026-08-21T10:00:00Z",
         "updated_at": "2026-08-21T10:00:00Z"},
    ]
    _configure_agents(T, [])
    T["leads"].return_value.select.return_value.eq.return_value.execute.return_value.data = lead_rows
    T["viewings"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    T["contracts"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    T["calls"].return_value.select.return_value.eq.return_value.execute.return_value.data = []
    owner = {"sub": "u", "email": "o@a.com", "agency_id": "ag-1",
             "role": "owner", "agent_id": "a-owner"}

    with patch("routers.dashboard.get_supabase", return_value=sb), \
         patch("routers.leads.get_supabase", return_value=sb):
        ov = await get_dashboard_overview(branch_id=None, agent_id=None, timeframe=None,
                                          start_date=None, end_date=None, platform=None,
                                          current_user=owner)
        st = await get_lead_stats(current_user=owner)

    funnel_leads = {x["name"]: x["count"] for x in ov["data"]["funnel"]}["Leads"]
    assert funnel_leads == st["data"]["total"] == 2
    assert st["data"]["closed"] == 1
