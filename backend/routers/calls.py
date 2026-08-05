from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token
from utils.response import api_success
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/calls", tags=["Calls (Logs)"])

# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("")
async def get_calls(
    campaign_id: Optional[str] = Query(None, description="Filter by campaign ID"),
    status: Optional[str] = Query(None, description="Filter by status_value"),
    current_user: dict = Depends(verify_token)
):
    """Get list of call logs."""
    sb = get_supabase()
    agency_id = current_user.get("agency_id")

    if not agency_id:
        raise HTTPException(status_code=400, detail="User is not associated with any agency")

    try:
        query = sb.table("calls").select("*").eq("agency_id", agency_id)
        
        if campaign_id:
            query = query.eq("campaign_id", campaign_id)
            
        if status and status != "all":
            query = query.eq("status_value", status)
            
        result = query.order("call_time", desc=True).execute()
        
        # Transform data to match frontend requirements
        calls = []
        for row in result.data:
            call_time = row.get("call_time")
            formatted_time = ""
            if call_time:
                # Naive formatting to HH:MM assuming ISO format string
                try:
                    dt = datetime.fromisoformat(call_time.replace('Z', '+00:00'))
                    formatted_time = dt.strftime("%H:%M")
                except:
                    formatted_time = "00:00"

            duration = row.get("duration_seconds", 0)
            mins = duration // 60
            secs = duration % 60
            formatted_duration = f"{mins}:{secs:02d}" if duration > 0 else "—"
            
            calls.append({
                "id": row["id"],
                "time": formatted_time,
                "hasAudio": bool(row.get("audio_url")),
                "name": row.get("owner_name", "Unknown"),
                "role": row.get("owner_role", "Owner"),
                "location": row.get("property_location", "Unknown Location"),
                "status": row.get("status", "No answer"),
                "status_value": row.get("status_value", "no-answer"),
                "duration": formatted_duration,
                "audio_url": row.get("audio_url")
            })

        return api_success(data=calls, message="Calls retrieved successfully")
    except Exception as e:
        logger.error(f"Error fetching calls: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch calls")
