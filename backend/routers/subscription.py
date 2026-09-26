"""
Subscription & Billing Router — Multi-Tenant SaaS Billing System

Endpoints:
GET    /subscription/plans            — list all available plans and recurring add-ons
GET    /subscription/my-plan          — current agency active plan & metered call usage
POST   /subscription/checkout         — create Stripe checkout session for plan subscription
POST   /subscription/upgrade          — upgrade/downgrade subscription with proration
POST   /subscription/add-on           — purchase recurring Agent Calls add-on pack (p1000-p10000)
DELETE /subscription/add-on/{code}    — cancel an active add-on pack
GET    /subscription/billing-portal   — generate Stripe Customer Portal session URL
GET    /subscription/invoices         — list invoices with filter (?status=paid|unpaid)
GET    /subscription/invoices/{id}    — single invoice details
GET    /subscription/contract         — subscription contract overview
GET    /subscription/payment-method   — get primary payment card or portal link
POST   /subscription/payment-method   — update card / redirect to Stripe Customer Portal
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, BackgroundTasks
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token
from config import settings
from utils.response import api_success
from utils.tenant import require_agency_id, apply_agency_scope
from services.billing_service import (
    PLANS_METADATA,
    ADDONS_METADATA,
    VAT_RATE,
    create_checkout_session,
    upgrade_subscription_plan,
    purchase_subscription_addon,
    remove_subscription_addon,
    create_billing_portal_session,
    record_call_usage,
    fetch_and_sync_live_invoices,
    get_saved_payment_method_info,
    update_saved_payment_method_info,
    cancel_subscription,
)
from services.contract_service import generate_subscription_agreement_pdf
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/subscription", tags=["Subscription & Billing"])



# ─── Request Models ─────────────────────────────────────────────────────────────

class UpgradeRequest(BaseModel):
    plan_name: str = Field(..., description="Plan tier: 'basic', 'grow', or 'pro'")


class AddOnRequest(BaseModel):
    addon_code: str = Field(..., description="Add-on code: 'p1000', 'p2000', 'p5000', 'p10000'")


class CheckoutRequest(BaseModel):
    plan_tier: str = Field(..., description="'basic', 'grow', or 'pro'")
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class PaymentMethodRequest(BaseModel):
    card_number: Optional[str] = None
    name_on_card: Optional[str] = None
    name: Optional[str] = None
    expiry: Optional[str] = None
    card_expiry: Optional[str] = None
    cvc: Optional[str] = None
    card_last4: Optional[str] = None
    card_brand: Optional[str] = None
    is_primary: bool = True
    payment_method_id: Optional[str] = None  # preferred: Stripe.js pm_… token


class CancelSubscriptionRequest(BaseModel):
    at_period_end: bool = True


# ─── 1. Plans & Pricing ─────────────────────────────────────────────────────────

@router.get("/plans")
async def list_available_plans(_: dict = Depends(verify_token)):
    """
    Returns all standard subscription plans and recurring call add-on packs with pricing in AED.
    """
    plans = []
    for key, val in PLANS_METADATA.items():
        vat_amount = round(val["price_aed"] * VAT_RATE, 2)
        total_with_vat = round(val["price_aed"] * (1 + VAT_RATE), 2)
        plans.append({
            "plan_key": key,
            "display_name": val["display_name"],
            "tagline": val["tagline"],
            "price_aed_excl_vat": val["price_aed"],
            "vat_aed": vat_amount,
            "total_price_aed": total_with_vat,
            "agents_included": val["agents"],
            "calls_included": val["included_calls"],
            "portals": val["portals"],
            "features": val.get("features", []),
        })

    addons = []
    for key, val in ADDONS_METADATA.items():
        addons.append({
            "addon_code": key,
            "calls_monthly": val["calls"],
            "price_aed": val["price_aed"],
            "label": val["label"],
        })

    return api_success(
        data={
            "currency": "AED",
            "vat_rate": "5%",
            "overage_rate_aed_per_call": 2.00,
            "plans": plans,
            "addons": addons,
        },
        message="Available plans and add-on packs retrieved successfully"
    )


# ─── 2. Current Plan & Usage ───────────────────────────────────────────────────

@router.get("/current")
@router.get("/my-plan")
async def get_current_agency_plan(
    request: Request,
    current_user: dict = Depends(verify_token)
):
    """
    Get active plan details, call quota, active add-ons, and current cycle usage.
    """
    agency_id = require_agency_id(current_user)
    role = current_user.get("role")

    if role not in ["owner", "manager", "super_admin"]:
        raise HTTPException(status_code=403, detail="Forbidden: Only owners and managers can view subscription details")

    sb = get_supabase()
    
    agency_data = {}
    try:
        agency_res = sb.table("agencies").select("name, subscription_plan, subscription_status").eq("id", agency_id).maybe_single().execute()
        if agency_res and agency_res.data:
            agency_data = agency_res.data
    except Exception as e:
        logger.debug(f"Agencies query notice: {e}")

    sub_data = {}
    try:
        sub_res = sb.table("subscriptions").select("*").eq("agency_id", agency_id).maybe_single().execute()
        if sub_res and sub_res.data:
            sub_data = sub_res.data
    except Exception as e:
        logger.debug(f"Subscriptions table notice: {e}")

    plan_tier = (sub_data.get("plan_tier") or agency_data.get("subscription_plan") or "grow").lower()
    plan_info = PLANS_METADATA.get(plan_tier, PLANS_METADATA["grow"])

    status = sub_data.get("status") or agency_data.get("subscription_status") or "active"
    included_calls = sub_data.get("included_calls", plan_info["included_calls"])
    addon_calls = sub_data.get("addon_calls", 0)
    used_calls = sub_data.get("used_calls", 0)
    total_quota = included_calls + addon_calls
    active_addons = sub_data.get("active_addons") or []

    # Calculate active add-on cost
    addon_cost_total = sum(ADDONS_METADATA.get(code, {}).get("price_aed", 0) for code in active_addons)
    base_price = plan_info["price_aed"]
    monthly_subtotal = base_price + addon_cost_total
    vat_amount = round(monthly_subtotal * VAT_RATE, 2)
    total_monthly_aed = round(monthly_subtotal + vat_amount, 2)

    cycle_end = sub_data.get("billing_cycle_end")
    cycle_start = sub_data.get("billing_cycle_start")
    contract_doc_url = f"{str(request.base_url).rstrip('/')}/subscription/contract/pdf"

    return api_success(
        data={
            "plan_tier": plan_tier,
            "display_name": plan_info["display_name"],
            "status": status,
            "base_price_aed": base_price,
            "addon_cost_aed": addon_cost_total,
            "vat_amount_aed": vat_amount,
            "total_monthly_aed": total_monthly_aed,
            "agents_included": plan_info["agents"],
            "billing_cycle_end": cycle_end,
            "active_addons": active_addons,
            "usage": {
                "included_calls": included_calls,
                "addon_calls": addon_calls,
                "total_quota": total_quota,
                "used_calls": used_calls,
                "remaining_calls": max(0, total_quota - used_calls),
                "overage_calls": max(0, used_calls - total_quota),
            },
            "contract": {
                "contract_number": sub_data.get("stripe_sub_id"),
                "product": f"{plan_info['display_name']} Plan + Agent Calls",
                "status": status.capitalize(),
                "duration_start": cycle_start,
                "duration_end": cycle_end,
                "payment_mode": "Credit/Debit Card" if getattr(settings, "STRIPE_SECRET_KEY", None) else None,
                "signed_by": None,
                "gross_amount_aed": base_price * 12,
                "discount_percent": 0,
                "total_amount_aed": total_monthly_aed * 12,
                "document_url": contract_doc_url,
            }
        },
        message="Current subscription details retrieved successfully"
    )


# ─── 3. Checkout & Upgrades ────────────────────────────────────────────────────

@router.post("/checkout")
async def initiate_checkout(
    body: CheckoutRequest,
    current_user: dict = Depends(verify_token)
):
    """
    Create a Stripe Checkout Session for new subscription purchases.
    """
    agency_id = require_agency_id(current_user)
    if current_user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only agency owners can initiate subscription checkout")

    sb = get_supabase()
    agency_name = "AndiOS Agency"
    try:
        agency_res = sb.table("agencies").select("name").eq("id", agency_id).maybe_single().execute()
        if agency_res and agency_res.data:
            agency_name = agency_res.data.get("name", "AndiOS Agency")
    except Exception as e:
        logger.debug(f"Agencies query notice: {e}")

    email = current_user.get("email", "owner@andios.ai")
    success_url = body.success_url or "http://localhost:3000/owner-dashboard/plan-billing?status=success"
    cancel_url = body.cancel_url or "http://localhost:3000/owner-dashboard/plan-billing?status=cancelled"

    try:
        res = await create_checkout_session(
            agency_id=agency_id,
            plan_tier=body.plan_tier,
            email=email,
            agency_name=agency_name,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return api_success(data=res, message="Checkout session created successfully")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Checkout creation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create checkout: {str(e)}")


@router.post("/upgrade")
async def upgrade_plan(
    body: UpgradeRequest,
    current_user: dict = Depends(verify_token)
):
    """
    Upgrade or switch plan tier (Basic, Grow, Pro) with automatic Stripe proration.
    """
    agency_id = require_agency_id(current_user)
    if current_user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only agency owners can upgrade the subscription plan")

    try:
        result = await upgrade_subscription_plan(agency_id, body.plan_name)
        return api_success(data=result, message=f"Subscription successfully upgraded to {body.plan_name.title()} Plan")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Upgrade failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to upgrade plan")


# ─── 4. Add-on Calls Management ────────────────────────────────────────────────

@router.post("/add-on")
async def add_call_pack(
    body: AddOnRequest,
    current_user: dict = Depends(verify_token)
):
    """
    Purchase a recurring Agent Calls add-on pack (p1000, p2000, p5000, p10000).
    """
    agency_id = require_agency_id(current_user)
    if current_user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only agency owners can purchase add-ons")

    try:
        result = await purchase_subscription_addon(agency_id, body.addon_code)
        return api_success(data=result, message=f"Add-on pack {body.addon_code.upper()} added to your subscription")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Addon purchase failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to purchase add-on")


@router.delete("/add-on/{addon_code}")
async def cancel_call_pack(
    addon_code: str,
    current_user: dict = Depends(verify_token)
):
    """
    Remove an active call pack add-on from the agency's subscription.
    """
    agency_id = require_agency_id(current_user)
    if current_user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only agency owners can remove add-ons")

    try:
        result = await remove_subscription_addon(agency_id, addon_code)
        return api_success(data=result, message=f"Add-on pack {addon_code.upper()} removed from your subscription")
    except Exception as e:
        logger.error(f"Addon removal failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove add-on")


# ─── 5. Stripe Customer Portal ─────────────────────────────────────────────────

@router.get("/billing-portal")
async def get_customer_portal_url(
    return_url: Optional[str] = Query(None),
    current_user: dict = Depends(verify_token)
):
    """
    Generates a secure hosted Stripe Customer Portal URL for updating cards and downloading tax invoices.
    """
    agency_id = require_agency_id(current_user)
    role = current_user.get("role")
    if role not in ["owner", "manager", "super_admin"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        portal_url = await create_billing_portal_session(agency_id, return_url)
        return api_success(data={"portal_url": portal_url}, message="Stripe customer portal session generated")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to generate portal URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate billing portal session")


# ─── 6. Invoices & Payments History ────────────────────────────────────────────

@router.get("/invoices")
async def list_invoices(
    status: Optional[str] = Query(None, description="Filter: 'paid' or 'unpaid'"),
    refresh: bool = Query(False, description="Force live sync from Stripe API"),
    current_user: dict = Depends(verify_token)
):
    """
    List agency invoices for Payments screen with Paid / Unpaid tab filtering.
    Consistently ultra-fast: Reads directly from Supabase. Synced in real-time via Webhooks.
    """
    agency_id = require_agency_id(current_user)
    role = current_user.get("role")
    if role not in ["owner", "manager", "super_admin"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    sb = get_supabase()
    invoices = []

    if refresh:
        # Explicit refresh requested by user
        invoices = await fetch_and_sync_live_invoices(agency_id, status)

    if not invoices:
        try:
            query = sb.table("invoices").select("*").eq("agency_id", agency_id)
            if status:
                query = query.eq("status", status.lower())
            result = query.order("due_date", desc=True).execute()
            if result and result.data:
                invoices = result.data
        except Exception as e:
            logger.debug(f"Invoices query note: {e}")

    # Fallback to cold-start initial sync if DB table was completely empty
    if not invoices and not refresh:
        invoices = await fetch_and_sync_live_invoices(agency_id, status)
        # No mock fallback — an agency with no invoices sees an honest empty list

    # Summary counts are GLOBAL for the agency (all invoices), independent of
    # the requested ?status= filter on the returned list.
    try:
        status_rows = (
            sb.table("invoices")
            .select("status")
            .eq("agency_id", agency_id)
            .execute()
            .data or []
        )
        global_paid = sum(1 for inv in status_rows if inv.get("status") == "paid")
        global_unpaid = sum(1 for inv in status_rows if inv.get("status") in ["unpaid", "upcoming"])
    except Exception as e:
        logger.warning(f"Invoice summary query failed: {e}")
        # Degrade gracefully to filtered-list counts rather than failing the request
        global_paid = None
        global_unpaid = None

    if global_paid is None:
        paid_count = sum(1 for inv in invoices if inv.get("status") == "paid")
        unpaid_count = sum(1 for inv in invoices if inv.get("status") in ["unpaid", "upcoming"])
    else:
        paid_count = global_paid
        unpaid_count = global_unpaid


    pm_info = await get_saved_payment_method_info(agency_id)

    return api_success(
        data={
            "invoices": invoices,
            "total": len(invoices),
            "summary": {
                "paid_count": paid_count,
                "unpaid_count": unpaid_count,
            },
            "payment_method": pm_info,
        },
        message="Invoices retrieved successfully"
    )



@router.get("/invoices/{invoice_id}")
async def get_single_invoice(
    invoice_id: str,
    current_user: dict = Depends(verify_token)
):
    """
    Get detailed breakdown of a single invoice. Owners/managers only.
    """
    role = current_user.get("role")
    if role not in ["owner", "manager", "super_admin"]:
        raise HTTPException(status_code=403, detail="Only owners and managers can view billing details")

    agency_id = require_agency_id(current_user)
    sb = get_supabase()
    try:
        res = sb.table("invoices").select("*").eq("agency_id", agency_id).eq("invoice_number", invoice_id).maybe_single().execute()
        if res and res.data:
            return api_success(data=res.data, message="Invoice details retrieved")
    except Exception as e:
        logger.debug(f"Single invoice query note: {e}")

    raise HTTPException(status_code=404, detail=f"Invoice '{invoice_id}' not found")


# ─── 7. Payment Method & Contract Overview ─────────────────────────────────────

@router.get("/payment-method")
async def get_saved_payment_method(current_user: dict = Depends(verify_token)):
    """Get active payment card summary. Owners/managers only."""
    role = current_user.get("role")
    if role not in ["owner", "manager", "super_admin"]:
        raise HTTPException(status_code=403, detail="Only owners and managers can view payment details")

    agency_id = require_agency_id(current_user)
    pm_info = await get_saved_payment_method_info(agency_id)
    return api_success(
        data=pm_info,
        message="Payment method retrieved"
    )



@router.post("/payment-method")
async def update_payment_method_handler(
    body: PaymentMethodRequest,
    current_user: dict = Depends(verify_token)
):
    """
    Save / update primary payment card details from Change Card modal.
    Accepts full card fields from UI modal or pre-parsed card_last4.
    """
    agency_id = require_agency_id(current_user)
    if current_user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can update payment methods")

    # PCI-DSS: raw card numbers are only tolerated for local development.
    # Production must use Stripe.js tokenization (payment_method_id).
    if (
        getattr(settings, "APP_ENV", "development") != "development"
        and not body.payment_method_id
        and body.card_number
    ):
        raise HTTPException(
            status_code=400,
            detail="Raw card data is not accepted. Use Stripe.js to tokenize the card first.",
        )

    updated_card = await update_saved_payment_method_info(
        agency_id=agency_id,
        card_number=body.card_number,
        name_on_card=body.name_on_card or body.name,
        expiry=body.expiry or body.card_expiry,
        cvc=body.cvc,
        card_last4=body.card_last4,
        card_brand=body.card_brand,
        is_primary=body.is_primary,
        payment_method_id=body.payment_method_id,
    )

    return api_success(
        data=updated_card,
        message="Payment card details updated successfully"
    )


@router.get("/contract")
async def get_subscription_contract_overview(
    request: Request,
    current_user: dict = Depends(verify_token)
):
    """Subscription screen contract details. Owners/managers only."""
    role = current_user.get("role")
    if role not in ["owner", "manager", "super_admin"]:
        raise HTTPException(status_code=403, detail="Only owners and managers can view contract details")

    agency_id = require_agency_id(current_user)
    sb = get_supabase()
    plan_tier = "grow"
    sub_row = {}
    try:
        sub_res = sb.table("subscriptions").select("*").eq("agency_id", agency_id).maybe_single().execute()
        if sub_res and sub_res.data:
            sub_row = sub_res.data
            plan_tier = sub_row.get("plan_tier", "grow")
        else:
            agency = sb.table("agencies").select("subscription_plan").eq("id", agency_id).maybe_single().execute()
            if agency and agency.data:
                plan_tier = agency.data.get("subscription_plan", "grow")
    except Exception as e:
        logger.debug(f"Contract overview note: {e}")

    plan_info = PLANS_METADATA.get(str(plan_tier).lower(), PLANS_METADATA["grow"])
    doc_url = f"{str(request.base_url).rstrip('/')}/subscription/contract/pdf"

    # Derive real financials from the active plan — no fabricated constants.
    gross_annual = float(plan_info["price_aed"]) * 12
    vat_amount = round(gross_annual * VAT_RATE, 2)
    duration_start = sub_row.get("billing_cycle_start")
    duration_end = sub_row.get("billing_cycle_end")

    return api_success(
        data={
            "contract_number": str(sub_row.get("stripe_sub_id") or "") or None,
            "product": f"{plan_info['display_name']} Plan + Agent Calls",
            "status": str(sub_row.get("status") or "inactive").title(),
            "duration_start": duration_start,
            "duration_end": duration_end,
            "payment_mode": "Credit/Debit Card" if getattr(settings, "STRIPE_SECRET_KEY", None) else None,
            "signed_by": None,
            "price_details": {
                "gross_amount_aed": gross_annual,
                "vat_5_percent_aed": vat_amount,
                "discount_percent": 0,
                "total_amount_aed": round(gross_annual + vat_amount, 2),
            },
            "document_url": doc_url,
        },
        message="Subscription contract retrieved successfully"
    )


@router.post("/cancel")
async def cancel_subscription_handler(
    body: CancelSubscriptionRequest,
    current_user: dict = Depends(verify_token)
):
    """
    Cancel the agency subscription. Owners only.
    By default cancels at period end (access retained until cycle end).
    """
    if current_user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can cancel the subscription")

    agency_id = require_agency_id(current_user)
    try:
        result = await cancel_subscription(agency_id, at_period_end=body.at_period_end)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Subscription cancellation failed for {agency_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to cancel subscription")

    return api_success(data=result, message="Subscription cancellation processed")


@router.get("/contract/pdf")
async def download_subscription_contract_pdf(
    request: Request,
    token: Optional[str] = Query(None),
):
    """Generate and stream the signed SaaS Master Subscription Agreement PDF."""
    sb = get_supabase()
    agency_id = None
    agency_name = "Registered Real Estate Agency"
    plan_tier = "grow"

    # 1. Extract token from Query param or Authorization Header
    auth_header = request.headers.get("authorization")
    raw_token = token
    if not raw_token and auth_header and auth_header.startswith("Bearer "):
        raw_token = auth_header[7:].strip()

    if raw_token:
        try:
            user_res = sb.auth.get_user(raw_token)
            if user_res and user_res.user:
                app_meta = user_res.user.app_metadata or {}
                agency_id = app_meta.get("agency_id")
                if not agency_id and user_res.user.email:
                    ag = sb.table("agents").select("agency_id").eq("email", user_res.user.email).maybe_single().execute()
                    if ag and ag.data:
                        agency_id = ag.data.get("agency_id")
        except Exception as e:
            logger.debug(f"PDF token decode note: {e}")

    if agency_id:
        try:
            sub_res = sb.table("subscriptions").select("*").eq("agency_id", agency_id).maybe_single().execute()
            if sub_res and sub_res.data:
                plan_tier = sub_res.data.get("plan_tier", "grow")
            agency = sb.table("agencies").select("name, subscription_plan").eq("id", agency_id).maybe_single().execute()
            if agency and agency.data:
                if not sub_res or not sub_res.data:
                    plan_tier = agency.data.get("subscription_plan", "grow")
                agency_name = agency.data.get("name") or agency_name
        except Exception as e:
            logger.debug(f"Contract PDF query note: {e}")

    plan_info = PLANS_METADATA.get(plan_tier.lower(), PLANS_METADATA["grow"])

    # Derive contract values from the real subscription row — no fabricated constants.
    sub_row = {}
    if agency_id:
        try:
            sub_res = sb.table("subscriptions").select("*").eq("agency_id", agency_id).maybe_single().execute()
            if sub_res and sub_res.data:
                sub_row = sub_res.data
        except Exception as e:
            logger.debug(f"Contract PDF sub note: {e}")

    gross_annual = float(plan_info["price_aed"]) * 12
    vat_amount = round(gross_annual * VAT_RATE, 2)

    contract_info = {
        "contract_number": sub_row.get("stripe_sub_id"),
        "product": f"{plan_info['display_name']} Plan + Agent Calls",
        "status": str(sub_row.get("status") or "inactive"),
        "duration_start": sub_row.get("billing_cycle_start"),
        "duration_end": sub_row.get("billing_cycle_end"),
        "payment_mode": "Credit/Debit Card" if getattr(settings, "STRIPE_SECRET_KEY", None) else None,
        "signed_by": None,
        "agency_name": agency_name,
        "price_details": {
            "gross_amount_aed": gross_annual,
            "vat_5_percent_aed": vat_amount,
            "discount_percent": 0,
            "total_amount_aed": round(gross_annual + vat_amount, 2),
        },
    }

    pdf_bytes = generate_subscription_agreement_pdf(contract_info)
    safe_number = str(contract_info["contract_number"] or "draft").replace("/", "-")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=AndiOS_Subscription_Contract_{safe_number}.pdf"
        }
    )



# Mock invoice fallback data removed — billing surfaces return only real records.
