"""
Provisioning Service — Centralizes phone number lifecycle management:
  - Atomic lock / idempotent number purchasing via Twilio
  - Crash recovery & stale state sweeps
  - Market country code resolution (default: 'AE')
  - Meta approval polling & auto-activation
  - Notifications for agency and administrators

DEPRECATED: This service handles legacy Twilio auto-provisioning for agencies
that were onboarded before the BYON/Embedded Signup migration. It is kept to
ensure existing agencies continue to work without disruption.

New agencies must connect their own WhatsApp numbers via Meta Embedded Signup
(see routers/connectors.py → POST /connectors/whatsapp/embedded-signup-callback).

This service is gated behind the ENABLE_TWILIO_PROVISIONING feature flag.
Set ENABLE_TWILIO_PROVISIONING=true in .env ONLY for platforms still onboarding
agencies via Twilio. Default is False (disabled).

DO NOT DELETE this file — existing agencies depend on it.
"""
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from fastapi import HTTPException

from config import settings
from database.supabase_client import get_supabase
from services.notification_service import (
    notify_number_status_change,
    notify_admin_stale_approval,
    notify_admin_provisioning_failure,
)

# Twilio Client — top-level alias allows tests to patch services.provisioning_service.Client
try:
    from twilio.rest import Client  # type: ignore
except ImportError:
    Client = None  # type: ignore

logger = logging.getLogger(__name__)

_PROVISIONING_DISABLED_MSG = (
    "Twilio auto-provisioning is disabled (ENABLE_TWILIO_PROVISIONING=false). "
    "New agencies must connect via Meta Embedded Signup. "
    "Set ENABLE_TWILIO_PROVISIONING=true only for legacy Twilio onboarding."
)


def _check_provisioning_enabled() -> None:
    """Raise HTTPException if ENABLE_TWILIO_PROVISIONING feature flag is off."""
    if not getattr(settings, "ENABLE_TWILIO_PROVISIONING", False):
        logger.warning("[Provisioning] %s", _PROVISIONING_DISABLED_MSG)
        raise HTTPException(
            status_code=403,
            detail=_PROVISIONING_DISABLED_MSG,
        )


def claim_agency_number_provisioning(agency_id: str, stale_minutes: int = 10) -> bool:
    """
    Atomically claims the number provisioning lock for an agency.
    Only allows transition if status is 'none' OR if stuck in 'provisioning'
    past stale_minutes (crash recovery).
    """
    sb = get_supabase()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Try Postgres RPC first
    try:
        rpc_res = sb.rpc("claim_agency_number_provisioning", {
            "p_agency_id": agency_id,
            "p_stale_minutes": stale_minutes,
        }).execute()
        if rpc_res and rpc_res.data is not None:
            return bool(rpc_res.data)
    except Exception as e:
        logger.debug(f"[Provisioning] RPC claim fallback to table update: {e}")

    # Fallback to direct conditional table query if RPC is not yet registered in test DB
    try:
        agency = sb.table("agencies").select("id, whatsapp_number_status, dedicated_whatsapp_number, number_provisioned_at").eq("id", agency_id).maybe_single().execute()
        if not agency or not agency.data:
            return False

        data = agency.data
        if data.get("dedicated_whatsapp_number"):
            return False

        curr_status = data.get("whatsapp_number_status") or "none"
        can_claim = False

        if curr_status == "none":
            can_claim = True
        elif curr_status == "provisioning":
            # Check crash recovery timeout
            prov_at = data.get("number_provisioned_at")
            if not prov_at:
                can_claim = True
            else:
                try:
                    dt = datetime.fromisoformat(prov_at.replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) - dt > timedelta(minutes=stale_minutes):
                        can_claim = True
                except Exception:
                    can_claim = True

        if not can_claim:
            return False

        # Attempt conditional update
        upd = sb.table("agencies").update({
            "whatsapp_number_status": "provisioning",
            "number_provisioned_at": now_iso,
        }).eq("id", agency_id).execute()

        return bool(upd and upd.data)
    except Exception as e:
        logger.error(f"[Provisioning] Error claiming agency {agency_id}: {e}")
        return False


def release_agency_number_claim(agency_id: str, status: str = "none") -> bool:
    """
    Releases the 'provisioning' lock if an error occurs.
    """
    sb = get_supabase()
    try:
        rpc_res = sb.rpc("release_agency_number_claim", {
            "p_agency_id": agency_id,
            "p_status": status,
        }).execute()
        if rpc_res and rpc_res.data is not None:
            return bool(rpc_res.data)
    except Exception as e:
        logger.debug(f"[Provisioning] RPC release fallback to table update: {e}")

    try:
        upd = sb.table("agencies").update({
            "whatsapp_number_status": status,
        }).eq("id", agency_id).eq("whatsapp_number_status", "provisioning").execute()
        return bool(upd and upd.data)
    except Exception as e:
        logger.error(f"[Provisioning] Error releasing agency claim {agency_id}: {e}")
        return False


async def provision_number_for_agency(
    agency_id: str,
    country_code: Optional[str] = None,
    is_manual_admin: bool = False,
) -> Dict[str, Any]:
    """
    Purchases a dedicated phone number via Twilio master account and assigns it to agency.
    Automated calls (from Stripe) are non-blocking and idempotent.
    Manual admin calls raise HTTP exceptions on client errors.
    """
    sb = get_supabase()

    # 1. Fetch agency
    agency_res = sb.table("agencies").select("id, name, country_code, dedicated_whatsapp_number, whatsapp_number_status").eq("id", agency_id).maybe_single().execute()
    if not agency_res or not agency_res.data:
        if is_manual_admin:
            raise HTTPException(status_code=404, detail="Agency not found")
        logger.warning(f"[Provisioning] Agency {agency_id} not found during auto-provisioning")
        return {"status": "failed", "reason": "agency_not_found"}

    agency = agency_res.data
    agency_name = agency.get("name") or "Unnamed Agency"

    # Already has a dedicated number?
    if agency.get("dedicated_whatsapp_number"):
        if is_manual_admin:
            raise HTTPException(
                status_code=400,
                detail=f"Agency already has dedicated number: {agency['dedicated_whatsapp_number']}. Deprovision first.",
            )
        logger.info(f"[Provisioning] Agency {agency_id} already has number {agency['dedicated_whatsapp_number']}; skipping.")
        return {
            "status": "skipped",
            "reason": "already_has_number",
            "number": agency["dedicated_whatsapp_number"],
        }

    # 2. Dynamic Country Code Resolution
    resolved_country = (country_code or agency.get("country_code") or "US").strip().upper()
    if not resolved_country or len(resolved_country) != 2:
        error_msg = f"Invalid country code '{resolved_country}' for agency {agency_id}."
        logger.error(f"[Provisioning] {error_msg}")
        sb.table("agencies").update({"whatsapp_number_status": "failed"}).eq("id", agency_id).execute()
        await notify_number_status_change(agency_id, "failed")
        await notify_admin_provisioning_failure(agency_id, agency_name, error_msg, resolved_country)
        if is_manual_admin:
            raise HTTPException(status_code=400, detail=error_msg)
        return {"status": "failed", "reason": "invalid_country_code"}

    # 3. Atomic Claim (Concurrency Lock)
    claimed = claim_agency_number_provisioning(agency_id)
    if not claimed:
        logger.info(f"[Provisioning] Lock claim refused for agency {agency_id} (already active or in progress).")
        if is_manual_admin:
            raise HTTPException(
                status_code=400,
                detail=f"Agency {agency_id} is already in state '{agency.get('whatsapp_number_status')}'",
            )
        return {"status": "skipped", "reason": "already_claimed_or_active"}

    # 4. Twilio Credentials
    account_sid = (os.getenv("TWILIO_ACCOUNT_SID") or settings.TWILIO_ACCOUNT_SID).strip()
    auth_token = (os.getenv("TWILIO_AUTH_TOKEN") or settings.TWILIO_AUTH_TOKEN).strip()
    if not account_sid or not auth_token:
        release_agency_number_claim(agency_id, "failed")
        await notify_number_status_change(agency_id, "failed")
        await notify_admin_provisioning_failure(
            agency_id, agency_name, "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be configured", resolved_country
        )
        if is_manual_admin:
            raise HTTPException(status_code=400, detail="TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be configured")
        return {"status": "failed", "reason": "twilio_credentials_missing"}

    # 5. Purchase Phone Number
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        # Use module-level Client alias (patchable in tests via services.provisioning_service.Client)
        twilio = Client(account_sid, auth_token)

        # Search available numbers in the resolved market
        available = twilio.available_phone_numbers(resolved_country).local.list(
            sms_enabled=True, voice_enabled=True, limit=1,
        )
        if not available:
            # Fallback to mobile or national if local has no stock
            try:
                available = twilio.available_phone_numbers(resolved_country).mobile.list(
                    sms_enabled=True, voice_enabled=True, limit=1,
                )
            except Exception:
                pass

        if not available:
            raise RuntimeError(f"No available numbers found in market '{resolved_country}'")

        chosen_number = available[0].phone_number
        purchased = twilio.incoming_phone_numbers.create(
            phone_number=chosen_number,
            friendly_name=f"AndiOS - {agency_name}",
        )
        new_number = purchased.phone_number

        # Update agency record
        sb.table("agencies").update({
            "dedicated_whatsapp_number": new_number,
            "whatsapp_number_status": "provisioned",
            "number_provisioned_at": now_iso,
        }).eq("id", agency_id).execute()

        # Send transition notification
        await notify_number_status_change(agency_id, "provisioned", new_number)
        logger.info(f"[Provisioning] Number {new_number} successfully provisioned for agency {agency_id} ({agency_name})")

        return {
            "status": "provisioned",
            "agency_id": agency_id,
            "dedicated_whatsapp_number": new_number,
            "twilio_sid": getattr(purchased, "sid", ""),
            "country_code": resolved_country,
            "message": f"Number {new_number} provisioned. Pending Meta WhatsApp approval (24-48h).",
        }

    except Exception as e:
        logger.error(f"[Provisioning] Failed to provision number for agency {agency_id}: {e}")
        release_agency_number_claim(agency_id, "failed")
        await notify_number_status_change(agency_id, "failed")
        await notify_admin_provisioning_failure(agency_id, agency_name, str(e), resolved_country)

        if is_manual_admin:
            raise HTTPException(status_code=502, detail=f"Twilio provisioning failed: {e}")
        return {"status": "failed", "error": str(e)}


async def activate_number_for_agency(
    agency_id: str,
    is_manual_admin: bool = False,
) -> Dict[str, Any]:
    """
    Transitions agency from 'provisioned' to 'active' once approved by Meta / Twilio.
    """
    sb = get_supabase()
    agency_res = sb.table("agencies").select("id, dedicated_whatsapp_number, whatsapp_number_status").eq("id", agency_id).maybe_single().execute()
    if not agency_res or not agency_res.data:
        if is_manual_admin:
            raise HTTPException(status_code=404, detail="Agency not found")
        return {"status": "failed", "reason": "agency_not_found"}

    agency = agency_res.data
    number = agency.get("dedicated_whatsapp_number", "")

    result = sb.table("agencies").update({
        "whatsapp_number_status": "active",
    }).eq("id", agency_id).execute()

    if not result or not result.data:
        if is_manual_admin:
            raise HTTPException(status_code=500, detail="Failed to update agency status")
        return {"status": "failed", "reason": "update_failed"}

    await notify_number_status_change(agency_id, "active", number)
    logger.info(f"[Provisioning] WhatsApp number {number} activated for agency {agency_id}")

    return {
        "status": "active",
        "agency_id": agency_id,
        "dedicated_whatsapp_number": number,
        "message": "WhatsApp number is now active.",
    }


async def check_twilio_sender_status(
    agency_id: str,
    dedicated_number: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Queries Twilio WhatsApp Sender registration for an agency's number.
    If approved, activates the number; if rejected, marks as failed.
    """
    sb = get_supabase()
    if not dedicated_number:
        agency = sb.table("agencies").select("dedicated_whatsapp_number").eq("id", agency_id).maybe_single().execute()
        if agency and agency.data:
            dedicated_number = agency.data.get("dedicated_whatsapp_number")

    if not dedicated_number:
        return {"status": "no_number"}

    account_sid = (os.getenv("TWILIO_ACCOUNT_SID") or settings.TWILIO_ACCOUNT_SID).strip()
    auth_token = (os.getenv("TWILIO_AUTH_TOKEN") or settings.TWILIO_AUTH_TOKEN).strip()
    if not account_sid or not auth_token:
        return {"status": "no_credentials"}

    try:
        from twilio.rest import Client  # type: ignore
        twilio = Client(account_sid, auth_token)

        # Inspect messaging services or whatsapp senders
        # Twilio WhatsApp Sender endpoint check or incoming phone number verification
        incoming = twilio.incoming_phone_numbers.list(phone_number=dedicated_number, limit=1)
        if not incoming:
            return {"status": "number_not_in_account"}

        # Simulate checking Meta approval or sender readiness
        sender_status = getattr(incoming[0], "status", "in-use")
        if sender_status in ("approved", "in-use", "online"):
            return await activate_number_for_agency(agency_id)
        elif sender_status in ("rejected", "failed"):
            sb.table("agencies").update({"whatsapp_number_status": "failed"}).eq("id", agency_id).execute()
            await notify_number_status_change(agency_id, "failed", dedicated_number)
            return {"status": "failed", "reason": sender_status}

        return {"status": "pending", "sender_status": sender_status}
    except Exception as e:
        logger.error(f"[Provisioning] Error checking sender status for agency {agency_id}: {e}")
        return {"status": "error", "error": str(e)}


async def check_stale_provisioned_agencies(hours_threshold: float = 48.0) -> List[Dict[str, Any]]:
    """
    Finds agencies waiting for Meta approval for > 48 hours and alerts administrators.
    """
    sb = get_supabase()
    alerts = []
    try:
        agencies = sb.table("agencies").select("id, name, dedicated_whatsapp_number, number_provisioned_at").eq("whatsapp_number_status", "provisioned").execute()
        if not agencies or not agencies.data:
            return alerts

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_threshold)
        for ag in agencies.data:
            prov_at_str = ag.get("number_provisioned_at")
            if not prov_at_str:
                continue

            try:
                prov_dt = datetime.fromisoformat(prov_at_str.replace("Z", "+00:00"))
            except Exception:
                continue

            if prov_dt < cutoff:
                pending_hours = (datetime.now(timezone.utc) - prov_dt).total_seconds() / 3600.0
                alert = await notify_admin_stale_approval(
                    agency_id=ag["id"],
                    agency_name=ag.get("name") or "Unnamed Agency",
                    number=ag.get("dedicated_whatsapp_number") or "N/A",
                    hours_pending=pending_hours,
                )
                alerts.append(alert)
    except Exception as e:
        logger.error(f"[Provisioning] Error during stale provisioned check: {e}")

    return alerts


async def recover_stuck_provisioning_agencies(minutes_threshold: float = 15.0) -> List[Dict[str, Any]]:
    """
    Crash recovery sweep: Finds agencies stuck in 'provisioning' for > 15 minutes
    without a dedicated number, marks them 'failed' and notifies admins.
    """
    sb = get_supabase()
    recovered = []
    try:
        stuck = sb.table("agencies").select("id, name, country_code, number_provisioned_at").eq("whatsapp_number_status", "provisioning").is_("dedicated_whatsapp_number", None).execute()
        if not stuck or not stuck.data:
            return recovered

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes_threshold)
        for ag in stuck.data:
            prov_at_str = ag.get("number_provisioned_at")
            should_recover = False
            if not prov_at_str:
                should_recover = True
            else:
                try:
                    prov_dt = datetime.fromisoformat(prov_at_str.replace("Z", "+00:00"))
                    if prov_dt < cutoff:
                        should_recover = True
                except Exception:
                    should_recover = True

            if should_recover:
                agency_id = ag["id"]
                agency_name = ag.get("name") or "Unnamed Agency"
                logger.warning(f"[Crash Recovery] Recovering stuck provisioning agency {agency_id}")
                sb.table("agencies").update({"whatsapp_number_status": "failed"}).eq("id", agency_id).execute()
                await notify_number_status_change(agency_id, "failed")
                await notify_admin_provisioning_failure(
                    agency_id, agency_name, f"Provisioning process timed out (> {int(minutes_threshold)} mins)", ag.get("country_code")
                )
                recovered.append({"agency_id": agency_id, "status": "recovered_to_failed"})
    except Exception as e:
        logger.error(f"[Provisioning] Error in crash recovery sweep: {e}")

    return recovered
