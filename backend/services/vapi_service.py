import os
import httpx
import logging
from datetime import datetime
from typing import Optional
import pytz
from config import settings
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

VAPI_API_KEY = os.getenv("VAPI_API_KEY", "")
VAPI_BASE_URL = "https://api.vapi.ai"
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", "")
VAPI_PHONE_NUMBER_ID = os.getenv("VAPI_PHONE_NUMBER_ID", "")


def is_within_calling_hours(timezone_str: str = "Asia/Dubai") -> bool:
    """Check if current time is within allowed calling hours (9 AM to 6 PM, Mon-Sat)."""
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    # Sunday = 6 in Python (but in Dubai, Friday is off. Let's do Mon-Sat = 0-5)
    if now.weekday() == 4:  # Friday off in Dubai
        return False
    return 9 <= now.hour < 18


async def trigger_outbound_call(
    phone_number: str,
    owner_name: str,
    assistant_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """
    Triggers an outbound call using Vapi.ai.
    Ensures the call is only made during allowed hours and configures voicemail detection.
    """
    if not VAPI_API_KEY:
        logger.warning("VAPI_API_KEY not set — skipping actual call")
        return {"status": "mock", "message": "Vapi API key not configured", "mock": True}

    if not is_within_calling_hours():
        logger.warning(f"Attempted to call {phone_number} outside of calling hours.")
        return {"status": "deferred", "message": "Outside of calling hours"}

    headers = {
        "Authorization": f"Bearer {VAPI_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "assistantId": assistant_id or VAPI_ASSISTANT_ID,
        "customer": {
            "number": phone_number,
            "name": owner_name,
        },
        "phoneNumberId": VAPI_PHONE_NUMBER_ID,
        "voicemailDetection": {
            "provider": "twilio",
            "voicemailDetectionTypes": ["machine_end_beep", "machine_end_other"],
        },
    }
    if metadata:
        payload["metadata"] = metadata

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{VAPI_BASE_URL}/call",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            logger.info(f"Vapi call triggered to {phone_number} for {owner_name} — call_id={data.get('id')}")
            return {"status": "initiated", "vapi_call_id": data.get("id"), **data}
    except Exception as e:
        logger.error(f"Failed to trigger Vapi call to {phone_number}: {e}")
        return {"status": "error", "message": str(e)}


async def run_campaign_batch(campaign_id: str, agency_id: str, batch_size: int = 10):
    """
    Dials the next batch of owners in a campaign.
    Called by the campaign runner scheduler.
    """
    sb = get_supabase()

    # Get campaign details
    campaign = sb.table("call_campaigns").select("*").eq("id", campaign_id).single().execute()
    if not campaign.data:
        logger.error(f"Campaign {campaign_id} not found")
        return

    camp = campaign.data
    if camp["status"] not in ("Running", "Scheduled"):
        logger.info(f"Campaign {campaign_id} is {camp['status']} — skipping batch")
        return

    # Check calling hours
    if not is_within_calling_hours():
        logger.info(f"Outside calling hours — deferring campaign {campaign_id}")
        return

    # Update status to Running
    sb.table("call_campaigns").update({"status": "Running"}).eq("id", campaign_id).execute()

    # Get owners in this group who haven't been called yet in this campaign
    already_called = sb.table("calls").select("owner_id").eq("campaign_id", campaign_id).execute()
    called_ids = {row["owner_id"] for row in already_called.data if row.get("owner_id")}

    owners_query = (
        sb.table("owners")
        .select("*")
        .eq("agency_id", agency_id)
        .eq("property_group", camp["target_group"])
        .eq("dnc_flag", False)
        .order("created_at")
        .limit(batch_size + len(called_ids))  # over-fetch to skip already-called
    )
    owners = owners_query.execute().data

    # Filter out already called
    to_call = [o for o in owners if o["id"] not in called_ids][:batch_size]

    if not to_call:
        # Campaign complete — all owners called
        sb.table("call_campaigns").update({"status": "Completed"}).eq("id", campaign_id).execute()
        logger.info(f"Campaign {campaign_id} completed — all owners dialed")
        return

    # Dial each owner
    for owner in to_call:
        call_result = await trigger_outbound_call(
            phone_number=owner["phone"],
            owner_name=owner["name"],
            metadata={
                "campaign_id": campaign_id,
                "owner_id": owner["id"],
                "agency_id": agency_id,
            },
        )

        # Insert call record
        call_record = {
            "agency_id": agency_id,
            "campaign_id": campaign_id,
            "owner_id": owner["id"],
            "owner_name": owner["name"],
            "phone_number": owner["phone"],
            "property_location": owner.get("property_group", ""),
            "status": "Initiated" if call_result.get("status") == "initiated" else "Failed",
            "status_value": "initiated" if call_result.get("status") == "initiated" else "failed",
            "vapi_call_id": call_result.get("vapi_call_id"),
            "call_time": datetime.utcnow().isoformat(),
        }
        sb.table("calls").insert(call_record).execute()

        # Update owner call status
        sb.table("owners").update({"call_status": "Called"}).eq("id", owner["id"]).execute()

    # Update campaign counters
    total_called = len(called_ids) + len(to_call)
    sb.table("call_campaigns").update({
        "total_owners": len(owners),
    }).eq("id", campaign_id).execute()

    logger.info(f"Campaign {campaign_id}: dialed {len(to_call)} owners (total: {total_called})")


async def process_vapi_webhook(payload: dict) -> dict:
    """
    Process Vapi webhook callback — stores call result, transcript, recording.
    Updates owner DNC flag if outcome is 'do_not_call'.
    Schedules retry if voicemail/no-answer.
    """
    sb = get_supabase()

    vapi_call_id = payload.get("call", {}).get("id") or payload.get("id", "")
    call_type = payload.get("type", "")  # 'end-of-call-report', 'status-update', etc.

    if call_type != "end-of-call-report":
        logger.info(f"Vapi webhook type={call_type} — ignoring (only processing end-of-call-report)")
        return {"status": "ignored", "type": call_type}

    call_data = payload.get("call", {})
    metadata = call_data.get("metadata", {})
    campaign_id = metadata.get("campaign_id")
    owner_id = metadata.get("owner_id")
    agency_id = metadata.get("agency_id")

    # Extract call details
    transcript = payload.get("transcript", "")
    recording_url = payload.get("recordingUrl") or call_data.get("recordingUrl", "")
    duration = payload.get("duration") or call_data.get("duration", 0)
    ended_reason = call_data.get("endedReason", "")
    summary = payload.get("summary", "")

    # Determine outcome
    analysis = payload.get("analysis", {})
    outcome = analysis.get("outcome", "no_answer").lower().replace(" ", "_")

    # Map outcomes to display values
    OUTCOME_MAP = {
        "listing_won": ("Listing won", "listing-won"),
        "callback_booked": ("Callback booked", "callback-booked"),
        "interested": ("Interested", "interested"),
        "not_interested": ("Not interested", "not-interested"),
        "do_not_call": ("Do not call", "do-not-call"),
        "voicemail": ("Voicemail", "voicemail"),
        "no_answer": ("No answer", "no-answer"),
        "busy": ("Busy", "busy"),
    }
    display, value = OUTCOME_MAP.get(outcome, ("No answer", "no-answer"))

    # Update call record
    update_data = {
        "status": display,
        "status_value": value,
        "duration_seconds": int(duration) if duration else 0,
        "transcript": transcript,
        "audio_url": recording_url,
        "summary": summary,
        "ended_reason": ended_reason,
    }

    if vapi_call_id:
        sb.table("calls").update(update_data).eq("vapi_call_id", vapi_call_id).execute()

    # Handle DNC
    if outcome == "do_not_call" and owner_id:
        sb.table("owners").update({"dnc_flag": True, "call_status": "DNC"}).eq("id", owner_id).execute()
        logger.info(f"Owner {owner_id} marked as DNC")

    # Handle listing won — update campaign counter
    if outcome == "listing_won" and campaign_id:
        campaign = sb.table("call_campaigns").select("calls_to_listings, answered").eq("id", campaign_id).single().execute()
        if campaign.data:
            sb.table("call_campaigns").update({
                "calls_to_listings": (campaign.data.get("calls_to_listings", 0) or 0) + 1,
            }).eq("id", campaign_id).execute()

    # Update answered count for all answered calls
    if outcome not in ("no_answer", "voicemail", "busy") and campaign_id:
        campaign = sb.table("call_campaigns").select("answered").eq("id", campaign_id).single().execute()
        if campaign.data:
            sb.table("call_campaigns").update({
                "answered": (campaign.data.get("answered", 0) or 0) + 1,
            }).eq("id", campaign_id).execute()

    # Schedule retry for voicemail/no-answer
    if outcome in ("voicemail", "no_answer", "busy") and owner_id:
        sb.table("owners").update({"call_status": "Retry"}).eq("id", owner_id).execute()

    logger.info(f"Vapi webhook processed: call={vapi_call_id}, outcome={outcome}")
    return {"status": "processed", "outcome": outcome}
