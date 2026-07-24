from fastapi import APIRouter, HTTPException, Depends
from models.document import DocumentCreate, DocumentResponse
from database import supabase_client
from middleware.auth_middleware import verify_token
from services.document_service import extract_document_data

router = APIRouter(prefix="/documents", tags=["Documents"])

@router.post("/", response_model=DocumentResponse)
async def create_document(doc: DocumentCreate, token: dict = Depends(verify_token)):
    """Creates a new document record and immediately triggers AI extraction."""
    sb = supabase_client.get_supabase()
    
    # Insert document
    result = sb.table("documents").insert(doc.model_dump()).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create document")
        
    doc_data = result.data[0]
    
    # Trigger extraction async or sync. Here we do it inline for simplicity.
    # In production, use background tasks: background_tasks.add_task(extract_document_data, doc_data['id'])
    extracted = await extract_document_data(doc_data["id"])
    doc_data["extracted_data"] = extracted
    doc_data["status"] = "extracted"
    
    return doc_data

@router.get("/lead/{lead_id}", response_model=list[DocumentResponse])
def get_documents_by_lead(lead_id: str, token: dict = Depends(verify_token)):
    """Get all documents associated with a lead."""
    sb = supabase_client.get_supabase()
    result = sb.table("documents").select("*").eq("lead_id", lead_id).execute()
    return result.data
