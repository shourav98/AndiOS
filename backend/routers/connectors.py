"""
Connectors Router — Integrations / Connectors page
GET  /connectors                            — list all connectors and status
POST /connectors/{name}/connect             — save API credentials (Step 1)
GET  /connectors/{name}/listings            — fetch listings for import (Step 2)
POST /connectors/{name}/activate            — finish & activate connector (Step 3)
GET  /connectors/{name}/webhook-url         — get webhook URL for connector
POST /connectors/{name}/disconnect          — disconnect any connector
GET  /connectors/google-calendar/auth       — start Google OAuth flow
GET  /connectors/google-calendar/callback   — handle OAuth callback
POST /connectors/google-calendar/disconnect — disconnect Google Calendar
POST /connectors/whatsapp/test              — send a test WhatsApp message
POST /connectors/property-finder/test       — send a test PF webhook
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional, Any
from database.supabase_client import get_supabase
from services.calendar_service import get_auth_url, exchange_code_for_tokens
from services.whatsapp_service import send_whatsapp_message
from middleware.auth_middleware import verify_token
from utils.response import api_success
from utils.tenant import require_agency_id, apply_agency_scope
from config import settings
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/connectors", tags=["Connectors"])


# ─── Supported connectors config ───────────────────────────────────────────────

CONNECTOR_NAMES = {"property_finder", "bayut", "dubizzle", "whatsapp", "google_calendar"}

CONNECTOR_DISPLAY = {
    "property_finder": "Property Finder",
    "whatsapp": "WhatsApp Business",
    "google_calendar": "Google Calendar",
    "bayut": "Bayut",
    "dubizzle": "Dubizzle",
}

WEBHOOK_URLS = {
    "property_finder": f"{settings.API_BASE_URL}/webhooks/property-finder",
    "bayut": f"{settings.API_BASE_URL}/webhooks/bayut",
    "dubizzle": f"{settings.API_BASE_URL}/webhooks/dubizzle",
    "whatsapp": f"{settings.API_BASE_URL}/webhooks/whatsapp",
}

# Mock listing data per provider (step 2 of wizard)
MOCK_LISTINGS = {
    "property_finder": [
        {"id": "PF-4821", "title": "2BR Apartment", "location": "Dubai Marina", "price": "AED 2.4M"},
        {"id": "PF-4822", "title": "Studio Apartment", "location": "JVC", "price": "AED 55k/yr"},
        {"id": "PF-4823", "title": "3BR Apartment", "location": "Downtown Dubai", "price": "AED 4.1M"},
    ],
    "bayut": [
        {"id": "BYT-2001", "title": "Modern Apartment", "location": "Dubai, UAE", "price": "AED 2.5M"},
        {"id": "BYT-2002", "title": "Luxury Villa", "location": "Abu Dhabi, UAE", "price": "AED 6.8M"},
        {"id": "BYT-2003", "title": "Cozy Studio", "location": "Sharjah, UAE", "price": "AED 1.2M"},
    ],
    "dubizzle": [
        {"id": "DBZ-7001", "title": "Villa", "location": "Arabian Ranches", "price": "AED 6.8M"},
        {"id": "DBZ-7002", "title": "Townhouse", "location": "Dubai Hills Estate", "price": "AED 3.9M"},
        {"id": "DBZ-7003", "title": "Studio Apartment", "location": "Business Bay", "price": "AED 65k/yr"},
    ],
}


# ─── Request models ─────────────────────────────────────────────────────────────

class ConnectorCredentials(BaseModel):
    credentials: dict[str, Any]


# ─── List all connectors ────────────────────────────────────────────────────────

@router.get("")
async def list_connectors(current_user: dict = Depends(verify_token)):
    """List all connectors with their connection status for the current agency."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)
    query = sb.table("connectors").select("id, name, is_connected, last_sync, updated_at")
    result = apply_agency_scope(query, current_user).execute()

    db_map = {c["name"]: c for c in result.data}

    connectors = []
    for name, display in CONNECTOR_DISPLAY.items():
        db_entry = db_map.get(name)
        connectors.append({
            "id": db_entry["id"] if db_entry else None,
            "name": name,
            "display_name": display,
            "is_connected": db_entry["is_connected"] if db_entry else False,
            "last_sync": db_entry.get("last_sync") if db_entry else None,
            "updated_at": db_entry["updated_at"] if db_entry else None,
            "webhook_url": WEBHOOK_URLS.get(name),
        })
    return api_success(data=connectors, message="Connectors retrieved successfully")


# ─── Generic connector wizard APIs ──────────────────────────────────────────────

@router.post("/{connector_name}/connect")
async def connect_connector(
    connector_name: str,
    body: ConnectorCredentials,
    current_user: dict = Depends(verify_token)
):
    """
    Step 1 of wizard: Save API credentials for a connector.
    Stores credentials in the connectors table (is_connected stays False until activate).
    """
    if connector_name not in CONNECTOR_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown connector: {connector_name}. Valid: {list(CONNECTOR_NAMES)}")

    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    existing = sb.table("connectors").select("id").eq("name", connector_name).eq("agency_id", agency_id).execute()

    if existing.data:
        sb.table("connectors").update({
            "auth_data": body.credentials,
            "is_connected": False,
        }).eq("id", existing.data[0]["id"]).execute()
        connector_id = existing.data[0]["id"]
    else:
        result = sb.table("connectors").insert({
            "name": connector_name,
            "agency_id": agency_id,
            "is_connected": False,
            "auth_data": body.credentials,
        }).execute()
        connector_id = result.data[0]["id"]

    logger.info(f"Credentials saved for connector: {connector_name} (agency: {agency_id})")
    return api_success(
        data={
            "connector_id": connector_id,
            "connector_name": connector_name,
            "status": "credentials_saved",
            "next_step": f"GET /connectors/{connector_name}/listings",
        },
        message=f"{CONNECTOR_DISPLAY.get(connector_name, connector_name)} credentials saved. Proceed to import listings."
    )


@router.get("/{connector_name}/listings")
async def get_connector_listings(
    connector_name: str,
    current_user: dict = Depends(verify_token)
):
    """
    Step 2 of wizard: Fetch available listings for this connector.
    Returns mock data — replace with real provider API calls once credentials are live.
    """
    if connector_name not in {"property_finder", "bayut", "dubizzle"}:
        raise HTTPException(status_code=400, detail=f"Listings not supported for: {connector_name}")

    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    connector = sb.table("connectors").select("auth_data").eq("name", connector_name).eq("agency_id", agency_id).execute()
    if not connector.data or not connector.data[0].get("auth_data"):
        raise HTTPException(
            status_code=400,
            detail=f"No credentials found for {connector_name}. Call POST /connectors/{connector_name}/connect first."
        )

    listings = MOCK_LISTINGS.get(connector_name, [])

    return api_success(
        data={
            "connector": connector_name,
            "listings": listings,
            "total": len(listings),
        },
        message=f"Listings fetched for {CONNECTOR_DISPLAY.get(connector_name, connector_name)}"
    )


@router.post("/{connector_name}/activate")
async def activate_connector(
    connector_name: str,
    current_user: dict = Depends(verify_token)
):
    """
    Step 3 of wizard — 'Finish & Activate' button:
    Marks the connector as is_connected=True in the database.
    Returns the webhook URL to be pasted in the provider's dashboard.
    """
    if connector_name not in CONNECTOR_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown connector: {connector_name}")

    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    existing = sb.table("connectors").select("id").eq("name", connector_name).eq("agency_id", agency_id).execute()
    if not existing.data:
        raise HTTPException(
            status_code=400,
            detail=f"Connector {connector_name} not found. Save credentials first via POST /connectors/{connector_name}/connect"
        )

    sb.table("connectors").update({
        "is_connected": True,
        "last_sync": "now()",
    }).eq("id", existing.data[0]["id"]).execute()

    webhook_url = WEBHOOK_URLS.get(
        connector_name,
        f"{settings.API_BASE_URL}/webhooks/{connector_name.replace('_', '-')}"
    )

    logger.info(f"Connector activated: {connector_name} (agency: {agency_id})")
    return api_success(
        data={
            "connector_name": connector_name,
            "display_name": CONNECTOR_DISPLAY.get(connector_name, connector_name),
            "is_connected": True,
            "webhook_url": webhook_url,
            "instruction": f"Paste this webhook URL in your {CONNECTOR_DISPLAY.get(connector_name, connector_name)} account settings.",
        },
        message=f"{CONNECTOR_DISPLAY.get(connector_name, connector_name)} is now active!"
    )


@router.get("/{connector_name}/webhook-url")
async def get_webhook_url(
    connector_name: str,
    _: dict = Depends(verify_token)
):
    """Return the webhook URL for a given connector (Step 3 display)."""
    if connector_name not in CONNECTOR_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown connector: {connector_name}")

    webhook_url = WEBHOOK_URLS.get(
        connector_name,
        f"{settings.API_BASE_URL}/webhooks/{connector_name.replace('_', '-')}"
    )
    return api_success(
        data={"connector_name": connector_name, "webhook_url": webhook_url},
        message="Webhook URL retrieved"
    )


@router.post("/{connector_name}/disconnect")
async def disconnect_connector(
    connector_name: str,
    current_user: dict = Depends(verify_token)
):
    """Disconnect any connector — sets is_connected=False and clears auth_data."""
    if connector_name not in CONNECTOR_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown connector: {connector_name}")

    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    apply_agency_scope(
        sb.table("connectors").update({"is_connected": False, "auth_data": None}),
        current_user,
    ).eq("name", connector_name).execute()

    logger.info(f"Connector disconnected: {connector_name} (agency: {agency_id})")
    return api_success(
        data={"connector_name": connector_name, "is_connected": False},
        message=f"{CONNECTOR_DISPLAY.get(connector_name, connector_name)} disconnected successfully"
    )


# ─── Google Calendar OAuth ─────────────────────────────────────────────────────

@router.get("/google-calendar/auth")
async def google_calendar_auth(current_user: dict = Depends(verify_token)):
    """Initiate Google Calendar OAuth2 flow. Returns the auth URL."""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=400, detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID in .env")
    agency_id = require_agency_id(current_user)
    auth_url = get_auth_url(state=agency_id)
    return api_success(data={"auth_url": auth_url}, message="Google OAuth URL generated")


@router.get("/google-calendar/callback")
async def google_calendar_callback(code: str = Query(...), state: str = Query(None)):
    """Google OAuth2 callback. Exchanges code for tokens and stores in Supabase."""
    try:
        tokens = exchange_code_for_tokens(code)
        sb = get_supabase()
        agency_id = state
        if not agency_id:
            raise ValueError("Agency ID missing from state")

        existing = sb.table("connectors").select("id").eq("name", "google_calendar").eq("agency_id", agency_id).execute()
        if existing.data:
            sb.table("connectors").update({
                "is_connected": True,
                "auth_data": tokens,
                "last_sync": "now()",
            }).eq("id", existing.data[0]["id"]).execute()
        else:
            sb.table("connectors").insert({
                "name": "google_calendar",
                "agency_id": agency_id,
                "is_connected": True,
                "auth_data": tokens,
                "last_sync": "now()",
            }).execute()

        logger.info("Google Calendar connected successfully")
        return RedirectResponse(url=f"{settings.FRONTEND_URL}/connectors?connected=google_calendar")
    except Exception as e:
        logger.error(f"Google Calendar OAuth error: {e}")
        return RedirectResponse(url=f"{settings.FRONTEND_URL}/connectors?error=google_calendar")


@router.post("/google-calendar/disconnect")
async def google_calendar_disconnect(current_user: dict = Depends(verify_token)):
    """Disconnect Google Calendar integration for the current agency."""
    sb = get_supabase()
    apply_agency_scope(
        sb.table("connectors").update({"is_connected": False, "auth_data": None}),
        current_user,
    ).eq("name", "google_calendar").execute()
    return api_success(message="Google Calendar disconnected")


@router.post("/google-calendar/test")
async def test_google_calendar(current_user: dict = Depends(verify_token)):
    """Test Google Calendar connection by listing upcoming events."""
    sb = get_supabase()
    require_agency_id(current_user)
    connector = (
        apply_agency_scope(
            sb.table("connectors").select("auth_data"),
            current_user,
        )
        .eq("name", "google_calendar")
        .eq("is_connected", True)
        .limit(1)
        .execute()
    )
    if not connector.data or not connector.data[0].get("auth_data"):
        raise HTTPException(status_code=400, detail="Google Calendar not connected")

    try:
        from services.calendar_service import _build_service
        service = _build_service(connector.data[0]["auth_data"])
        calendar_id = settings.GOOGLE_SHARED_CALENDAR_ID or "primary"
        cal = service.calendars().get(calendarId=calendar_id).execute()
        return api_success(data={"calendar_name": cal.get("summary"), "calendar_id": calendar_id}, message="Google Calendar connected")
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

    sb = get_supabase()
    sb.table("connectors").update({"is_connected": True, "last_sync": "now()"}).eq("name", "whatsapp").execute()

    return api_success(data={"provider": settings.WHATSAPP_PROVIDER}, message="WhatsApp test message sent")


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
    return api_success(data={"response": resp.json()}, message="Test Property Finder webhook sent")
