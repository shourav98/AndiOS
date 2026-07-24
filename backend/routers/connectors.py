"""
Connectors Router — Integrations / Connectors page
GET  /connectors                            — list all connectors and status
GET  /connectors/google-calendar/auth       — start Google OAuth flow
GET  /connectors/google-calendar/callback   — handle OAuth callback
POST /connectors/google-calendar/disconnect — disconnect Google Calendar
POST /connectors/whatsapp/test              — send a test WhatsApp message
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from database.supabase_client import get_supabase
from services.calendar_service import get_auth_url, exchange_code_for_tokens
from services.whatsapp_service import send_whatsapp_message
from middleware.auth_middleware import verify_token
from config import settings
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/connectors", tags=["Connectors"])


@router.get("")
async def list_connectors(_: dict = Depends(verify_token)):
    """List all connectors with their connection status."""
    sb = get_supabase()
    result = sb.table("connectors").select("id, name, is_connected, last_sync, updated_at").execute()

    connectors = []
    for c in result.data:
        connectors.append({
            "id": c["id"],
            "name": c["name"],
            "is_connected": c["is_connected"],
            "last_sync": c.get("last_sync"),
            "updated_at": c["updated_at"],
            "display_name": {
                "property_finder": "Property Finder",
                "whatsapp": "WhatsApp Business",
                "google_calendar": "Google Calendar",
                "bayut": "Bayut",
                "dubizzle": "Dubizzle",
            }.get(c["name"], c["name"]),
        })
    return connectors


# ─── Google Calendar OAuth ─────────────────────────────────────────────────────

@router.get("/google-calendar/auth")
async def google_calendar_auth(_: dict = Depends(verify_token)):
    """Initiate Google Calendar OAuth2 flow. Returns the auth URL."""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=400, detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID in .env")
    auth_url = get_auth_url()
    return {"auth_url": auth_url}


@router.get("/google-calendar/callback")
async def google_calendar_callback(code: str = Query(...), state: str = Query(None)):
    """
    Google OAuth2 callback. Exchanges code for tokens and stores in Supabase.
    Redirects to frontend after success.
    """
    try:
        tokens = exchange_code_for_tokens(code)
        sb = get_supabase()

        # Store tokens (in production encrypt these!)
        sb.table("connectors").update({
            "is_connected": True,
            "auth_data": tokens,
            "last_sync": "now()",
        }).eq("name", "google_calendar").execute()

        logger.info("Google Calendar connected successfully")
        return RedirectResponse(url=f"{settings.FRONTEND_URL}/connectors?connected=google_calendar")
    except Exception as e:
        logger.error(f"Google Calendar OAuth error: {e}")
        return RedirectResponse(url=f"{settings.FRONTEND_URL}/connectors?error=google_calendar")


@router.post("/google-calendar/disconnect")
async def google_calendar_disconnect(_: dict = Depends(verify_token)):
    """Disconnect Google Calendar integration."""
    sb = get_supabase()
    sb.table("connectors").update({
        "is_connected": False,
        "auth_data": None,
    }).eq("name", "google_calendar").execute()
    return {"status": "disconnected"}


@router.post("/google-calendar/test")
async def test_google_calendar(_: dict = Depends(verify_token)):
    """Test Google Calendar connection by listing upcoming events."""
    sb = get_supabase()
    connector = (
        sb.table("connectors")
        .select("auth_data")
        .eq("name", "google_calendar")
        .eq("is_connected", True)
        .single()
        .execute()
    )
    if not connector.data or not connector.data.get("auth_data"):
        raise HTTPException(status_code=400, detail="Google Calendar not connected")

    try:
        from services.calendar_service import _build_service
        service = _build_service(connector.data["auth_data"])
        calendar_id = settings.GOOGLE_SHARED_CALENDAR_ID or "primary"
        cal = service.calendars().get(calendarId=calendar_id).execute()
        return {"status": "connected", "calendar_name": cal.get("summary"), "calendar_id": calendar_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Calendar test failed: {str(e)}")


# ─── WhatsApp ─────────────────────────────────────────────────────────────────

@router.post("/whatsapp/test")
async def test_whatsapp(to_phone: str = Query(...), _: dict = Depends(verify_token)):
    """Send a test WhatsApp message to verify the connection."""
    result = await send_whatsapp_message(
        to_phone,
        "✅ AndiOS WhatsApp integration is working! This is a test message from your AI assistant."
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=f"WhatsApp test failed: {result.get('error')}")

    # Mark WhatsApp as connected
    sb = get_supabase()
    sb.table("connectors").update({"is_connected": True, "last_sync": "now()"}).eq("name", "whatsapp").execute()

    return {"status": "sent", "provider": settings.WHATSAPP_PROVIDER}


@router.post("/property-finder/test")
async def test_property_finder_webhook(_: dict = Depends(verify_token)):
    """Send a test Property Finder webhook payload to verify the pipeline."""
    import httpx
    test_payload = {
        "lead": {
            "id": "TEST-001",
            "name": "Test Lead",
            "phone": "+971501234567",
            "email": "test@example.com",
            "property_ref": "PF-TEST-001",
            "property_title": "2BR Apartment — Dubai Marina",
            "bedrooms": 2,
            "budget": 120000,
            "community": "Dubai Marina",
        }
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{settings.API_BASE_URL}/webhooks/property-finder", json=test_payload)
    return {"status": "test_sent", "response": resp.json()}
