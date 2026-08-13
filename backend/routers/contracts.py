from fastapi import APIRouter, HTTPException, Depends, Query
from models.contract import ContractCreate, ContractResponse
from database import supabase_client
from middleware.auth_middleware import verify_token
from services.contract_service import (
    generate_tenancy_agreement,
    send_contract_for_esign,
    verify_and_record_signature,
    close_contract_with_cheque,
)
from utils.response import api_success, ApiResponse
from utils.tenant import require_agency_id, apply_agency_scope
from pydantic import BaseModel
from typing import Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/contracts", tags=["Contracts"])


class SignRequest(BaseModel):
    token: str
    role: str  # 'landlord' or 'tenant'


class CloseContractRequest(BaseModel):
    cheque_image_url: str


@router.post("/", response_model=ApiResponse[ContractResponse])
def create_contract(contract: ContractCreate, current_user: dict = Depends(verify_token)):
    """Creates a new draft contract."""
    sb = supabase_client.get_supabase()
    agency_id = require_agency_id(current_user)

    contract_data = contract.model_dump(exclude_none=True)
    contract_data["agency_id"] = agency_id
    contract_data["created_by"] = current_user.get("agent_id")

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
            "ref": f"TC-{str(row['id'])[-4:].upper()}",
            "documentUrl": row.get("document_url"),
            "landlordSignedAt": row.get("landlord_signed_at"),
            "tenantSignedAt": row.get("tenant_signed_at"),
        })

    return api_success(data=formatted_contracts, message="Contracts retrieved successfully")


@router.post("/{contract_id}/generate")
async def generate_contract_pdf(contract_id: str, current_user: dict = Depends(verify_token)):
    """Generates the physical PDF for a contract and uploads to Supabase Storage."""
    require_agency_id(current_user)
    try:
        url = await generate_tenancy_agreement(contract_id)
        return api_success(data={"url": url}, message="Contract PDF generated and uploaded successfully")
    except Exception as e:
        logger.error(f"PDF generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{contract_id}")
def get_contract(contract_id: str, current_user: dict = Depends(verify_token)):
    """Get details of a single contract."""
    sb = supabase_client.get_supabase()
    require_agency_id(current_user)

    query = sb.table("contracts").select("*").eq("id", contract_id)
    result = apply_agency_scope(query, current_user).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Contract not found")

    return api_success(data=result.data, message="Contract retrieved successfully")


@router.post("/{contract_id}/send-esign")
async def send_contract_esign(contract_id: str, current_user: dict = Depends(verify_token)):
    """Generate PDF (if not already) and send e-signature links to landlord and tenant via WhatsApp."""
    require_agency_id(current_user)
    sb = supabase_client.get_supabase()

    query = sb.table("contracts").select("status, document_url").eq("id", contract_id)
    result = apply_agency_scope(query, current_user).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Contract not found")

    # Generate PDF first if not yet generated
    if not result.data.get("document_url"):
        await generate_tenancy_agreement(contract_id)

    try:
        sign_data = await send_contract_for_esign(contract_id)
        return api_success(data=sign_data, message="Contract sent for e-signature via WhatsApp")
    except Exception as e:
        logger.error(f"E-sign send failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{contract_id}/sign")
async def sign_contract(contract_id: str, body: SignRequest):
    """
    Public endpoint — landlord or tenant signs the contract using their unique token.
    No auth required (token-based verification).
    """
    try:
        result = await verify_and_record_signature(contract_id, body.token, body.role)
        return api_success(data=result, message=f"Contract signed by {body.role} successfully")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Signing failed: {e}")
        raise HTTPException(status_code=500, detail="Signing failed")


@router.post("/{contract_id}/close")
async def close_contract(contract_id: str, body: CloseContractRequest, current_user: dict = Depends(verify_token)):
    """Close a signed contract by uploading the 5% agency fee cheque."""
    require_agency_id(current_user)
    try:
        result = await close_contract_with_cheque(contract_id, body.cheque_image_url)
        return api_success(data=result, message="Contract closed successfully — deal won!")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Contract close failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to close contract")
