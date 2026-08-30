"""
Branches Router — Agency Branch Management
POST   /branches       — create new branch
GET    /branches       — list all branches with agent count
GET    /branches/{id}  — get single branch
PATCH  /branches/{id}  — update branch
DELETE /branches/{id}  — delete branch
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, List
import uuid
from datetime import datetime, timezone
import logging

from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token
from utils.response import api_success, ApiResponse
from utils.tenant import require_agency_id, is_management_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/branches", tags=["Branches"])


class BranchCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, description="Branch name (e.g. Downtown Dubai)")
    location: Optional[str] = Field(None, max_length=255, description="Physical location or address")
    phone: Optional[str] = Field(None, max_length=50, description="Branch phone number")
    notes: Optional[str] = Field(None, max_length=500, description="Optional notes")


class BranchUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    location: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None


def _get_agency_branches_dict(sb, agency_id: str) -> tuple[dict, list]:
    """Retrieve stored branches and agent rows for an agency."""
    agency_res = sb.table("agencies").select("settings").eq("id", agency_id).maybe_single().execute()
    agency_settings = (agency_res.data or {}).get("settings") or {}
    stored_branches = agency_settings.get("branches") or []

    agents_res = sb.table("agents").select("id, name, branch").eq("agency_id", agency_id).eq("is_active", True).execute()
    agent_rows = agents_res.data or []

    return agency_settings, stored_branches, agent_rows


@router.get("", response_model=ApiResponse[list])
async def list_branches(current_user: dict = Depends(verify_token)):
    """
    List all branches for the current agency with unique IDs and real-time agent counts.
    """
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    agency_settings, stored_branches, agent_rows = _get_agency_branches_dict(sb, agency_id)

    # Build agent count map by branch name and branch id
    agent_count_by_name = {}
    for a in agent_rows:
        b_val = (a.get("branch") or "").strip()
        if b_val:
            agent_count_by_name[b_val.lower()] = agent_count_by_name.get(b_val.lower(), 0) + 1

    branches_list = []
    seen_names = set()

    # 1. Process explicitly stored branches
    for b in stored_branches:
        b_name = b.get("name", "").strip()
        if not b_name:
            continue
        count = agent_count_by_name.get(b_name.lower(), 0) + agent_count_by_name.get(b.get("id", "").lower(), 0)
        branches_list.append({
            "id": b.get("id"),
            "name": b_name,
            "location": b.get("location") or b_name,
            "phone": b.get("phone"),
            "notes": b.get("notes"),
            "agent_count": count,
            "created_at": b.get("created_at") or datetime.now(timezone.utc).isoformat(),
        })
        seen_names.add(b_name.lower())

    # 2. Discover any branches assigned on agents but not yet explicitly configured in settings
    for a in agent_rows:
        b_val = (a.get("branch") or "").strip()
        if b_val and b_val.lower() not in seen_names:
            auto_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{agency_id}-{b_val}"))
            count = agent_count_by_name.get(b_val.lower(), 0)
            branches_list.append({
                "id": auto_id,
                "name": b_val,
                "location": b_val,
                "phone": None,
                "notes": "Auto-discovered from team assignments",
                "agent_count": count,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            seen_names.add(b_val.lower())

    branches_list.sort(key=lambda x: x["name"])
    return api_success(data=branches_list, message="Branches retrieved successfully")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_branch(body: BranchCreate, current_user: dict = Depends(verify_token)):
    """
    Create a new branch for the agency.
    Requires owner or manager role.
    """
    if not is_management_role(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Only owners and managers can create branches")

    sb = get_supabase()
    agency_id = require_agency_id(current_user)
    branch_name = body.name.strip()

    agency_settings, stored_branches, agent_rows = _get_agency_branches_dict(sb, agency_id)

    # Check for duplicate branch name
    for b in stored_branches:
        if b.get("name", "").strip().lower() == branch_name.lower():
            raise HTTPException(status_code=409, detail=f"A branch named '{branch_name}' already exists")

    new_branch_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    new_branch = {
        "id": new_branch_id,
        "name": branch_name,
        "location": body.location or branch_name,
        "phone": body.phone,
        "notes": body.notes,
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    stored_branches.append(new_branch)
    agency_settings["branches"] = stored_branches

    # Save to agencies settings jsonb
    sb.table("agencies").update({"settings": agency_settings}).eq("id", agency_id).execute()

    res_data = dict(new_branch)
    res_data["agent_count"] = 0

    return api_success(data=res_data, message="Branch created successfully", status_code=201)


@router.get("/{branch_id}")
async def get_branch(branch_id: str, current_user: dict = Depends(verify_token)):
    """Get single branch details with assigned agents."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    agency_settings, stored_branches, agent_rows = _get_agency_branches_dict(sb, agency_id)

    target = None
    for b in stored_branches:
        if b.get("id") == branch_id or b.get("name", "").lower() == branch_id.lower():
            target = dict(b)
            break

    if not target:
        # Check if matching agent branch name
        for a in agent_rows:
            if (a.get("branch") or "").lower() == branch_id.lower() or str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{agency_id}-{a.get('branch')}")) == branch_id:
                target = {
                    "id": branch_id,
                    "name": a.get("branch"),
                    "location": a.get("branch"),
                    "phone": None,
                    "notes": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                break

    if not target:
        raise HTTPException(status_code=404, detail="Branch not found")

    # Get assigned agents
    assigned_agents = [
        {"id": a["id"], "name": a["name"], "email": a.get("email")}
        for a in agent_rows
        if (a.get("branch") or "").strip().lower() == target["name"].strip().lower()
    ]
    target["agent_count"] = len(assigned_agents)
    target["agents"] = assigned_agents

    return api_success(data=target, message="Branch retrieved successfully")


@router.patch("/{branch_id}")
async def update_branch(branch_id: str, body: BranchUpdate, current_user: dict = Depends(verify_token)):
    """Update branch details."""
    if not is_management_role(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Only owners and managers can update branches")

    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    agency_settings, stored_branches, agent_rows = _get_agency_branches_dict(sb, agency_id)

    target_idx = None
    for idx, b in enumerate(stored_branches):
        if b.get("id") == branch_id:
            target_idx = idx
            break

    if target_idx is None:
        raise HTTPException(status_code=404, detail="Branch not found")

    old_name = stored_branches[target_idx].get("name")
    if body.name is not None:
        new_name = body.name.strip()
        # Check duplicate
        for idx, b in enumerate(stored_branches):
            if idx != target_idx and b.get("name", "").strip().lower() == new_name.lower():
                raise HTTPException(status_code=409, detail=f"Branch name '{new_name}' is already in use")
        stored_branches[target_idx]["name"] = new_name
        # Update agents assigned to old name
        if old_name != new_name:
            sb.table("agents").update({"branch": new_name}).eq("agency_id", agency_id).eq("branch", old_name).execute()

    if body.location is not None:
        stored_branches[target_idx]["location"] = body.location
    if body.phone is not None:
        stored_branches[target_idx]["phone"] = body.phone
    if body.notes is not None:
        stored_branches[target_idx]["notes"] = body.notes

    stored_branches[target_idx]["updated_at"] = datetime.now(timezone.utc).isoformat()
    agency_settings["branches"] = stored_branches

    sb.table("agencies").update({"settings": agency_settings}).eq("id", agency_id).execute()

    return api_success(data=stored_branches[target_idx], message="Branch updated successfully")


@router.delete("/{branch_id}")
async def delete_branch(branch_id: str, current_user: dict = Depends(verify_token)):
    """Delete a branch."""
    if not is_management_role(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Only owners and managers can delete branches")

    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    agency_settings, stored_branches, agent_rows = _get_agency_branches_dict(sb, agency_id)

    target = None
    new_branches = []
    for b in stored_branches:
        if b.get("id") == branch_id:
            target = b
        else:
            new_branches.append(b)

    if not target:
        raise HTTPException(status_code=404, detail="Branch not found")

    agency_settings["branches"] = new_branches
    sb.table("agencies").update({"settings": agency_settings}).eq("id", agency_id).execute()

    return api_success(data={"deleted_branch_id": branch_id, "name": target.get("name")}, message="Branch deleted successfully")
