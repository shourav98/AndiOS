import logging
from database import supabase_client
from typing import Any

logger = logging.getLogger(__name__)

async def generate_tenancy_agreement(contract_id: str) -> str:
    """
    Generates a PDF tenancy agreement based on the lead, property, and extracted documents.
    Returns a mock URL to the generated PDF.
    """
    try:
        sb = supabase_client.get_supabase()
        
        # Fetch Contract
        contract_result = sb.table("contracts").select("*, leads(*)").eq("id", contract_id).execute()
        if not contract_result.data:
            raise ValueError("Contract not found")
            
        contract = contract_result.data[0]
        lead_id = contract["lead_id"]
        
        # Fetch Extracted Documents for the lead
        docs_result = sb.table("documents").select("*").eq("lead_id", lead_id).eq("status", "extracted").execute()
        
        # In a real implementation, we would map the extracted data (name, passport number)
        # onto a PDF template using WeasyPrint or ReportLab.
        
        logger.info(f"Generating tenancy agreement for {contract['leads']['name']} at {contract['property_address']}")
        
        # Mock URL generation
        mock_pdf_url = f"https://storage.andios.com/contracts/{contract_id}.pdf"
        
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
