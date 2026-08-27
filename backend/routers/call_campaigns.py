from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Any
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token
from utils.response import api_success
from utils.tenant import require_agency_id, is_management_role
from utils.plan_limits import check_campaign_limit, get_plan_limits
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/call-campaigns", tags=["Call Campaigns"])


# ─── Pydantic Models ──────────────────────────────────────────────────────────

class CallCampaignCreate(BaseModel):
    campaign_name: str
    group: str
    from_time: Optional[str] = None
    to_time: Optional[str] = None


class CallCampaignUpdate(BaseModel):
    status: str


# ─── Helper: Build campaign response with real KPIs ──────────────────────────

def _build_campaign_response(row: dict, calls_data: list = None) -> dict:
    """Build a formatted campaign response with real aggregate KPIs from calls table."""
    total = row.get("total_owners", 0) or 1  # avoid division by zero
    answered = row.get("answered", 0) or 0
    calls_to_list = row.get("calls_to_listings", 0) or 0

    ans_pct = f"{int((answered / total) * 100)}%" if total > 0 else "0%"
    list_pct = f"{round((calls_to_list / max(answered, 1)) * 100, 1)}%"

    return {
        "id": row["id"],
        "campaignName": row["campaign_name"],
        "campaignSubtitle": row.get("campaign_subtitle", f"Targeting {row['target_group']}"),
        "group": row["target_group"],
        "answerRate": {
            "percentage": ans_pct,
            "fraction": f"{answered}/{total}",
        },
        "callsToListings": {
            "percentage": list_pct,
            "fraction": f"{calls_to_list}/{answered or total}",
        },
        "status": row["status"],
        "totalOwners": total,
        "answered": answered,
        "listingsWon": calls_to_list,
        "fromTime": row.get("from_time"),
        "toTime": row.get("to_time"),
        "createdAt": row.get("created_at"),
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("")
async def get_call_campaigns(current_user: dict = Depends(verify_token)):
    """Get list of call campaigns with real KPI data."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    result = sb.table("call_campaigns").select("*").eq("agency_id", agency_id).order("created_at", desc=True).execute()

    campaigns = [_build_campaign_response(row) for row in result.data]
    return api_success(data=campaigns, message="Campaigns retrieved successfully")


@router.post("", status_code=201)
async def create_call_campaign(campaign: CallCampaignCreate, current_user: dict = Depends(verify_token)):
    """Create a new call campaign. Enforces the plan's monthly campaign quota."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    # Enforce plan quota (raises 403 when the monthly limit is reached and
    # blocks suspended/cancelled subscriptions)
    check_campaign_limit(agency_id)

    # Count owners in the target group (excluding DNC)
    owners_result = (
        sb.table("owners")
        .select("id", count="exact")
        .eq("agency_id", agency_id)
        .eq("property_group", campaign.group)
        .eq("dnc_flag", False)
        .execute()
    )
    total_owners = owners_result.count if hasattr(owners_result, "count") and owners_result.count is not None else len(owners_result.data)

    insert_data = {
        "agency_id": agency_id,
        "campaign_name": campaign.campaign_name,
        "target_group": campaign.group,
        "from_time": campaign.from_time,
        "to_time": campaign.to_time,
        "status": "Scheduled",
        "total_owners": total_owners,
    }

    result = sb.table("call_campaigns").insert(insert_data).execute()
    if not result.data:
        raise HTTPException(status_code=400, detail="Failed to create campaign")

    return api_success(data=result.data[0], message="Campaign created successfully", status_code=201)


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: str, current_user: dict = Depends(verify_token)):
    """Get a specific campaign with real KPI data."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    result = sb.table("call_campaigns").select("*").eq("id", campaign_id).eq("agency_id", agency_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return api_success(data=_build_campaign_response(result.data), message="Campaign retrieved successfully")


@router.post("/{campaign_id}/run")
async def run_campaign(campaign_id: str, current_user: dict = Depends(verify_token)):
    """
    Start or resume a calling campaign. Owners/managers only — running a
    campaign triggers paid outbound dials. Also validates that the agency's
    subscription is active.
    """
    if not is_management_role(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Only owners and managers can run campaigns")

    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    # Validate subscription status (raises 403 when suspended/cancelled)
    get_plan_limits(agency_id)

    campaign = sb.table("call_campaigns").select("*").eq("id", campaign_id).eq("agency_id", agency_id).single().execute()
    if not campaign.data:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if campaign.data["status"] == "Completed":
        raise HTTPException(status_code=400, detail="Campaign already completed")

    import os
    from config import settings
    vapi_key = (getattr(settings, "VAPI_API_KEY", "") or os.getenv("VAPI_API_KEY", "")).strip()
    is_production = getattr(settings, "APP_ENV", "development") != "development"
    if is_production and not vapi_key:
        raise HTTPException(
            status_code=400,
            detail="Vapi AI calling is not configured. Please configure VAPI_API_KEY and VAPI_ASSISTANT_ID in environment."
        )

    # Update status to Running
    sb.table("call_campaigns").update({"status": "Running"}).eq("id", campaign_id).execute()

    # Trigger first batch
    from services.vapi_service import run_campaign_batch
    await run_campaign_batch(campaign_id, agency_id, batch_size=10)

    # Schedule recurring batches via APScheduler
    from services.scheduler import scheduler
    from apscheduler.triggers.interval import IntervalTrigger

    job_id = f"campaign_{campaign_id}"
    if not scheduler.get_job(job_id):
        scheduler.add_job(
            run_campaign_batch,
            trigger=IntervalTrigger(minutes=5),
            args=[campaign_id, agency_id],
            id=job_id,
            replace_existing=True,
        )

    return api_success(
        data={"campaign_id": campaign_id, "status": "Running"},
        message="Campaign started — dialing owners",
    )


@router.post("/{campaign_id}/pause")
async def pause_campaign(campaign_id: str, current_user: dict = Depends(verify_token)):
    """Pause a running campaign."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    campaign = sb.table("call_campaigns").select("status").eq("id", campaign_id).eq("agency_id", agency_id).single().execute()
    if not campaign.data:
        raise HTTPException(status_code=404, detail="Campaign not found")

    sb.table("call_campaigns").update({"status": "Paused"}).eq("id", campaign_id).execute()

    # Remove scheduled job
    from services.scheduler import scheduler
    job_id = f"campaign_{campaign_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    return api_success(
        data={"campaign_id": campaign_id, "status": "Paused"},
        message="Campaign paused",
    )


@router.get("/{campaign_id}/calls")
async def get_campaign_calls(campaign_id: str, current_user: dict = Depends(verify_token)):
    """Get all call logs for a specific campaign."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    # Verify campaign belongs to agency
    campaign = sb.table("call_campaigns").select("id").eq("id", campaign_id).eq("agency_id", agency_id).execute()
    if not campaign.data:
        raise HTTPException(status_code=404, detail="Campaign not found")

    result = sb.table("calls").select("*").eq("campaign_id", campaign_id).order("call_time", desc=True).execute()
    return api_success(data=result.data, message="Campaign calls retrieved")
