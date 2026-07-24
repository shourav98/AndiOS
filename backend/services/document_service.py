import logging
import json
from database import supabase_client
from services.ai_service import client
from config import settings

logger = logging.getLogger(__name__)

async def extract_document_data(document_id: str) -> dict:
    """
    Extracts structured data from a passport or Emirates ID using OpenAI Vision.
    (Note: In a real environment, we'd fetch the image bytes from Supabase storage using the file_url.
    For this mockup, we'll simulate the vision extraction process).
    """
    try:
        sb = supabase_client.get_supabase()
        
        # 1. Fetch document record
        doc_result = sb.table("documents").select("*").eq("id", document_id).execute()
        if not doc_result.data:
            raise ValueError("Document not found")
            
        document = doc_result.data[0]
        file_url = document["file_url"]
        doc_type = document["document_type"]
        
        logger.info(f"Extracting data for document {document_id} of type {doc_type}")

        # 2. Ask OpenAI Vision to extract data
        # In a real implementation, we would pass the base64 encoded image here.
        system_prompt = f"""
        You are a Document Extraction AI. 
        Extract the following fields from this {doc_type}:
        - full_name
        - document_number
        - expiry_date (YYYY-MM-DD)
        - nationality
        
        Return ONLY valid JSON.
        """
        
        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Simulated image data (url: " + file_url + ")"}
            ],
            response_format={ "type": "json_object" }
        )
        
        extracted_data_raw = response.choices[0].message.content
        extracted_data = json.loads(extracted_data_raw)
        
        # 3. Update the database
        sb.table("documents").update({
            "extracted_data": extracted_data,
            "status": "extracted"
        }).eq("id", document_id).execute()
        
        return extracted_data
        
    except Exception as e:
        logger.error(f"Failed to extract document data: {e}")
        sb = supabase_client.get_supabase()
        sb.table("documents").update({
            "status": "failed",
            "error_message": str(e)
        }).eq("id", document_id).execute()
        raise e
