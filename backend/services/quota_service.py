"""
Quota Service — Centralized shared gateway quota enforcement.

All quota operations use atomic Postgres RPC functions to prevent race conditions
on burst traffic (check + increment happen in a SINGLE database round-trip).

Usage pattern in webhook handlers:
    allowed = await check_and_consume_whatsapp_quota(agency_id)
    if not allowed:
        # Log suppressed, save message, do NOT send AI reply
        return {"status": "suppressed", "reason": "Monthly usage quota exceeded"}
    try:
        await process_andi_ai_response(...)
    except Exception as ai_error:
        # Refund quota only on AI processing failure (not Twilio delivery failure)
        await refund_whatsapp_quota(agency_id)
        raise
"""
import logging
from database.supabase_client import get_supabase
from config import settings

logger = logging.getLogger(__name__)


# ─── WhatsApp Quota ───────────────────────────────────────────────────────────

async def check_and_consume_whatsapp_quota(agency_id: str) -> bool:
    """
    Atomically check if agency has WhatsApp quota remaining, and consume 1 unit.

    Uses a single Postgres RPC (check_and_increment_whatsapp_quota) that
    performs UPDATE ... WHERE used < limit in one transaction — eliminating
    any SELECT→UPDATE race condition on burst traffic.

    Returns:
        True  — quota available, 1 unit consumed, message can proceed.
        False — blocked (is_quota_frozen=True OR used >= limit), no mutation.

    In development (QUOTA_ENFORCEMENT_ENABLED=False), always returns True.
    """
    if not settings.QUOTA_ENFORCEMENT_ENABLED:
        logger.debug(f"[Quota] Enforcement disabled — allowing agency {agency_id}")
        return True

    try:
        sb = get_supabase()
        result = sb.rpc(
            "check_and_increment_whatsapp_quota",
            {"agency_uuid": agency_id}
        ).execute()
        allowed: bool = result.data
        if not allowed:
            logger.warning(f"[Quota] WhatsApp quota BLOCKED for agency {agency_id}")
        return allowed
    except Exception as e:
        # Fail open on DB errors (don't block users due to quota service outage)
        logger.error(f"[Quota] WhatsApp quota check error for {agency_id}: {e} — failing open")
        return True


async def refund_whatsapp_quota(agency_id: str) -> None:
    """
    Refund 1 WhatsApp quota unit — called ONLY when AI processing fails
    AFTER quota was already consumed.

    NOT called for Twilio delivery failures (those are valid usage attempts).
    Uses GREATEST(0, used-1) to prevent negative counters.
    """
    if not settings.QUOTA_ENFORCEMENT_ENABLED:
        return

    try:
        sb = get_supabase()
        sb.rpc("decrement_whatsapp_used", {"agency_uuid": agency_id}).execute()
        logger.info(f"[Quota] WhatsApp quota refunded for agency {agency_id} (AI failure)")
    except Exception as e:
        logger.error(f"[Quota] Quota refund error for {agency_id}: {e}")


# ─── Voice Quota (Vapi / Sami AI) ─────────────────────────────────────────────

async def check_and_consume_voice_quota(agency_id: str) -> bool:
    """
    Atomically check if agency has voice call quota remaining, and consume 1 unit.
    Same atomic pattern as check_and_consume_whatsapp_quota.
    Called before launching any Vapi outbound call.
    """
    if not settings.QUOTA_ENFORCEMENT_ENABLED:
        logger.debug(f"[Quota] Enforcement disabled — allowing voice for agency {agency_id}")
        return True

    try:
        sb = get_supabase()
        result = sb.rpc(
            "check_and_increment_voice_quota",
            {"agency_uuid": agency_id}
        ).execute()
        allowed: bool = result.data
        if not allowed:
            logger.warning(f"[Quota] Voice quota BLOCKED for agency {agency_id}")
        return allowed
    except Exception as e:
        logger.error(f"[Quota] Voice quota check error for {agency_id}: {e} — failing open")
        return True


async def refund_voice_quota(agency_id: str) -> None:
    """Refund 1 voice quota unit after Vapi call setup failure."""
    if not settings.QUOTA_ENFORCEMENT_ENABLED:
        return

    try:
        sb = get_supabase()
        sb.rpc("decrement_voice_used", {"agency_uuid": agency_id}).execute()
        logger.info(f"[Quota] Voice quota refunded for agency {agency_id}")
    except Exception as e:
        logger.error(f"[Quota] Voice refund error for {agency_id}: {e}")


# ─── Freeze / Unfreeze ────────────────────────────────────────────────────────

async def freeze_agency(agency_id: str, reason: str = "quota_exceeded") -> None:
    """
    Explicitly set is_quota_frozen=True for an agency.
    Note: The atomic RPC already blocks on limit without setting this flag.
    This is for admin-triggered freezes (e.g. payment failure, abuse).
    The dashboard shows frozen if: is_quota_frozen OR used >= limit.
    """
    try:
        sb = get_supabase()
        sb.table("agencies").update({"is_quota_frozen": True}).eq("id", agency_id).execute()
        logger.warning(f"[Quota] Agency {agency_id} FROZEN — reason: {reason}")
    except Exception as e:
        logger.error(f"[Quota] Freeze error for {agency_id}: {e}")


async def unfreeze_agency(agency_id: str) -> None:
    """Unfreeze an agency (e.g. after Add-on purchase or manual admin action)."""
    try:
        sb = get_supabase()
        sb.table("agencies").update({"is_quota_frozen": False}).eq("id", agency_id).execute()
        logger.info(f"[Quota] Agency {agency_id} UNFROZEN")
    except Exception as e:
        logger.error(f"[Quota] Unfreeze error for {agency_id}: {e}")


# ─── Monthly Reset ────────────────────────────────────────────────────────────

async def reset_all_quotas(agency_ids: list[str] | None = None) -> int:
    """
    Reset monthly usage counters and clear frozen flag.
    Called by POST /admin/quota/monthly-reset (GitHub Actions on 1st of each month).

    Args:
        agency_ids: If None, resets ALL active agencies. Otherwise only the listed ones.

    Returns:
        Number of agencies reset.
    """
    try:
        sb = get_supabase()
        if agency_ids:
            result = sb.rpc(
                "reset_monthly_quotas",
                {"target_agency_ids": agency_ids}
            ).execute()
        else:
            result = sb.rpc("reset_monthly_quotas", {}).execute()
        count = result.data or 0
        logger.info(f"[Quota] Monthly reset complete — {count} agencies reset")
        return count
    except Exception as e:
        logger.error(f"[Quota] Monthly reset error: {e}")
        raise


# ─── Usage Stats ──────────────────────────────────────────────────────────────

def get_agency_quota_status(agency_id: str) -> dict:
    """
    Fetch current quota stats for an agency.
    Dashboard frozen badge should use:
      is_frozen = is_quota_frozen OR (whatsapp_monthly_used >= whatsapp_monthly_limit)
    """
    sb = get_supabase()
    result = sb.table("agencies").select(
        "whatsapp_monthly_limit, whatsapp_monthly_used, "
        "voice_monthly_limit, voice_monthly_used, "
        "is_quota_frozen, quota_reset_at, whatsapp_number_status"
    ).eq("id", agency_id).single().execute()

    if not result.data:
        return {}

    data = result.data
    wa_limit = data["whatsapp_monthly_limit"] or 0
    wa_used = data["whatsapp_monthly_used"] or 0
    vo_limit = data["voice_monthly_limit"] or 0
    vo_used = data["voice_monthly_used"] or 0

    return {
        "whatsapp": {
            "limit": wa_limit,
            "used": wa_used,
            "remaining": max(0, wa_limit - wa_used),
            "percent_used": round((wa_used / wa_limit * 100), 1) if wa_limit > 0 else 0,
        },
        "voice": {
            "limit": vo_limit,
            "used": vo_used,
            "remaining": max(0, vo_limit - vo_used),
            "percent_used": round((vo_used / vo_limit * 100), 1) if vo_limit > 0 else 0,
        },
        # Dashboard should display frozen badge when either condition is true
        "is_frozen_display": data["is_quota_frozen"] or (wa_used >= wa_limit),
        "is_quota_frozen": data["is_quota_frozen"],
        "whatsapp_number_status": data["whatsapp_number_status"],
        "quota_reset_at": data["quota_reset_at"],
    }
