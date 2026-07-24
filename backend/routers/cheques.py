from fastapi import APIRouter, HTTPException, Depends
from models.cheque import ChequeCreate, ChequeResponse
from database import supabase_client
from middleware.auth_middleware import verify_token
from services.contract_service import log_cheque

router = APIRouter(prefix="/cheques", tags=["Cheques"])

@router.post("/", response_model=ChequeResponse)
async def create_cheque(cheque: ChequeCreate, token: dict = Depends(verify_token)):
    """Logs a new cheque for a contract."""
    result = await log_cheque(str(cheque.contract_id), cheque.model_dump())
    if not result:
        raise HTTPException(status_code=500, detail="Failed to log cheque")
    return result

@router.get("/", response_model=list[ChequeResponse])
def list_cheques(status: str = None, token: dict = Depends(verify_token)):
    """List all cheques, optionally filtering by status (e.g. pending, bounced)."""
    sb = supabase_client.get_supabase()
    query = sb.table("cheques").select("*, contracts(*)")
    if status:
        query = query.eq("status", status)
    result = query.order("due_date").execute()
    return result.data
