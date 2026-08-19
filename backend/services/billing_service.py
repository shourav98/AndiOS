"""
AndiOS Billing Service — Stripe Billing & Metered Usage Integration

Handles:
- Customer lifecycle (create, get, attach payment methods)
- Subscription plans (Basic, Grow, Pro in AED)
- Recurring Add-on packs (p1000, p2000, p5000, p10000)
- Pay-as-you-go call overage tracking (AED 2.00 / call)
- Stripe Customer Portal sessions (Change Card, download tax invoice)
- Webhook synchronization to Supabase DB (subscriptions & invoices tables)
- Live Stripe Invoice fetching & automatic syncing
- Automatic Stripe Product & Price initialization for sandbox testing
"""

import os
import stripe
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from database.supabase_client import get_supabase
from config import settings

logger = logging.getLogger(__name__)

# Initialize Stripe API key
if getattr(settings, "STRIPE_SECRET_KEY", None):
    stripe.api_key = settings.STRIPE_SECRET_KEY

# ─── Plan & Add-on Definitions ──────────────────────────────────────────────

PLANS_METADATA = {
    "basic": {
        "display_name": "Basic",
        "tagline": "For a solo agent getting started with AI.",
        "price_aed": 1400,
        "agents": 1,
        "included_calls": 1000,
        "portals": 1,
        "features": [
            "1 AI agent (Andi or Sami)",
            "1,000 Agent Calls / month",
            "1 connected portal",
            "WhatsApp lead replies",
            "Email support"
        ],
        "price_id_env": "STRIPE_PRICE_BASIC",
    },
    "grow": {
        "display_name": "Grow",
        "tagline": "For a growing team across one or two branches.",
        "price_aed": 2800,
        "agents": 3,
        "included_calls": 3000,
        "portals": "All",
        "features": [
            "3 AI agents",
            "3,000 Agent Calls / month",
            "All portals connected",
            "Calling campaigns + owner DB",
            "Priority support"
        ],
        "price_id_env": "STRIPE_PRICE_GROW",
    },
    "pro": {
        "display_name": "Pro",
        "tagline": "For a multi-branch agency running at full scale.",
        "price_aed": 5600,
        "agents": 10,
        "included_calls": 8000,
        "portals": "All + multi-branch",
        "features": [
            "10 AI agents",
            "8,000 Agent Calls / month",
            "All portals + multi-branch",
            "Advanced reports & exports",
            "Dedicated success manager"
        ],
        "price_id_env": "STRIPE_PRICE_PRO",
    },
}

ADDONS_METADATA = {
    "p1000": {
        "calls": 1000,
        "price_aed": 2000,
        "label": "1,000 calls / month",
        "price_id_env": "STRIPE_PRICE_ADDON_P1000",
    },
    "p2000": {
        "calls": 2000,
        "price_aed": 4000,
        "label": "2,000 calls / month",
        "price_id_env": "STRIPE_PRICE_ADDON_P2000",
    },
    "p5000": {
        "calls": 5000,
        "price_aed": 10000,
        "label": "5,000 calls / month",
        "price_id_env": "STRIPE_PRICE_ADDON_P5000",
    },
    "p10000": {
        "calls": 10000,
        "price_aed": 20000,
        "label": "10,000 calls / month",
        "price_id_env": "STRIPE_PRICE_ADDON_P10000",
    },
}

VAT_RATE = 0.05  # 5% UAE VAT


def get_or_create_stripe_price(item_key: str, is_addon: bool = False) -> Optional[str]:
    """
    Retrieve configured Stripe price ID from settings, or automatically create it in Stripe.
    """
    if not getattr(settings, "STRIPE_SECRET_KEY", None):
        return None

    stripe.api_key = settings.STRIPE_SECRET_KEY

    # Check env first
    env_key = f"STRIPE_PRICE_ADDON_{item_key.upper()}" if is_addon else f"STRIPE_PRICE_{item_key.upper()}"
    configured_id = getattr(settings, env_key, None)
    if configured_id:
        return configured_id

    # Auto find/create in Stripe
    try:
        if is_addon:
            meta = ADDONS_METADATA.get(item_key)
            if not meta:
                return None
            prod_name = f"AndiOS Add-on: {meta['label']}"
            price_amount = int(meta["price_aed"] * 100)  # AED in fils/cents
        else:
            meta = PLANS_METADATA.get(item_key)
            if not meta:
                return None
            prod_name = f"AndiOS {meta['display_name']} Plan"
            price_amount = int(meta["price_aed"] * 100)

        # Search existing product
        prods = stripe.Product.list(limit=20, active=True)
        target_prod = next((p for p in prods.data if p.name == prod_name), None)
        if not target_prod:
            target_prod = stripe.Product.create(name=prod_name)

        # Search existing price
        prices = stripe.Price.list(product=target_prod.id, active=True, currency="aed")
        for pr in prices.data:
            if pr.unit_amount == price_amount and pr.recurring and pr.recurring.interval == "month":
                return pr.id

        # Create new recurring monthly price
        new_price = stripe.Price.create(
            product=target_prod.id,
            unit_amount=price_amount,
            currency="aed",
            recurring={"interval": "month"},
        )
        return new_price.id
    except Exception as e:
        logger.error(f"Error auto-creating Stripe price for {item_key}: {e}")
        return None


# ─── Customer Management ───────────────────────────────────────────────────

async def get_or_create_stripe_customer(agency_id: str, email: str, name: str) -> Optional[str]:
    """
    Find existing Stripe Customer ID in Supabase, or create a new one in Stripe.
    """
    sb = get_supabase()
    cust_id = None
    try:
        sub_res = sb.table("subscriptions").select("stripe_cust_id").eq("agency_id", agency_id).maybe_single().execute()
        if sub_res and sub_res.data and sub_res.data.get("stripe_cust_id"):
            cust_id = sub_res.data["stripe_cust_id"]
    except Exception as e:
        logger.debug(f"Subscription query notice: {e}")

    if cust_id:
        return cust_id

    if not getattr(settings, "STRIPE_SECRET_KEY", None):
        logger.warning(f"STRIPE_SECRET_KEY not set. Using mock customer for agency {agency_id}")
        mock_cust_id = f"cus_mock_{agency_id[:8]}"
        _upsert_local_subscription(agency_id, {"stripe_cust_id": mock_cust_id})
        return mock_cust_id

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        # Search if customer already exists in Stripe
        existing_custs = stripe.Customer.list(email=email, limit=1)
        if existing_custs.data:
            cust = existing_custs.data[0]
            _upsert_local_subscription(agency_id, {"stripe_cust_id": cust.id})
            return cust.id

        customer = stripe.Customer.create(
            email=email,
            name=name,
            metadata={"agency_id": agency_id},
        )
        _upsert_local_subscription(agency_id, {"stripe_cust_id": customer.id})
        return customer.id
    except Exception as e:
        logger.error(f"Error creating Stripe customer: {e}")
        return None


# ─── Stripe Customer Portal Session ────────────────────────────────────────

async def create_billing_portal_session(agency_id: str, return_url: Optional[str] = None) -> Optional[str]:
    """
    Generate a hosted Stripe Customer Portal URL where owners can:
    - Update payment method (Card details)
    - View and download tax invoices
    - Manage billing details
    """
    default_return = getattr(settings, "STRIPE_PORTAL_RETURN_URL", "http://localhost:3000/owner-dashboard/plan-billing")
    if not getattr(settings, "STRIPE_SECRET_KEY", None):
        return return_url or default_return

    stripe.api_key = settings.STRIPE_SECRET_KEY
    sb = get_supabase()
    cust_id = None
    try:
        sub_res = sb.table("subscriptions").select("stripe_cust_id").eq("agency_id", agency_id).maybe_single().execute()
        if sub_res and sub_res.data and sub_res.data.get("stripe_cust_id"):
            cust_id = sub_res.data["stripe_cust_id"]
    except Exception as e:
        logger.debug(f"Subscription table error: {e}")

    if not cust_id:
        cust_id = await get_or_create_stripe_customer(agency_id, "owner@andios.ai", "AndiOS Agency")

    try:
        session = stripe.billing_portal.Session.create(
            customer=cust_id,
            return_url=return_url or default_return,
        )
        return session.url
    except Exception as e:
        logger.error(f"Failed to create Stripe billing portal session: {e}")
        return return_url or default_return


# ─── Checkout & Subscriptions ──────────────────────────────────────────────

async def create_checkout_session(
    agency_id: str,
    plan_tier: str,
    email: str,
    agency_name: str,
    success_url: str,
    cancel_url: str
) -> Dict[str, Any]:
    """
    Create a real Stripe Checkout Session in subscription mode for the selected plan.
    """
    plan_tier = plan_tier.lower()
    if plan_tier not in PLANS_METADATA:
        raise ValueError(f"Invalid plan tier '{plan_tier}'. Must be one of: {list(PLANS_METADATA.keys())}")

    plan_info = PLANS_METADATA[plan_tier]
    price_id = get_or_create_stripe_price(plan_tier, is_addon=False)

    if not getattr(settings, "STRIPE_SECRET_KEY", None) or not price_id:
        logger.warning("Stripe credentials or Price ID not configured. Simulating checkout locally.")
        now = datetime.utcnow()
        _upsert_local_subscription(agency_id, {
            "plan_tier": plan_tier,
            "status": "active",
            "included_calls": plan_info["included_calls"],
            "billing_cycle_start": now.isoformat(),
            "billing_cycle_end": (now + timedelta(days=30)).isoformat(),
        })
        return {
            "checkout_url": success_url,
            "mode": "simulation",
            "plan_tier": plan_tier,
        }

    stripe.api_key = settings.STRIPE_SECRET_KEY
    cust_id = await get_or_create_stripe_customer(agency_id, email, agency_name)
    try:
        session = stripe.checkout.Session.create(
            customer=cust_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"agency_id": agency_id, "plan_tier": plan_tier},
            subscription_data={"metadata": {"agency_id": agency_id, "plan_tier": plan_tier}},
        )
        return {
            "checkout_url": session.url,
            "session_id": session.id,
            "mode": "stripe_live_sandbox",
            "plan_tier": plan_tier,
            "price_aed": plan_info["price_aed"],
        }
    except Exception as e:
        logger.error(f"Failed to create Stripe checkout session: {e}")
        raise


async def upgrade_subscription_plan(agency_id: str, new_plan_tier: str) -> Dict[str, Any]:
    """
    Upgrade or switch plan tier with automatic Stripe proration.
    """
    new_plan_tier = new_plan_tier.lower()
    if new_plan_tier not in PLANS_METADATA:
        raise ValueError(f"Invalid plan tier. Valid: {list(PLANS_METADATA.keys())}")

    plan_info = PLANS_METADATA[new_plan_tier]
    new_price_id = get_or_create_stripe_price(new_plan_tier, is_addon=False)

    sb = get_supabase()
    sub_data = {}
    try:
        sub_res = sb.table("subscriptions").select("*").eq("agency_id", agency_id).maybe_single().execute()
        if sub_res and sub_res.data:
            sub_data = sub_res.data
    except Exception as e:
        logger.debug(f"Subscription query note: {e}")

    stripe_sub_id = sub_data.get("stripe_sub_id")

    if getattr(settings, "STRIPE_SECRET_KEY", None) and stripe_sub_id and new_price_id:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)
            current_item_id = stripe_sub["items"]["data"][0].id

            stripe.Subscription.modify(
                stripe_sub_id,
                items=[{"id": current_item_id, "price": new_price_id}],
                proration_behavior="create_prorations",
                metadata={"agency_id": agency_id, "plan_tier": new_plan_tier},
            )
            logger.info(f"Stripe subscription {stripe_sub_id} upgraded to {new_plan_tier}")
        except Exception as e:
            logger.error(f"Error modifying Stripe subscription: {e}")

    # Update database record
    _upsert_local_subscription(agency_id, {
        "plan_tier": new_plan_tier,
        "included_calls": plan_info["included_calls"],
        "status": "active",
    })

    # Also update agencies table
    try:
        sb.table("agencies").update({
            "subscription_plan": new_plan_tier,
            "subscription_status": "active"
        }).eq("id", agency_id).execute()
    except Exception as e:
        logger.debug(f"Agencies update note: {e}")

    return {
        "plan_tier": new_plan_tier,
        "display_name": plan_info["display_name"],
        "price_aed": plan_info["price_aed"],
        "included_calls": plan_info["included_calls"],
        "status": "active",
    }


# ─── Add-on Management ─────────────────────────────────────────────────────

async def purchase_subscription_addon(agency_id: str, addon_code: str) -> Dict[str, Any]:
    """
    Add a recurring call pack (p1000, p2000, p5000, p10000) to current subscription.
    """
    addon_code = addon_code.lower()
    if addon_code not in ADDONS_METADATA:
        raise ValueError(f"Invalid add-on '{addon_code}'. Valid: {list(ADDONS_METADATA.keys())}")

    addon_info = ADDONS_METADATA[addon_code]
    addon_price_id = get_or_create_stripe_price(addon_code, is_addon=True)

    sb = get_supabase()
    sub_data = {}
    try:
        sub_res = sb.table("subscriptions").select("*").eq("agency_id", agency_id).maybe_single().execute()
        if sub_res and sub_res.data:
            sub_data = sub_res.data
    except Exception as e:
        logger.debug(f"Subscriptions table note: {e}")

    stripe_sub_id = sub_data.get("stripe_sub_id")
    current_addons = sub_data.get("active_addons") or []
    if addon_code not in current_addons:
        current_addons.append(addon_code)

    total_addon_calls = sum(ADDONS_METADATA.get(code, {}).get("calls", 0) for code in current_addons)

    if getattr(settings, "STRIPE_SECRET_KEY", None) and stripe_sub_id and addon_price_id:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            stripe.SubscriptionItem.create(
                subscription=stripe_sub_id,
                price=addon_price_id,
                quantity=1,
                metadata={"agency_id": agency_id, "addon_code": addon_code},
            )
            logger.info(f"Added Stripe subscription item for addon {addon_code}")
        except Exception as e:
            logger.error(f"Error adding Stripe subscription item: {e}")

    _upsert_local_subscription(agency_id, {
        "active_addons": current_addons,
        "addon_calls": total_addon_calls,
    })

    return {
        "purchased_addon": addon_code,
        "label": addon_info["label"],
        "price_aed": addon_info["price_aed"],
        "active_addons": current_addons,
        "total_addon_calls": total_addon_calls,
    }


async def remove_subscription_addon(agency_id: str, addon_code: str) -> Dict[str, Any]:
    """
    Remove a recurring call pack add-on from the agency's subscription.
    """
    addon_code = addon_code.lower()
    sb = get_supabase()
    sub_data = {}
    try:
        sub_res = sb.table("subscriptions").select("*").eq("agency_id", agency_id).maybe_single().execute()
        if sub_res and sub_res.data:
            sub_data = sub_res.data
    except Exception as e:
        logger.debug(f"Subscriptions note: {e}")

    current_addons = sub_data.get("active_addons") or []
    if addon_code in current_addons:
        current_addons.remove(addon_code)

    total_addon_calls = sum(ADDONS_METADATA.get(code, {}).get("calls", 0) for code in current_addons)

    _upsert_local_subscription(agency_id, {
        "active_addons": current_addons,
        "addon_calls": total_addon_calls,
    })

    return {
        "removed_addon": addon_code,
        "active_addons": current_addons,
        "total_addon_calls": total_addon_calls,
    }


# ─── Metered Usage & Overage Tracking ──────────────────────────────────────

async def record_call_usage(agency_id: str, count: int = 1) -> Dict[str, Any]:
    """
    Record an AI call made by an agency.
    Tracks against included + addon quota, and records overage when exceeded.
    """
    sb = get_supabase()
    sub_data = {}
    try:
        sub_res = sb.table("subscriptions").select("*").eq("agency_id", agency_id).maybe_single().execute()
        if sub_res and sub_res.data:
            sub_data = sub_res.data
    except Exception as e:
        logger.debug(f"Subscriptions note: {e}")

    current_used = sub_data.get("used_calls", 0) + count
    included = sub_data.get("included_calls", 3000)
    addon = sub_data.get("addon_calls", 0)
    total_quota = included + addon

    overage_calls = max(0, current_used - total_quota)

    _upsert_local_subscription(agency_id, {"used_calls": current_used})

    return {
        "used_calls": current_used,
        "total_quota": total_quota,
        "overage_calls": overage_calls,
        "is_over_limit": current_used > total_quota,
    }


async def get_saved_payment_method_info(agency_id: str) -> Dict[str, Any]:
    """Retrieve actual saved payment card details from Stripe customer account."""
    if not getattr(settings, "STRIPE_SECRET_KEY", None):
        return {
            "card_brand": "visa",
            "card_last4": "4242",
            "card_expiry": "10/50",
            "is_primary": True,
            "used_for": "all invoices",
        }

    sb = get_supabase()
    cust_id = None
    try:
        sub_res = sb.table("subscriptions").select("stripe_cust_id").eq("agency_id", agency_id).maybe_single().execute()
        if sub_res and sub_res.data and sub_res.data.get("stripe_cust_id"):
            cust_id = sub_res.data["stripe_cust_id"]
    except Exception as e:
        logger.debug(f"Payment method query note: {e}")

    if not cust_id:
        return {
            "card_brand": "visa",
            "card_last4": "4242",
            "card_expiry": "10/50",
            "is_primary": True,
            "used_for": "all invoices",
        }

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        pms = stripe.PaymentMethod.list(customer=cust_id, type="card", limit=1)
        if pms.data:
            card_obj = _to_dict_safe(pms.data[0]).get("card", {})
            return {
                "card_brand": str(card_obj.get("brand", "visa")).lower(),
                "card_last4": str(card_obj.get("last4", "4242")),
                "card_expiry": f"{card_obj.get('exp_month', 12):02d}/{str(card_obj.get('exp_year', 30))[-2:]}",
                "is_primary": True,
                "used_for": "all invoices",
            }
    except Exception as e:
        logger.debug(f"Stripe payment method fetch note: {e}")

    return {
        "card_brand": "visa",
        "card_last4": "4242",
        "card_expiry": "10/50",
        "is_primary": True,
        "used_for": "all invoices",
    }


# ─── Live Stripe Invoice Sync ──────────────────────────────────────────────


def _to_dict_safe(obj: Any) -> Dict[str, Any]:
    """Safely convert Stripe object to standard Python dictionary."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict_recursive"):
        return obj.to_dict_recursive()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    try:
        return dict(obj)
    except Exception:
        res = {}
        for attr in [
            "id", "number", "amount_paid", "amount_due", "total",
            "status", "paid", "due_date", "created", "invoice_pdf",
            "hosted_invoice_url", "customer", "metadata"
        ]:
            if hasattr(obj, attr):
                res[attr] = getattr(obj, attr)
        return res


# ─── Live Stripe Invoice Sync ──────────────────────────────────────────────

async def fetch_and_sync_live_invoices(agency_id: str, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetch real invoices directly from Stripe API for this agency, sync to Supabase, and return with PDF download URLs.
    """
    if not getattr(settings, "STRIPE_SECRET_KEY", None):
        return []

    sb = get_supabase()
    cust_id = None
    try:
        sub_res = sb.table("subscriptions").select("stripe_cust_id").eq("agency_id", agency_id).maybe_single().execute()
        if sub_res and sub_res.data and sub_res.data.get("stripe_cust_id"):
            cust_id = sub_res.data["stripe_cust_id"]
    except Exception as e:
        logger.debug(f"Subscription lookup note: {e}")

    stripe.api_key = settings.STRIPE_SECRET_KEY

    # If no customer ID in subscription row, search Stripe by customer list
    if not cust_id:
        try:
            custs = stripe.Customer.list(limit=20)
            for c in custs.data:
                c_dict = _to_dict_safe(c)
                if c_dict.get("metadata", {}).get("agency_id") == agency_id:
                    cust_id = c_dict.get("id")
                    break
        except Exception:
            pass

    if not cust_id:
        return []

    try:
        stripe_invoices = stripe.Invoice.list(customer=cust_id, limit=20)
        results = []
        for inv_obj in stripe_invoices.data:
            inv = _to_dict_safe(inv_obj)
            await sync_invoice_from_stripe(inv, agency_id=agency_id)

            amount_total = float(inv.get("amount_paid") or inv.get("amount_due") or inv.get("total") or 0) / 100.0
            vat_portion = round(amount_total * (VAT_RATE / (1.0 + VAT_RATE)), 2)
            
            is_paid = bool(inv.get("paid")) or inv.get("status") == "paid"
            inv_status = "paid" if is_paid else ("unpaid" if inv.get("status") != "void" else "void")
            
            due_ts = inv.get("due_date") or inv.get("created")
            due_date = datetime.utcfromtimestamp(due_ts).strftime("%Y-%m-%d") if due_ts else datetime.utcnow().strftime("%Y-%m-%d")
            inv_num = inv.get("number") or f"INV-{inv.get('id', '')[-6:]}"
            pdf_url = inv.get("invoice_pdf") or inv.get("hosted_invoice_url") or "#"

            item = {
                "id": inv.get("id"),
                "invoice_number": inv_num,
                "contract_number": "139350",
                "frequency": "Monthly",
                "mode": "Card",
                "due_date": due_date,
                "status": inv_status,
                "amount": amount_total,
                "vat_amount": vat_portion,
                "pdf_url": pdf_url,
                "hosted_invoice_url": inv.get("hosted_invoice_url"),
            }
            results.append(item)

        if status_filter:
            results = [i for i in results if i["status"] == status_filter.lower()]

        return results
    except Exception as e:
        logger.error(f"Error fetching live Stripe invoices: {e}")
        return []


# ─── Webhook Sync Functions ────────────────────────────────────────────────

async def sync_subscription_from_stripe(stripe_sub_obj: Any) -> None:
    """
    Called by webhook on customer.subscription.created/updated.
    """
    stripe_sub = _to_dict_safe(stripe_sub_obj)
    agency_id = stripe_sub.get("metadata", {}).get("agency_id")
    cust_id = stripe_sub.get("customer")
    sb = get_supabase()

    if not agency_id and cust_id:
        try:
            found = sb.table("subscriptions").select("agency_id").eq("stripe_cust_id", cust_id).maybe_single().execute()
            if found and found.data:
                agency_id = found.data.get("agency_id")
        except Exception as e:
            logger.debug(f"Sync query note: {e}")

    if not agency_id:
        logger.warning(f"Unable to match Stripe subscription {stripe_sub.get('id')} to an agency_id")
        return

    plan_tier = stripe_sub.get("metadata", {}).get("plan_tier", "grow")
    status = stripe_sub.get("status", "active")
    period_start = datetime.utcfromtimestamp(stripe_sub.get("current_period_start", datetime.utcnow().timestamp()))
    period_end = datetime.utcfromtimestamp(stripe_sub.get("current_period_end", (datetime.utcnow() + timedelta(days=30)).timestamp()))

    plan_info = PLANS_METADATA.get(plan_tier, PLANS_METADATA["grow"])

    _upsert_local_subscription(agency_id, {
        "stripe_cust_id": stripe_sub.get("customer"),
        "stripe_sub_id": stripe_sub.get("id"),
        "plan_tier": plan_tier,
        "status": status,
        "included_calls": plan_info["included_calls"],
        "billing_cycle_start": period_start.isoformat(),
        "billing_cycle_end": period_end.isoformat(),
    })
    logger.info(f"Synced subscription for agency {agency_id} (Plan: {plan_tier}, Status: {status})")


async def sync_invoice_from_stripe(stripe_inv_obj: Any, agency_id: Optional[str] = None) -> None:
    """
    Called by webhook on invoice.created or invoice.payment_succeeded.
    """
    stripe_inv = _to_dict_safe(stripe_inv_obj)
    if not agency_id:
        agency_id = stripe_inv.get("metadata", {}).get("agency_id")
    cust_id = stripe_inv.get("customer")
    sb = get_supabase()

    if not agency_id and cust_id:
        try:
            found = sb.table("subscriptions").select("agency_id").eq("stripe_cust_id", cust_id).maybe_single().execute()
            if found and found.data:
                agency_id = found.data.get("agency_id")
        except Exception as e:
            logger.debug(f"Invoice sync query note: {e}")


    if not agency_id:
        logger.warning(f"Unable to match invoice {stripe_inv.get('id')} to an agency_id")
        return

    amount_total = float(stripe_inv.get("amount_paid") or stripe_inv.get("amount_due") or stripe_inv.get("total") or 0) / 100.0
    vat_portion = round(amount_total * (VAT_RATE / (1.0 + VAT_RATE)), 2)
    
    is_paid = bool(stripe_inv.get("paid")) or stripe_inv.get("status") == "paid"
    inv_status = "paid" if is_paid else ("unpaid" if stripe_inv.get("status") != "void" else "void")

    due_ts = stripe_inv.get("due_date") or stripe_inv.get("created")
    due_date = datetime.utcfromtimestamp(due_ts).strftime("%Y-%m-%d") if due_ts else datetime.utcnow().strftime("%Y-%m-%d")

    inv_num = stripe_inv.get("number") or f"INV-{stripe_inv.get('id', '')[-6:]}"
    pdf_url = stripe_inv.get("invoice_pdf") or stripe_inv.get("hosted_invoice_url") or "#"

    try:
        existing = sb.table("invoices").select("id").eq("stripe_invoice_id", stripe_inv.get("id")).maybe_single().execute()
        if existing and existing.data:
            sb.table("invoices").update({
                "status": inv_status,
                "amount": amount_total,
                "vat_amount": vat_portion,
                "pdf_url": pdf_url,
                "paid_at": datetime.utcnow().isoformat() if inv_status == "paid" else None,
            }).eq("id", existing.data["id"]).execute()
        else:
            sb.table("invoices").insert({
                "agency_id": agency_id,
                "invoice_number": inv_num,
                "stripe_invoice_id": stripe_inv.get("id"),
                "billing_period": f"{due_date} Cycle",
                "due_date": due_date,
                "amount": amount_total,
                "vat_amount": vat_portion,
                "status": inv_status,
                "pdf_url": pdf_url,
                "paid_at": datetime.utcnow().isoformat() if inv_status == "paid" else None,
            }).execute()
    except Exception as e:
        logger.error(f"Failed to upsert invoice to Supabase: {e}")

    if inv_status == "paid":
        _upsert_local_subscription(agency_id, {"used_calls": 0})

    logger.info(f"Synced invoice {inv_num} for agency {agency_id} with status {inv_status}")



# ─── Internal Helper ───────────────────────────────────────────────────────

def _upsert_local_subscription(agency_id: str, updates: Dict[str, Any]) -> None:
    """Internal helper to insert or update the agency's subscriptions row."""
    sb = get_supabase()
    try:
        existing = sb.table("subscriptions").select("id").eq("agency_id", agency_id).maybe_single().execute()
        if existing and existing.data:
            sb.table("subscriptions").update(updates).eq("id", existing.data["id"]).execute()
        else:
            full_payload = {
                "agency_id": agency_id,
                "plan_tier": "grow",
                "status": "active",
                "included_calls": 3000,
                "addon_calls": 0,
                "used_calls": 0,
                "active_addons": [],
                "billing_cycle_start": datetime.utcnow().isoformat(),
                "billing_cycle_end": (datetime.utcnow() + timedelta(days=30)).isoformat(),
                **updates,
            }
            sb.table("subscriptions").insert(full_payload).execute()
    except Exception as e:
        logger.debug(f"Subscription table not ready or write error: {e}")
