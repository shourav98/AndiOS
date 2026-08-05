import os
import httpx
import logging
from datetime import datetime
import pytz
from config import settings

logger = logging.getLogger(__name__)

VAPI_API_KEY = os.getenv("VAPI_API_KEY", "mock_vapi_key")
VAPI_BASE_URL = "https://api.vapi.ai"

def is_within_calling_hours(timezone_str: str = "Asia/Dubai") -> bool:
    """Check if current time is within allowed calling hours (9 AM to 6 PM)."""
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    # Check if Sunday (in Dubai Sunday is weekend, but typical work week is Mon-Fri or Sun-Thu depending on the company. Let's assume Mon-Sat)
    # Let's just enforce 9 AM to 6 PM for now.
    if 9 <= now.hour < 18:
        return True
    return False

async def trigger_outbound_call(phone_number: str, owner_name: str, assistant_id: str):
    """
    Triggers an outbound call using Vapi.ai.
    Ensures the call is only made during allowed hours and configures voicemail detection.
    """
    if not is_within_calling_hours():
        logger.warning(f"Attempted to call {phone_number} outside of calling hours.")
        return {"status": "error", "message": "Outside of calling hours"}

    headers = {
        "Authorization": f"Bearer {VAPI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "assistantId": assistant_id,
        "customer": {
            "number": phone_number,
            "name": owner_name
        },
        "phoneNumberId": os.getenv("VAPI_PHONE_NUMBER_ID", "mock_phone_id"),
        "voicemailDetection": {
            "provider": "twilio", 
            "voicemailDetectionTypes": ["machine_end_beep", "machine_end_other"]
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{VAPI_BASE_URL}/call",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            logger.info(f"Triggered Vapi call to {phone_number} for {owner_name}")
            return response.json()
    except Exception as e:
        logger.error(f"Failed to trigger Vapi call to {phone_number}: {e}")
        return {"status": "error", "message": str(e)}
