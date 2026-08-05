from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from models.contract import ContractCreate, ContractResponse
from database import supabase_client
from middleware.auth_middleware import verify_token
from services.contract_service import generate_tenancy_agreement
from utils.response import api_success, ApiResponse

router = APIRouter(prefix="/contracts", tags=["Contracts"])

@router.post("/", response_model=ApiResponse[ContractResponse])
def create_contract(contract: ContractCreate, token: dict = Depends(verify_token)):
    """Creates a new draft contract."""
    sb = supabase_client.get_supabase()
    result = sb.table("contracts").insert(contract.model_dump()).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create contract")
    return api_success(data=result.data[0], message="Contract created successfully")

@router.get("/", response_model=ApiResponse[list[ContractResponse]])
def list_contracts(token: dict = Depends(verify_token)):
    """List all contracts."""
    sb = supabase_client.get_supabase()
    result = sb.table("contracts").select("*").order("created_at", desc=True).execute()
    return api_success(data=result.data, message="Contracts retrieved successfully")

@router.post("/{contract_id}/generate")
async def generate_contract_pdf(contract_id: str, token: dict = Depends(verify_token)):
    """Generates the physical PDF for a contract."""
    try:
        url = await generate_tenancy_agreement(contract_id)
        return api_success(data={"url": url}, message="Contract PDF generated successfully")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
