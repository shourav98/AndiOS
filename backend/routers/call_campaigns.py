from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Any
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token
from utils.response import api_success
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

# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("")
async def get_call_campaigns(current_user: dict = Depends(verify_token)):
    """Get list of call campaigns."""
    sb = get_supabase()
    agency_id = current_user.get("agency_id")

    if not agency_id:
        raise HTTPException(status_code=400, detail="User is not associated with any agency")

    try:
        result = sb.table("call_campaigns").select("*").eq("agency_id", agency_id).order("created_at", desc=True).execute()
        
        # Transform data to match frontend requirements
        campaigns = []
        for row in result.data:
            # Calculate mock fractions for UI if data is 0
            total = row.get("total_owners", 0) or 100 # mock 100 if 0 for display
            answered = row.get("answered", 0)
            calls_to_list = row.get("calls_to_listings", 0)
            
            ans_pct = f"{int((answered/total)*100)}%" if total > 0 else "0%"
            list_pct = f"{round((calls_to_list/total)*100, 1)}%" if total > 0 else "0%"
            
            campaigns.append({
                "id": row["id"],
                "campaignName": row["campaign_name"],
                "campaignSubtitle": row.get("campaign_subtitle", f"Targeting {row['target_group']}"),
                "group": row["target_group"],
                "answerRate": {
                    "percentage": ans_pct,
                    "fraction": f"{answered}/{total}"
                },
                "callsToListings": {
                    "percentage": list_pct,
                    "fraction": f"{calls_to_list}/{total}"
                },
                "status": row["status"]
            })

        return api_success(data=campaigns, message="Campaigns retrieved successfully")
    except Exception as e:
        logger.error(f"Error fetching call campaigns: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch call campaigns")

@router.post("", status_code=201)
async def create_call_campaign(campaign: CallCampaignCreate, current_user: dict = Depends(verify_token)):
    """Create a new call campaign."""
    sb = get_supabase()
    agency_id = current_user.get("agency_id")

    if not agency_id:
        raise HTTPException(status_code=400, detail="User is not associated with any agency")

    try:
        # 1. Get the total number of owners in this group to initialize the total count
        owners_result = sb.table("owners").select("id", count="exact").eq("agency_id", agency_id).eq("property_group", campaign.group).execute()
        total_owners = owners_result.count if hasattr(owners_result, 'count') and owners_result.count is not None else len(owners_result.data)

        # 2. Insert campaign
        insert_data = {
            "agency_id": agency_id,
            "campaign_name": campaign.campaign_name,
            "target_group": campaign.group,
            "from_time": campaign.from_time,
            "to_time": campaign.to_time,
            "status": "Scheduled",
            "total_owners": total_owners
        }
        
        result = sb.table("call_campaigns").insert(insert_data).execute()
        if not result.data:
            raise HTTPException(status_code=400, detail="Failed to create campaign")
            
        return api_success(data=result.data[0], message="Campaign created successfully", status_code=201)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating call campaign: {e}")
        raise HTTPException(status_code=500, detail="Failed to create call campaign")

@router.get("/{campaign_id}")
async def get_campaign(campaign_id: str, current_user: dict = Depends(verify_token)):
    """Get a specific campaign by ID."""
    sb = get_supabase()
    agency_id = current_user.get("agency_id")

    try:
        result = sb.table("call_campaigns").select("*").eq("id", campaign_id).eq("agency_id", agency_id).single().execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
            
        row = result.data
        total = row.get("total_owners", 0) or 100
        answered = row.get("answered", 0)
        calls_to_list = row.get("calls_to_listings", 0)
        
        ans_pct = f"{int((answered/total)*100)}%" if total > 0 else "0%"
        list_pct = f"{round((calls_to_list/total)*100, 1)}%" if total > 0 else "0%"

        data = {
            "id": row["id"],
            "campaignName": row["campaign_name"],
            "campaignSubtitle": row.get("campaign_subtitle", f"Targeting {row['target_group']}"),
            "group": row["target_group"],
            "answerRate": {
                "percentage": ans_pct,
                "fraction": f"{answered}/{total}"
            },
            "callsToListings": {
                "percentage": list_pct,
                "fraction": f"{calls_to_list}/{total}"
            },
            "status": row["status"]
        }

        return api_success(data=data, message="Campaign retrieved successfully")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch campaign")
