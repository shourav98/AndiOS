"""
Connectors Router — Integrations / Connectors page
GET  /connectors                                    — list all connectors and status
POST /connectors/{name}/connect                     — save API credentials (Step 1)
GET  /connectors/{name}/listings                    — fetch listings for import (Step 2)
POST /connectors/{name}/activate                    — finish & activate connector (Step 3)
GET  /connectors/{name}/webhook-url                 — get webhook URL for connector
POST /connectors/{name}/disconnect                  — disconnect any connector
GET  /connectors/google-calendar/auth               — start Google OAuth flow
GET  /connectors/google-calendar/callback           — handle OAuth callback
POST /connectors/google-calendar/disconnect         — disconnect Google Calendar
POST /connectors/whatsapp/test                      — send a test WhatsApp message
POST /connectors/whatsapp/embedded-signup-callback  — Meta Embedded Signup v4 token exchange
GET  /connectors/whatsapp/status                    — get WhatsApp connection status
DELETE /connectors/whatsapp/disconnect              — disconnect WhatsApp account
POST /connectors/property-finder/test               — send a test PF webhook
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
from utils.tenant import require_agency_id, apply_agency_scope, is_management_role
from config import settings
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/connectors", tags=["Connectors"])


# ─── Supported connectors config ───────────────────────────────────────────────

CONNECTOR_NAMES = {"property_finder", "bayut", "dubizzle", "whatsapp", "google_calendar"}

# Property portal connectors shown on the Connectors page
# WhatsApp and Google Calendar are managed separately (env/OAuth) and excluded here
CONNECTOR_DISPLAY = {
    "property_finder": "Property Finder",
    "bayut": "Bayut",
    "dubizzle": "Dubizzle",
}

WEBHOOK_URLS = {
    "property_finder": f"{settings.API_BASE_URL}/webhooks/property-finder",
    "bayut": f"{settings.API_BASE_URL}/webhooks/bayut",
    "dubizzle": f"{settings.API_BASE_URL}/webhooks/dubizzle",
    "whatsapp": f"{settings.API_BASE_URL}/webhooks/whatsapp",
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
    Step 2 of wizard: Fetch available listings for this connector from stored feed/credentials.
    Returns real listings if imported or a clean status indicating sync via active webhook.
    """
    if connector_name not in {"property_finder", "bayut", "dubizzle"}:
        raise HTTPException(status_code=400, detail=f"Listings not supported for: {connector_name}")

    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    connector = sb.table("connectors").select("auth_data, is_connected").eq("name", connector_name).eq("agency_id", agency_id).execute()
    if not connector.data or not connector.data[0].get("auth_data"):
        raise HTTPException(
            status_code=400,
            detail=f"No credentials found for {connector_name}. Call POST /connectors/{connector_name}/connect first."
        )

    auth_data = connector.data[0].get("auth_data") or {}
    # If listings were provided in auth_data or parsed from feed
    listings = auth_data.get("listings", [])
    if isinstance(listings, list) and len(listings) > 0:
        total = len(listings)
        msg = f"Retrieved {total} listings for {CONNECTOR_DISPLAY.get(connector_name, connector_name)}"
    else:
        listings = []
        total = 0
        msg = f"Credentials configured. Listings will automatically sync via webhook for {CONNECTOR_DISPLAY.get(connector_name, connector_name)}."

    return api_success(
        data={
            "connector": connector_name,
            "listings": listings,
            "total": total,
            "is_connected": connector.data[0].get("is_connected", False),
        },
        message=msg
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
        logger.error(f"Google Calendar test failed: {e}")
        raise HTTPException(status_code=500, detail="Calendar test failed — check connector credentials")


# ─── WhatsApp ─────────────────────────────────────────────────────────────────

@router.post("/whatsapp/test")
async def test_whatsapp(to_phone: str = Query(...), current_user: dict = Depends(verify_token)):
    """Send a test WhatsApp message to verify the connection. Management only."""
    if not is_management_role(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Only owners and managers can test connectors")

    result = await send_whatsapp_message(
        to_phone,
        "✅ AndiOS WhatsApp integration is working! This is a test message from your AI assistant."
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail="WhatsApp test failed — check provider credentials")

    sb = get_supabase()
    agency_id = require_agency_id(current_user)
    # Tenant isolation: only update THIS agency's connector row
    (
        sb.table("connectors")
        .update({"is_connected": True, "last_sync": "now()"})
        .eq("name", "whatsapp")
        .eq("agency_id", agency_id)
        .execute()
    )

    return api_success(data={"provider": settings.WHATSAPP_PROVIDER}, message="WhatsApp test message sent")


# ─── Meta Embedded Signup v4 ─────────────────────────────────────────────────
# Reference: https://developers.facebook.com/docs/whatsapp/embedded-signup

class EmbeddedSignupCallbackRequest(BaseModel):
    """
    Payload sent by the frontend after the user completes the Meta Embedded Signup
    (ESU v4) popup. The JS SDK returns these fields in the onMessage callback.

    Fields:
        code:           The authorization code to exchange for a system user token.
        waba_id:        The WhatsApp Business Account ID.
        phone_number_id: Meta's phone number ID for this number.
        is_coexistence: True if the number was already in use with WhatsApp Business App.
        agent_id:       Optional — if connecting for a specific agent (BYON).
    """
    code: str
    waba_id: str
    phone_number_id: str
    is_coexistence: bool = False
    agent_id: Optional[str] = None


async def _exchange_code_for_token(code: str) -> dict:
    """
    Exchange the ESU authorization code for a system user access token.
    POST https://graph.facebook.com/{version}/oauth/access_token
    Reference: https://developers.facebook.com/docs/whatsapp/embedded-signup/token-exchange
    """
    import httpx
    graph_version = getattr(settings, "META_GRAPH_API_VERSION", "v22.0")
    url = f"https://graph.facebook.com/{graph_version}/oauth/access_token"
    params = {
        "client_id": getattr(settings, "META_APP_ID", ""),
        "client_secret": getattr(settings, "META_APP_SECRET", ""),
        "code": code,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def _register_phone_number(phone_number_id: str, access_token: str) -> None:
    """
    Register the phone number for Cloud API use.
    POST https://graph.facebook.com/{version}/{phone_number_id}/register
    Reference: https://developers.facebook.com/docs/whatsapp/cloud-api/reference/registration
    """
    import httpx
    graph_version = getattr(settings, "META_GRAPH_API_VERSION", "v22.0")
    url = f"https://graph.facebook.com/{graph_version}/{phone_number_id}/register"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "pin": "000000"}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code not in (200, 201):
            logger.error("[ESU] Phone register error %s: %s", resp.status_code, resp.text)
            # Non-fatal for coexistence numbers that are already registered
            if resp.status_code != 400:
                resp.raise_for_status()


async def _subscribe_app_to_waba(
    waba_id: str,
    access_token: str,
    is_coexistence: bool,
) -> None:
    """
    Subscribe this app to WABA webhooks.
    POST https://graph.facebook.com/{version}/{waba_id}/subscribed_apps

    For Coexistence accounts, additional webhook fields are required:
      - smb_message_echoes  (messages sent from the mobile WhatsApp Business App)
      - history             (historical message sync)
      - smb_app_state_sync  (app state synchronization)
    Reference: https://developers.facebook.com/docs/whatsapp/embedded-signup/coexistence
    """
    import httpx
    graph_version = getattr(settings, "META_GRAPH_API_VERSION", "v22.0")
    url = f"https://graph.facebook.com/{graph_version}/{waba_id}/subscribed_apps"
    headers = {"Authorization": f"Bearer {access_token}"}

    # Standard fields
    webhook_fields = "messages,message_template_status_update,account_update"
    if is_coexistence:
        # Additional fields required for WhatsApp Coexistence
        # Reference: https://developers.facebook.com/docs/whatsapp/embedded-signup/coexistence
        webhook_fields += ",smb_message_echoes,history,smb_app_state_sync"

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, headers=headers, params={"webhook_fields": webhook_fields})
        if resp.status_code not in (200, 201):
            logger.warning("[ESU] Subscribe app to WABA error %s: %s", resp.status_code, resp.text)


async def _fetch_phone_number_details(phone_number_id: str, access_token: str) -> dict:
    """
    Fetch the actual E.164 phone number for this phone_number_id.
    GET https://graph.facebook.com/{version}/{phone_number_id}
    """
    import httpx
    graph_version = getattr(settings, "META_GRAPH_API_VERSION", "v22.0")
    url = f"https://graph.facebook.com/{graph_version}/{phone_number_id}"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"fields": "display_phone_number,verified_name,quality_rating,name_status"}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, headers=headers, params=params)
        if resp.status_code == 200:
            return resp.json()
    return {}


@router.post("/whatsapp/embedded-signup-callback")
async def whatsapp_embedded_signup_callback(
    body: EmbeddedSignupCallbackRequest,
    current_user: dict = Depends(verify_token),
):
    """
    Meta Embedded Signup v4 backend callback.

    Called by the frontend after the user completes the ESU popup.
    This endpoint:
      1. Validates input
      2. Exchanges the auth code for a system user access token
      3. Fetches the phone number details (E.164 number, display name)
      4. Registers the phone number for Cloud API use
      5. Subscribes the app to WABA webhooks (+ coexistence fields if needed)
      6. Encrypts and stores the token in communication_accounts
      7. Returns the connected account summary

    Required env vars: META_APP_ID, META_APP_SECRET, META_GRAPH_API_VERSION
    """
    from utils.crypto import encrypt_token
    from datetime import datetime, timezone, timedelta
    import re

    agency_id = require_agency_id(current_user)
    agent_id = body.agent_id  # None = agency default; UUID = per-agent BYON

    # Validate META_APP_SECRET is configured
    if not getattr(settings, "META_APP_SECRET", ""):
        raise HTTPException(
            status_code=503,
            detail="META_APP_SECRET is not configured. Cannot exchange Embedded Signup code.",
        )

    # ── Step 1: Exchange code for access token ──
    try:
        token_response = await _exchange_code_for_token(body.code)
    except Exception as exc:
        logger.error("[ESU] Token exchange failed for agency %s: %s", agency_id, exc)
        raise HTTPException(status_code=502, detail=f"Meta token exchange failed: {exc}")

    access_token = token_response.get("access_token", "")
    if not access_token:
        raise HTTPException(
            status_code=502,
            detail="Meta returned no access_token. Check that META_APP_ID and META_APP_SECRET are correct.",
        )

    # Determine token expiry (long-lived = ~60 days; System User = permanent)
    expires_in = token_response.get("expires_in")  # seconds, or None for permanent
    token_expires_at = (
        (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat()
        if expires_in
        else None
    )

    # ── Step 2: Fetch phone number details ──
    phone_details = await _fetch_phone_number_details(body.phone_number_id, access_token)
    display_phone = phone_details.get("display_phone_number", "")
    # Normalize: strip spaces, dashes, leading '+'
    phone_e164_digits = re.sub(r"[^\d]", "", display_phone)
    verified_name = phone_details.get("verified_name", "")

    # ── Step 3: Register phone number for Cloud API ──
    try:
        await _register_phone_number(body.phone_number_id, access_token)
    except Exception as exc:
        logger.warning("[ESU] Phone registration warning for %s: %s", body.phone_number_id, exc)
        # Non-fatal — coexistence numbers may already be registered

    # ── Step 4: Subscribe app to WABA webhooks ──
    try:
        await _subscribe_app_to_waba(body.waba_id, access_token, body.is_coexistence)
    except Exception as exc:
        logger.warning("[ESU] Webhook subscription warning for WABA %s: %s", body.waba_id, exc)

    # ── Step 5: Encrypt token and upsert communication_accounts ──
    encrypted_token = encrypt_token(access_token)

    sb = get_supabase()
    upsert_data = {
        "agency_id": agency_id,
        "agent_id": agent_id,
        "channel": "whatsapp",
        "provider": "meta",
        "phone_number": phone_e164_digits,
        "phone_number_id": body.phone_number_id,
        "external_account_id": body.waba_id,
        "access_token": encrypted_token,
        "token_expires_at": token_expires_at,
        "status": "active",
        "is_coexistence": body.is_coexistence,
        "meta_onboarding_state": "embedded_signup",
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "disconnected_at": None,
        "metadata": {
            "app_secret": getattr(settings, "META_APP_SECRET", ""),
            "waba_name": verified_name,
        },
    }

    try:
        # Upsert: update if already exists (reconnect), insert if new
        existing_res = (
            sb.table("communication_accounts")
            .select("id")
            .eq("agency_id", agency_id)
            .eq("channel", "whatsapp")
            .eq("provider", "meta")
        )
        if agent_id:
            existing_res = existing_res.eq("agent_id", agent_id)
        else:
            existing_res = existing_res.is_("agent_id", "null")
        existing = existing_res.limit(1).execute()

        if existing.data:
            existing_id = existing.data[0]["id"]
            sb.table("communication_accounts").update(upsert_data).eq("id", existing_id).execute()
            account_id = existing_id
        else:
            insert_res = sb.table("communication_accounts").insert(upsert_data).execute()
            account_id = insert_res.data[0]["id"]

    except Exception as exc:
        logger.error("[ESU] DB upsert failed for agency %s: %s", agency_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to save WhatsApp account: {exc}")

    logger.info(
        "[ESU] WhatsApp connected: agency=%s agent=%s phone=%s waba=%s coexistence=%s",
        agency_id, agent_id, phone_e164_digits[-4:] + "****" if phone_e164_digits else "?",
        body.waba_id, body.is_coexistence,
    )

    return api_success(
        data={
            "status": "connected",
            "account_id": account_id,
            "phone_number": display_phone,
            "phone_number_id": body.phone_number_id,
            "waba_id": body.waba_id,
            "is_coexistence": body.is_coexistence,
            "verified_name": verified_name,
            "provider": "meta",
        },
        message="WhatsApp account connected via Embedded Signup",
    )


@router.get("/whatsapp/status")
async def whatsapp_connection_status(
    agent_id: Optional[str] = Query(None, description="Agent UUID — omit for agency default"),
    current_user: dict = Depends(verify_token),
):
    """
    Get the current WhatsApp connection status for this agency or specific agent.
    Returns account details without exposing the access token.
    """
    agency_id = require_agency_id(current_user)
    sb = get_supabase()

    query = (
        sb.table("communication_accounts")
        .select(
            "id, phone_number, phone_number_id, external_account_id, status, "
            "is_coexistence, meta_onboarding_state, connected_at, token_expires_at, metadata"
        )
        .eq("agency_id", agency_id)
        .eq("channel", "whatsapp")
    )
    if agent_id:
        query = query.eq("agent_id", agent_id)
    else:
        query = query.is_("agent_id", "null")

    result = query.limit(1).execute()

    if not result.data:
        return api_success(
            data={"status": "disconnected", "provider": None},
            message="No WhatsApp account connected",
        )

    row = result.data[0]
    return api_success(
        data={
            "status": row.get("status", "disconnected"),
            "provider": "meta",
            "phone_number": row.get("phone_number", ""),
            "phone_number_id": row.get("phone_number_id", ""),
            "waba_id": row.get("external_account_id", ""),
            "is_coexistence": row.get("is_coexistence", False),
            "connected_at": row.get("connected_at"),
            "token_expires_at": row.get("token_expires_at"),
            "waba_name": (row.get("metadata") or {}).get("waba_name", ""),
        },
        message="WhatsApp connection status",
    )


@router.delete("/whatsapp/disconnect")
async def whatsapp_disconnect(
    agent_id: Optional[str] = Query(None),
    current_user: dict = Depends(verify_token),
):
    """
    Disconnect a WhatsApp account.
    Sets status to 'disconnected' and clears the access token.
    Does NOT deregister the phone number from Meta — do that manually if needed.
    Management roles only.
    """
    from datetime import datetime, timezone
    if not is_management_role(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Only owners and managers can disconnect WhatsApp")

    agency_id = require_agency_id(current_user)
    sb = get_supabase()

    query = (
        sb.table("communication_accounts")
        .update({
            "status": "disconnected",
            "access_token": None,
            "disconnected_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("agency_id", agency_id)
        .eq("channel", "whatsapp")
    )
    if agent_id:
        query = query.eq("agent_id", agent_id)
    else:
        query = query.is_("agent_id", "null")

    query.execute()

    logger.info("[Connectors] WhatsApp disconnected: agency=%s agent=%s", agency_id, agent_id)
    return api_success(message="WhatsApp account disconnected")


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
