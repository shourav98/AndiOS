import logging
from database import supabase_client
from typing import Any

logger = logging.getLogger(__name__)

import os
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

async def _generate_pdf(contract: dict, lead: dict, filepath: str):
    """Internal function to generate the DLD standard PDF using ReportLab."""
    c = canvas.Canvas(filepath, pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(100, 800, "DUBAI UNIFIED TENANCY CONTRACT")
    c.setFont("Helvetica", 12)
    c.drawString(100, 750, f"Contract ID: {contract['id']}")
    c.drawString(100, 730, f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    
    c.drawString(100, 680, "1. LANDLORD DETAILS")
    c.drawString(120, 660, f"Name: {contract.get('landlord_name', 'TBD')}")
    
    c.drawString(100, 610, "2. TENANT DETAILS")
    c.drawString(120, 590, f"Name: {lead.get('name', 'Unknown')}")
    c.drawString(120, 570, f"Phone: {lead.get('phone', 'Unknown')}")
    c.drawString(120, 550, f"Email: {lead.get('email', 'Unknown')}")
    
    c.drawString(100, 500, "3. PROPERTY DETAILS")
    c.drawString(120, 480, f"Address: {contract.get('property_address', 'TBD')}")
    
    c.drawString(100, 430, "4. CONTRACT TERMS")
    c.drawString(120, 410, f"Rent Amount: AED {contract.get('rent_amount', 0)}")
    c.drawString(120, 390, f"Number of Cheques: {contract.get('cheques_count', 1)}")
    
    c.save()

async def send_docusign_envelope(contract_id: str, tenant_email: str, landlord_email: str):
    """Internal function to trigger DocuSign signature request."""
    # In a real app, this would use the DocuSign REST API with the generated PDF.
    logger.info(f"Sending DocuSign envelope for contract {contract_id} to tenant {tenant_email} and landlord {landlord_email}")
    return "sent"

async def generate_tenancy_agreement(contract_id: str) -> str:
    """
    Generates a PDF tenancy agreement based on the lead, property, and extracted documents.
    Returns a URL to the generated PDF.
    """
    try:
        sb = supabase_client.get_supabase()
        
        # Fetch Contract
        contract_result = sb.table("contracts").select("*, leads(*)").eq("id", contract_id).execute()
        if not contract_result.data:
            raise ValueError("Contract not found")
            
        contract = contract_result.data[0]
        lead = contract.get("leads", {})
        
        # Generate PDF using ReportLab
        filename = f"contract_{contract_id}.pdf"
        filepath = os.path.join(os.getcwd(), filename) # Saving locally for now
        await _generate_pdf(contract, lead, filepath)
        logger.info(f"Generated PDF tenancy agreement for {lead.get('name')} at {contract.get('property_address')}")
        
        # In a real app we'd upload the PDF to Supabase Storage:
        # with open(filepath, 'rb') as f:
        #     sb.storage.from_("contracts").upload(filename, f)
        # pdf_url = sb.storage.from_("contracts").get_public_url(filename)
        
        mock_pdf_url = f"https://storage.andios.com/contracts/{filename}"
        
        # Trigger E-Signature (DocuSign/HelloSign)
        await send_docusign_envelope(
            contract_id,
            tenant_email=lead.get("email", "tenant@example.com"),
            landlord_email="landlord@example.com"
        )
        
        # Update Contract
        sb.table("contracts").update({
            "status": "sent",
            "document_url": mock_pdf_url
        }).eq("id", contract_id).execute()
        
        return mock_pdf_url
        
    except Exception as e:
        logger.error(f"Failed to generate tenancy agreement: {e}")
        raise e

async def log_cheque(contract_id: str, cheque_data: dict[str, Any]) -> dict:
    """Logs a new post-dated cheque for a contract."""
    sb = supabase_client.get_supabase()
    cheque_data["contract_id"] = contract_id
    result = sb.table("cheques").insert(cheque_data).execute()
    return result.data[0] if result.data else {}

async def perform_ocr_on_document(document_id: str) -> dict:
    """
    Uses an OCR service (e.g. Google Cloud Vision or AWS Textract) to extract ID/Passport or Title Deed info.
    """
    sb = supabase_client.get_supabase()
    doc_result = sb.table("documents").select("*").eq("id", document_id).execute()
    if not doc_result.data:
        raise ValueError("Document not found")
    
    doc = doc_result.data[0]
    logger.info(f"Performing OCR on document {document_id} of type {doc.get('document_type')}")
    
    # Mock OCR extraction
    extracted_data = {}
    if doc.get("document_type") in ["passport", "emirates_id"]:
        extracted_data = {"name": "MOCK TENANT NAME", "id_number": "784-1234-5678901-1"}
    elif doc.get("document_type") == "title_deed":
        extracted_data = {"owner_name": "MOCK LANDLORD NAME", "property_number": "12345"}
    
    # Update document with extracted data
    sb.table("documents").update({
        "status": "extracted",
        "extracted_data": extracted_data
    }).eq("id", document_id).execute()
    
    return extracted_data
