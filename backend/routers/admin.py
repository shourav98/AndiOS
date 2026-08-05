from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token
from utils.response import api_success
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Super Admin"])

def require_super_admin(current_user: dict = Depends(verify_token)):
    """Middleware to ensure the user is a super admin."""
    role = current_user.get("role")
    if role != "super_admin":
        raise HTTPException(status_code=403, detail="Forbidden: Super admin access required")
    return current_user

class AgencyCreate(BaseModel):
    name: str
    admin_email: str
    subscription_status: str = "trialing"

@router.get("/agencies")
async def list_agencies(current_user: dict = Depends(require_super_admin)):
    """List all agencies in the system."""
    sb = get_supabase()
    try:
        result = sb.table("agencies").select("*").order("created_at", desc=True).execute()
        return api_success(data=result.data, message="Agencies retrieved successfully")
    except Exception as e:
        logger.error(f"Error fetching agencies: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch agencies")

@router.post("/agencies", status_code=201)
async def create_agency(agency: AgencyCreate, current_user: dict = Depends(require_super_admin)):
    """Create a new agency tenant."""
    sb = get_supabase()
    try:
        insert_data = {
            "name": agency.name,
            "subscription_status": agency.subscription_status,
            "is_active": True
        }
        result = sb.table("agencies").insert(insert_data).execute()
        if not result.data:
            raise HTTPException(status_code=400, detail="Failed to create agency")
            
        # In a real system we would also create an admin user for this agency in auth.users
        return api_success(data=result.data[0], message="Agency created successfully", status_code=201)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating agency: {e}")
        raise HTTPException(status_code=500, detail="Failed to create agency")

@router.patch("/agencies/{agency_id}/status")
async def update_agency_status(agency_id: str, status: str = Query(...), current_user: dict = Depends(require_super_admin)):
    """Update subscription status of an agency (active, suspended, trialing)."""
    sb = get_supabase()
    try:
        result = sb.table("agencies").update({"subscription_status": status}).eq("id", agency_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Agency not found")
        return api_success(data=result.data[0], message=f"Agency status updated to {status}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating agency {agency_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update agency status")
