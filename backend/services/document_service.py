import logging
import json
import base64
import httpx
from database import supabase_client
from services.ai_service import client
from config import settings

logger = logging.getLogger(__name__)


OCR_PROMPTS = {
    "emirates_id": """Extract the following fields from this Emirates ID card image:
- full_name (as printed on the card)
- id_number (the Emirates ID number, format: 784-XXXX-XXXXXXX-X)
- nationality
- date_of_birth (YYYY-MM-DD)
- expiry_date (YYYY-MM-DD)
- gender
Return ONLY valid JSON with these fields. Use null for any field you cannot read.""",

    "passport": """Extract the following fields from this passport image:
- full_name (as in the MRZ or photo page)
- document_number (passport number)
- nationality
- date_of_birth (YYYY-MM-DD)
- expiry_date (YYYY-MM-DD)
- gender
- issuing_country
Return ONLY valid JSON with these fields. Use null for any field you cannot read.""",

    "title_deed": """Extract the following fields from this Dubai Title Deed document:
- owner_name
- property_number (plot/unit number)
- area_name (community/area)
- building_name
- property_type (apartment, villa, etc.)
- title_deed_number
Return ONLY valid JSON with these fields. Use null for any field you cannot read.""",

    "visa": """Extract the following fields from this UAE residence visa:
- full_name
- visa_number
- uid_number
- nationality
- expiry_date (YYYY-MM-DD)
- sponsor
Return ONLY valid JSON with these fields. Use null for any field you cannot read.""",
}


async def _fetch_image_as_base64(file_url: str) -> str:
    """Download image from Supabase Storage URL and return base64-encoded string."""
    async with httpx.AsyncClient(timeout=30) as http_client:
        resp = await http_client.get(file_url)
        resp.raise_for_status()
        return base64.b64encode(resp.content).decode("utf-8")


async def extract_document_data(document_id: str) -> dict:
    """
    Extracts structured data from uploaded document images using OpenAI GPT-4o Vision.
    Supports: emirates_id, passport, title_deed, visa.
    """
    sb = supabase_client.get_supabase()

    doc_result = sb.table("documents").select("*").eq("id", document_id).execute()
    if not doc_result.data:
        raise ValueError("Document not found")

    document = doc_result.data[0]
    file_url = document.get("file_url", "")
    doc_type = document.get("document_type", "emirates_id")

    if not file_url:
        raise ValueError("Document has no file URL")

    logger.info(f"OCR extraction for document {document_id} (type={doc_type})")

    # Get the appropriate prompt
    system_prompt = OCR_PROMPTS.get(doc_type, OCR_PROMPTS["emirates_id"])

    try:
        # Fetch image and encode
        image_b64 = await _fetch_image_as_base64(file_url)

        # Determine content type from URL
        content_type = "image/jpeg"
        if file_url.lower().endswith(".png"):
            content_type = "image/png"
        elif file_url.lower().endswith(".webp"):
            content_type = "image/webp"

        # Call OpenAI Vision
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{content_type};base64,{image_b64}",
                                "detail": "high",
                            },
                        },
                        {
                            "type": "text",
                            "text": "Please extract the data from this document image.",
                        },
                    ],
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=500,
            temperature=0.1,
        )

        extracted_data = json.loads(response.choices[0].message.content)

    except httpx.HTTPError as fetch_err:
        logger.warning(f"Could not fetch image from {file_url}: {fetch_err}. Falling back to URL-based OCR.")
        # Fallback: send URL directly (works if URL is publicly accessible)
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": file_url}},
                        {"type": "text", "text": "Please extract the data from this document image."},
                    ],
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=500,
            temperature=0.1,
        )
        extracted_data = json.loads(response.choices[0].message.content)

    # Update document in DB
    sb.table("documents").update({
        "extracted_data": extracted_data,
        "status": "extracted",
    }).eq("id", document_id).execute()

    logger.info(f"OCR completed for document {document_id}: {list(extracted_data.keys())}")
    return extracted_data
