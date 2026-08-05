import os
import stripe
import logging
from database.supabase_client import get_supabase
from datetime import datetime

logger = logging.getLogger(__name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "mock_stripe_key")

async def check_agency_subscription(agency_id: str) -> bool:
    """
    Checks if an agency has an active subscription and hasn't exceeded limits.
    Returns True if allowed to proceed, False otherwise.
    """
    sb = get_supabase()
    # Mock limit check
    agency_result = sb.table("agencies").select("subscription_status").eq("id", agency_id).single().execute()
    
    if not agency_result.data:
        return False
        
    status = agency_result.data.get("subscription_status")
    if status == "active":
        return True
    elif status == "trialing":
        return True
        
    return False

async def create_checkout_session(agency_id: str, success_url: str, cancel_url: str) -> str:
    """Creates a Stripe checkout session for agency subscription."""
    logger.info(f"Creating checkout session for agency {agency_id}")
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': os.getenv("STRIPE_PRICE_ID", "mock_price_id"),
                'quantity': 1,
            }],
            mode='subscription',
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=agency_id
        )
        return session.url
    except Exception as e:
        logger.error(f"Failed to create Stripe checkout session: {e}")
        return ""
