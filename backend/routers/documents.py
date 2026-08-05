from fastapi import APIRouter, HTTPException, Depends
from models.document import DocumentCreate, DocumentResponse
from database import supabase_client
from middleware.auth_middleware import verify_token
from services.document_service import extract_document_data
from utils.response import api_success, ApiResponse
from utils.tenant import require_agency_id, verify_lead_access

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.post("/", response_model=ApiResponse[DocumentResponse])
async def create_document(doc: DocumentCreate, current_user: dict = Depends(verify_token)):
    """Creates a new document record and immediately triggers AI extraction."""
    sb = supabase_client.get_supabase()
    agency_id = require_agency_id(current_user)
    await verify_lead_access(str(doc.lead_id), current_user)

    doc_data = doc.model_dump()
    doc_data["agency_id"] = agency_id
    result = sb.table("documents").insert(doc_data).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create document")

    doc_data = result.data[0]

    extracted = await extract_document_data(doc_data["id"])
    doc_data["extracted_data"] = extracted
    doc_data["status"] = "extracted"

    return api_success(data=doc_data, message="Document created successfully")


@router.get("/lead/{lead_id}", response_model=ApiResponse[list[DocumentResponse]])
async def get_documents_by_lead(lead_id: str, current_user: dict = Depends(verify_token)):
    """Get all documents associated with a lead."""
    await verify_lead_access(lead_id, current_user)
    sb = supabase_client.get_supabase()
    result = sb.table("documents").select("*").eq("lead_id", lead_id).execute()
    return api_success(data=result.data, message="Documents retrieved successfully")
