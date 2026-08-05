from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from models.contract import ContractCreate, ContractResponse
from database import supabase_client
from middleware.auth_middleware import verify_token
from services.contract_service import generate_tenancy_agreement
from utils.response import api_success, ApiResponse
from utils.tenant import require_agency_id, apply_agency_scope

router = APIRouter(prefix="/contracts", tags=["Contracts"])

@router.post("/", response_model=ApiResponse[ContractResponse])
def create_contract(contract: ContractCreate, current_user: dict = Depends(verify_token)):
    """Creates a new draft contract."""
    sb = supabase_client.get_supabase()
    agency_id = require_agency_id(current_user)

    contract_data = contract.model_dump(exclude_none=True)
    contract_data["agency_id"] = agency_id
    
    result = sb.table("contracts").insert(contract_data).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create contract")
    return api_success(data=result.data[0], message="Contract created successfully")

@router.get("/")
def list_contracts(current_user: dict = Depends(verify_token)):
    """List all contracts for the current agency."""
    sb = supabase_client.get_supabase()
    require_agency_id(current_user)

    query = sb.table("contracts").select("*").order("created_at", desc=True)
    result = apply_agency_scope(query, current_user).execute()
    
    formatted_contracts = []
    for row in result.data:
        formatted_contracts.append({
            "id": row["id"],
            "propertyUnit": row.get("property_unit", row.get("property_address")),
            "areaCommunity": row.get("area_community"),
            "tenantName": row.get("tenant_name"),
            "ownerName": row.get("owner_name"),
            "startDate": row.get("start_date"),
            "endDate": row.get("end_date"),
            "annualRent": row.get("rent_amount"),
            "status": row.get("status", "Draft"),
            "ref": f"TC-{str(row['id'])[-4:].upper()}"
        })
        
    return api_success(data=formatted_contracts, message="Contracts retrieved successfully")

@router.post("/{contract_id}/generate")
async def generate_contract_pdf(contract_id: str, token: dict = Depends(verify_token)):
    """Generates the physical PDF for a contract."""
    try:
        url = await generate_tenancy_agreement(contract_id)
        return api_success(data={"url": url}, message="Contract PDF generated successfully")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
