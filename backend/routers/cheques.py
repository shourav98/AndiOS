from fastapi import APIRouter, HTTPException, Depends
from models.cheque import ChequeCreate, ChequeResponse
from database import supabase_client
from middleware.auth_middleware import verify_token
from services.contract_service import log_cheque
from utils.response import api_success, ApiResponse
from utils.tenant import require_agency_id, apply_agency_scope

router = APIRouter(prefix="/cheques", tags=["Cheques"])


@router.post("/", response_model=ApiResponse[ChequeResponse])
async def create_cheque(cheque: ChequeCreate, current_user: dict = Depends(verify_token)):
    """Logs a new cheque for a contract."""
    agency_id = require_agency_id(current_user)
    sb = supabase_client.get_supabase()

    # Verify contract belongs to this agency
    contract = (
        sb.table("contracts")
        .select("id")
        .eq("id", str(cheque.contract_id))
        .eq("agency_id", agency_id)
        .single()
        .execute()
    )
    if not contract.data:
        raise HTTPException(status_code=404, detail="Contract not found")

    cheque_data = cheque.model_dump()
    cheque_data["agency_id"] = agency_id
    result = await log_cheque(str(cheque.contract_id), cheque_data)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to log cheque")
    return api_success(data=result, message="Cheque created successfully")


@router.get("/", response_model=ApiResponse[list[ChequeResponse]])
def list_cheques(status: str = None, current_user: dict = Depends(verify_token)):
    """List all cheques for the current agency, optionally filtering by status."""
    sb = supabase_client.get_supabase()
    query = sb.table("cheques").select("*, contracts(*)")
    query = apply_agency_scope(query, current_user)
    if status:
        query = query.eq("status", status)
    result = query.order("due_date").execute()
    return api_success(data=result.data, message="Cheques retrieved successfully")
