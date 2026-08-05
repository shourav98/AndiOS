from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token
from utils.response import api_success
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/owners", tags=["Owners Database"])

# ─── Pydantic Models ──────────────────────────────────────────────────────────

class OwnerCreate(BaseModel):
    name: str
    phone: str
    email: Optional[EmailStr] = None
    property_group: Optional[str] = None
    property_unit: Optional[str] = None
    call_status: Optional[str] = "Not called"
    notes: Optional[str] = None

class OwnerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    property_group: Optional[str] = None
    property_unit: Optional[str] = None
    call_status: Optional[str] = None
    notes: Optional[str] = None

class BulkUploadRequest(BaseModel):
    owners: List[OwnerCreate]

# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("")
async def get_owners(
    group: Optional[str] = Query(None, description="Filter by property group"),
    current_user: dict = Depends(verify_token)
):
    """Get list of property owners from the database."""
    sb = get_supabase()
    agency_id = current_user.get("agency_id")

    if not agency_id:
        raise HTTPException(status_code=400, detail="User is not associated with any agency")

    try:
        query = sb.table("owners").select("*").eq("agency_id", agency_id)
        
        if group and group != "all":
            query = query.eq("property_group", group)
            
        result = query.order("created_at", desc=True).execute()
        return api_success(data=result.data, message="Owners retrieved successfully")
    except Exception as e:
        logger.error(f"Error fetching owners: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch owners")

@router.post("", status_code=201)
async def create_owner(owner: OwnerCreate, current_user: dict = Depends(verify_token)):
    """Add a single owner to the database."""
    sb = get_supabase()
    agency_id = current_user.get("agency_id")

    if not agency_id:
        raise HTTPException(status_code=400, detail="User is not associated with any agency")

    try:
        owner_data = owner.dict(exclude_unset=True)
        owner_data["agency_id"] = agency_id
        
        result = sb.table("owners").insert(owner_data).execute()
        if not result.data:
            raise HTTPException(status_code=400, detail="Failed to create owner")
            
        return api_success(data=result.data[0], message="Owner added successfully", status_code=201)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating owner: {e}")
        raise HTTPException(status_code=500, detail="Failed to create owner")

@router.post("/bulk-upload", status_code=201)
async def bulk_upload_owners(payload: BulkUploadRequest, current_user: dict = Depends(verify_token)):
    """Bulk upload multiple owners."""
    sb = get_supabase()
    agency_id = current_user.get("agency_id")

    if not agency_id:
        raise HTTPException(status_code=400, detail="User is not associated with any agency")

    try:
        insert_data = []
        for owner in payload.owners:
            data = owner.dict(exclude_unset=True)
            data["agency_id"] = agency_id
            insert_data.append(data)
            
        result = sb.table("owners").insert(insert_data).execute()
        
        return api_success(
            data={"count": len(result.data)}, 
            message=f"{len(result.data)} owners uploaded successfully", 
            status_code=201
        )
    except Exception as e:
        logger.error(f"Error bulk uploading owners: {e}")
        raise HTTPException(status_code=500, detail="Failed to bulk upload owners")

@router.get("/{owner_id}")
async def get_owner(owner_id: str, current_user: dict = Depends(verify_token)):
    """Get a specific owner by ID."""
    sb = get_supabase()
    agency_id = current_user.get("agency_id")

    try:
        result = sb.table("owners").select("*").eq("id", owner_id).eq("agency_id", agency_id).single().execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Owner not found")
        return api_success(data=result.data, message="Owner retrieved successfully")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching owner {owner_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch owner")
