"""
Connectors Router — Integrations / Connectors page

Route Registration Order:
Specific named routes (/google-calendar/*, /whatsapp/*, /meta-esu/*, /property-finder/*)
are registered BEFORE generic /{connector_name}/* routes to prevent path parameter shadowing.

Specific Routes:
GET    /connectors                                    — list all connectors and status
GET    /connectors/google-calendar/auth               — start Google OAuth flow
GET    /connectors/google-calendar/callback           — handle OAuth callback
POST   /connectors/google-calendar/disconnect         — disconnect Google Calendar
POST   /connectors/google-calendar/test               — test Google Calendar
POST   /connectors/whatsapp/test                      — send a test WhatsApp message
POST   /connectors/meta-esu/callback                  — Meta Embedded Signup v4 callback
POST   /connectors/whatsapp/embedded-signup-callback  — Alias for Meta ESU callback
GET    /connectors/meta-esu/status                    — get WhatsApp connection status
GET    /connectors/whatsapp/status                    — Alias for WhatsApp status
POST   /connectors/meta-esu/disconnect                — disconnect WhatsApp account
DELETE /connectors/whatsapp/disconnect                — Alias for WhatsApp disconnect
POST   /connectors/property-finder/test               — send a test PF webhook

Generic Wizard Routes:
POST   /connectors/{connector_name}/connect           — save API credentials (Step 1)
GET    /connectors/{connector_name}/listings          — fetch listings for import (Step 2)
POST   /connectors/{connector_name}/activate          — finish & activate connector (Step 3)
GET    /connectors/{connector_name}/webhook-url       — get webhook URL for connector
POST   /connectors/{connector_name}/disconnect        — disconnect any generic connector
"""
from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from config import settings
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token
from services.calendar_service import get_auth_url, exchange_code_for_tokens
from utils.crypto import encrypt_token, decrypt_token
from utils.response import api_success
from utils.tenant import require_agency_id, apply_agency_scope, is_management_role

logger = logging.getLogger(__name__)

# Security: Silence HTTP request logging globally to prevent client_secret, code,
# or access tokens in URLs/parameters from leaking into application logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

router = APIRouter(prefix="/connectors", tags=["Connectors"])

# ─── Supported connectors config ───────────────────────────────────────────────

CONNECTOR_NAMES = {"property_finder", "bayut", "dubizzle", "whatsapp", "google_calendar"}
RESERVED_SPECIFIC_CONNECTORS = {"meta-esu", "google-calendar"}


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


class EmbeddedSignupCallbackRequest(BaseModel):
    """
    Payload sent by frontend after completing Meta Embedded Signup (v4).
    """
    code: str
    waba_id: str
    phone_number_id: str
    agent_id: Optional[str] = None
    confirm_replace_default: bool = False


class EmbeddedSignupDisconnectRequest(BaseModel):
    agent_id: Optional[str] = None


# ─── List all connectors ────────────────────────────────────────────────────────

@router.get("")
async def list_connectors(current_user: dict = Depends(verify_token)):
    """List all connectors with their connection status for the current agency."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)
    query = sb.table("connectors").select("id, name, is_connected, last_sync, updated_at")
    result = apply_agency_scope(query, current_user).execute()

    db_map = {c["name"]: c for c in (result.data or [])}

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


# ─── Google Calendar OAuth (Specific Routes) ───────────────────────────────────

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


# ─── WhatsApp Management Test (Specific Route) ────────────────────────────────

@router.post("/whatsapp/test")
async def test_whatsapp(to_phone: str = Query(...), current_user: dict = Depends(verify_token)):
    if not is_management_role(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Only owners and managers can test connectors")

    agency_id = require_agency_id(current_user)
    agent_id = current_user.get("agent_id") or (current_user.get("id") if not is_management_role(current_user.get("role")) else None)
    from services.whatsapp_service import send_whatsapp_for_agency
    result = await send_whatsapp_for_agency(
        agency_id,
        to_phone,
        "✅ AndiOS WhatsApp integration is working! This is a test message from your AI assistant.",
        agent_id=agent_id,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail="WhatsApp test failed — check provider credentials")

    sb = get_supabase()
    sb.table("connectors").update({"is_connected": True, "last_sync": "now()"}).eq("name", "whatsapp").eq("agency_id", agency_id).execute()

    return api_success(data={"provider": settings.WHATSAPP_PROVIDER}, message="WhatsApp test message sent")


# ─── Meta Embedded Signup v4 (Specific Routes) ────────────────────────────────

def _graph_base_url() -> str:
    """Return versioned Graph API base URL using settings."""
    version = getattr(settings, "META_GRAPH_API_VERSION", "v26.0") or "v26.0"
    return f"https://graph.facebook.com/{version}"


async def _exchange_code_for_token(code: str, corr_id: str) -> dict:
    """
    Exchange ESU authorization code for an access token.
    Never logs client_secret or authorization code.
    Logs details server-side with correlation id; returns generic error to client.
    """
    import httpx
    url = f"{_graph_base_url()}/oauth/access_token"
    params = {
        "client_id": getattr(settings, "META_APP_ID", ""),
        "client_secret": getattr(settings, "META_APP_SECRET", ""),
        "code": code,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            logger.error("[ESU][%s] Token exchange error: %s %s", corr_id, resp.status_code, resp.text)
            raise HTTPException(
                status_code=502,
                detail=f"WhatsApp provider authorization failed. Reference: {corr_id}",
            )
        return resp.json()


async def _verify_token_waba_permission(access_token: str, waba_id: str, corr_id: str) -> None:
    """
    Verify via debug_token endpoint that access_token is valid and grants
    access to the target WABA (fail-closed).
    Reference: https://developers.facebook.com/docs/facebook-login/guides/access-tokens/debugging-and-error-handling
    Sentence: "The debug_token endpoint returns metadata about the given user or page access token." (13 words).
    """
    import httpx
    app_id = getattr(settings, "META_APP_ID", "")
    app_secret = getattr(settings, "META_APP_SECRET", "")
    if not app_id or not app_secret:
        logger.error("[ESU][%s] META_APP_ID or META_APP_SECRET not configured for debug_token check", corr_id)
        raise HTTPException(
            status_code=503,
            detail=f"WhatsApp provider credentials not configured on server. Reference: {corr_id}",
        )

    base = _graph_base_url()
    debug_url = f"{base}/debug_token"
    params = {
        "input_token": access_token,
        "access_token": f"{app_id}|{app_secret}",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(debug_url, params=params)
        if resp.status_code != 200:
            logger.error("[ESU][%s] debug_token HTTP error: %s %s", corr_id, resp.status_code, resp.text)
            raise HTTPException(
                status_code=400,
                detail=f"Failed to verify access token with WhatsApp provider. Reference: {corr_id}",
            )

        data = resp.json().get("data", {})
        if not data.get("is_valid"):
            logger.error("[ESU][%s] Token reported invalid by Meta debug_token: %s", corr_id, data)
            raise HTTPException(
                status_code=400,
                detail=f"WhatsApp authorization token is invalid or expired. Reference: {corr_id}",
            )

        granular_scopes = data.get("granular_scopes", [])
        waba_authorized = False
        for gs in granular_scopes:
            if gs.get("scope") == "whatsapp_business_management":
                target_ids = [str(t) for t in gs.get("target_ids", [])]
                if str(waba_id) in target_ids:
                    waba_authorized = True
                    break

        if not waba_authorized:
            logger.error(
                "[ESU][%s] Token granular scopes do not positively grant whatsapp_business_management access to WABA %s: %s",
                corr_id, waba_id, granular_scopes
            )
            raise HTTPException(
                status_code=403,
                detail=f"WhatsApp authorization does not grant access to account {waba_id}. Reference: {corr_id}",
            )



async def _verify_waba_and_phone(waba_id: str, phone_number_id: str, access_token: str, corr_id: str) -> dict:
    """
    Verify with new token that waba_id and phone_number_id belong to it before saving (fail-closed).
    Handles pagination across WABA phone numbers.
    Fetches display_phone_number, verified_name, quality_rating, platform_type, is_pin_enabled, is_on_biz_app.
    """
    import httpx
    base = _graph_base_url()
    headers = {"Authorization": f"Bearer {access_token}"}

    # Step A: Validate token permissions via debug_token (fail-closed)
    await _verify_token_waba_permission(access_token, waba_id, corr_id)

    async with httpx.AsyncClient(timeout=20) as client:
        # Step B: Fetch phone number details
        phone_url = f"{base}/{phone_number_id}"
        params = {
            "fields": "id,display_phone_number,verified_name,quality_rating,platform_type,is_pin_enabled,code_verification_status,is_on_biz_app"
        }
        phone_resp = await client.get(phone_url, headers=headers, params=params)
        if phone_resp.status_code != 200:
            logger.error("[ESU][%s] Failed to verify phone_number_id %s: %s %s", corr_id, phone_number_id, phone_resp.status_code, phone_resp.text)
            raise HTTPException(
                status_code=400,
                detail=f"Phone number could not be verified with WhatsApp provider. Reference: {corr_id}",
            )
        phone_data = phone_resp.json()

        # Step C: Verify phone belongs to WABA with pagination (fail-closed)
        waba_numbers_url = f"{base}/{waba_id}/phone_numbers"
        phone_found = False
        next_url = waba_numbers_url

        while next_url:
            waba_resp = await client.get(next_url, headers=headers)
            if waba_resp.status_code != 200:
                logger.error("[ESU][%s] WABA phone_numbers query failed (%s): %s", corr_id, waba_resp.status_code, waba_resp.text)
                raise HTTPException(
                    status_code=400,
                    detail=f"WhatsApp Business Account numbers check failed. Reference: {corr_id}",
                )
            waba_json = waba_resp.json()
            waba_data = waba_json.get("data", [])
            waba_phone_ids = [str(item.get("id")) for item in waba_data]
            if str(phone_number_id) in waba_phone_ids:
                phone_found = True
                break
            paging = waba_json.get("paging", {})
            next_url = paging.get("next")

        if not phone_found:
            logger.error("[ESU][%s] phone_number_id %s not found in WABA %s numbers list", corr_id, phone_number_id, waba_id)
            raise HTTPException(
                status_code=400,
                detail=f"Phone number {phone_number_id} does not belong to WhatsApp Business Account {waba_id}. Reference: {corr_id}",
            )

        return phone_data


async def _register_phone_number_if_needed(
    phone_number_id: str,
    access_token: str,
    pin: str,
    phone_data: dict,
    corr_id: str,
) -> tuple[bool, bool, bool]:
    """
    Registers the phone number for Cloud API use if needed.
    Returns: (is_registered, is_coexistence, actually_registered_with_pin)

    Per Meta Official Docs:
    - URL: https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users
    - Citation: [UNVERIFIED: Meta doc page requires client-side execution; relying on documented is_on_biz_app and platform_type fields]
    - Coexistence: is_on_biz_app is True AND platform_type == "CLOUD_API"
      (phone registration is skipped for coexistence numbers).
    - Already registered: platform_type == "CLOUD_API" and is_pin_enabled is True.
    - Normal number: platform_type is NOT_APPLICABLE and is_on_biz_app is not True.
      (Per Meta Phone Number reference: NOT_APPLICABLE means phone number has not yet completed Cloud API registration).
    - Fallback: [UNVERIFIED fallback: error string matching] if register fails with
      "register endpoint is not available for smb businesses".
    """
    import httpx

    is_on_biz_app = phone_data.get("is_on_biz_app") is True
    platform_type = (phone_data.get("platform_type") or "").upper()

    # 1. Coexistence check evaluated BEFORE "already registered" branch:
    # A coexistence number has is_on_biz_app=True and platform_type=CLOUD_API,
    # and registration must be skipped.
    if is_on_biz_app and platform_type == "CLOUD_API":
        logger.info(
            "[ESU][%s] Detected WhatsApp Business App Coexistence for phone %s (is_on_biz_app=True, platform_type=CLOUD_API). Skipping registration.",
            corr_id, phone_number_id,
        )
        return True, True, False

    # 2. Already registered non-coexistence Cloud API number:
    if phone_data.get("is_pin_enabled") is True and platform_type == "CLOUD_API":
        logger.info("[ESU][%s] Phone %s is already registered with Cloud API.", corr_id, phone_number_id)
        return True, False, False

    # 3. Normal number (e.g. platform_type NOT_APPLICABLE and is_on_biz_app not True): must register
    base = _graph_base_url()
    url = f"{base}/{phone_number_id}/register"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "pin": pin}

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code in (200, 201):
            logger.info("[ESU][%s] Successfully registered Cloud API number %s with PIN.", corr_id, phone_number_id)
            return True, False, True

        resp_text = resp.text.lower()
        # [UNVERIFIED fallback: error string matching]
        if "register endpoint is not available for smb businesses" in resp_text:
            logger.info("[ESU][%s] Detected SMB Coexistence mode for number %s via error string fallback.", corr_id, phone_number_id)
            return True, True, False

        logger.warning("[ESU][%s] Register endpoint returned unexpected status %s: %s", corr_id, resp.status_code, resp.text)
        return False, False, False


async def _subscribe_app_to_waba(
    waba_id: str,
    access_token: str,
    corr_id: str,
) -> bool:
    """
    Subscribe this app to WABA webhooks.
    POST https://graph.facebook.com/{version}/{waba_id}/subscribed_apps
    Official docs: https://developers.facebook.com/docs/graph-api/reference/whats-app-business-account/subscribed_apps/
    """
    import httpx
    base = _graph_base_url()
    url = f"{base}/{waba_id}/subscribed_apps"
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, headers=headers)
        if resp.status_code in (200, 201):
            logger.info("[ESU][%s] Successfully subscribed app to WABA %s", corr_id, waba_id)
            return True
        logger.warning("[ESU][%s] Subscribe app to WABA %s failed: %s %s", corr_id, waba_id, resp.status_code, resp.text)
        return False


async def _bg_auto_create_templates(waba_id: str, access_token: str, account_id: str):
    """Background task to auto-create standard starter templates for a WABA."""
    from services.communication.template_service import auto_create_standard_templates
    sb = get_supabase()
    try:
        await auto_create_standard_templates(waba_id, access_token, account_id)
        res = sb.table("communication_accounts").select("metadata").eq("id", account_id).limit(1).execute()
        meta = (res.data[0].get("metadata") or {}) if (res and res.data) else {}
        meta["templates_status"] = "created"
        sb.table("communication_accounts").update({"metadata": meta}).eq("id", account_id).execute()
        logger.info("[ESU] Starter templates successfully created for account %s", account_id)
    except Exception as tmpl_err:
        logger.warning("[ESU] Auto template creation warning for WABA %s (account %s): %s", waba_id, account_id, tmpl_err)
        try:
            res = sb.table("communication_accounts").select("metadata").eq("id", account_id).limit(1).execute()
            meta = (res.data[0].get("metadata") or {}) if (res and res.data) else {}
            meta["templates_status"] = "failed"
            meta["templates_error"] = "TEMPLATE_CREATION_FAILED"
            sb.table("communication_accounts").update({"metadata": meta}).eq("id", account_id).execute()
        except Exception:
            pass


@router.post("/meta-esu/callback")
@router.post("/whatsapp/embedded-signup-callback")
async def meta_embedded_signup_callback(
    body: EmbeddedSignupCallbackRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(verify_token),
):

    """
    Meta Embedded Signup v4 backend callback.
    Enforces per-agent isolation, verifies caller-agency ownership of agent_id,
    verifies WABA ownership and token permissions (fail-closed), validates phone uniqueness,
    authoritatively detects coexistence server-side, encrypts credentials,
    subscribes WABA webhooks with status tracking, and schedules template creation.
    """
    corr_id = uuid.uuid4().hex[:12]
    agency_id = require_agency_id(current_user)
    current_user_id = current_user.get("id") or current_user.get("sub")
    current_user_role = current_user.get("role", "")

    sb = get_supabase()

    # 1. Enforce agent scoping and agency ownership
    if body.agent_id:
        if not is_management_role(current_user_role) and body.agent_id != current_user_id:
            raise HTTPException(
                status_code=403,
                detail="Agents can only connect their own personal WhatsApp account.",
            )
        # Verify manager-supplied agent_id belongs to caller's agency
        agent_res = (
            sb.table("agents")
            .select("id")
            .eq("id", body.agent_id)
            .eq("agency_id", agency_id)
            .limit(1)
            .execute()
        )
        if not agent_res.data:
            raise HTTPException(
                status_code=400,
                detail=f"Target agent {body.agent_id} does not belong to your agency.",
            )
        target_agent_id = body.agent_id
    else:
        if not is_management_role(current_user_role):
            target_agent_id = current_user_id
        else:
            target_agent_id = None

    # Agency-default protection: Never overwrite active agency-default row with provider != 'meta'
    # without explicit confirm_replace_default=true.
    if target_agent_id is None:
        existing_default = (
            sb.table("communication_accounts")
            .select("id, provider, status")
            .eq("agency_id", agency_id)
            .is_("agent_id", "null")
            .eq("channel", "whatsapp")
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if existing_default and existing_default.data:
            existing_prov = existing_default.data[0].get("provider")
            if existing_prov and existing_prov != "meta":
                if not body.confirm_replace_default:
                    logger.warning(
                        "[ESU][%s] Rejected attempt to overwrite active agency-default %s account without confirm_replace_default.",
                        corr_id, existing_prov
                    )
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"An active agency-default WhatsApp account already exists with provider '{existing_prov}'. "
                            "To replace it with Meta Cloud API, set confirm_replace_default=true in the request body."
                        ),
                    )
                logger.info(
                    "[ESU][%s] Manager explicitly confirmed replacing active agency-default provider '%s' with 'meta'.",
                    corr_id, existing_prov
                )

    if not getattr(settings, "META_APP_SECRET", ""):
        raise HTTPException(
            status_code=503,
            detail="META_APP_SECRET is not configured. Cannot exchange Embedded Signup code.",
        )

    # 2. Exchange code for access token
    token_response = await _exchange_code_for_token(body.code, corr_id)
    access_token = token_response.get("access_token", "")
    if not access_token:
        raise HTTPException(status_code=502, detail=f"Meta returned no access_token. Reference: {corr_id}")

    expires_in = token_response.get("expires_in")
    token_expires_at = (
        (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat()
        if expires_in
        else None
    )

    # 3. Verify with new token that waba_id and phone_number_id belong to it (fail-closed)
    phone_details = await _verify_waba_and_phone(body.waba_id, body.phone_number_id, access_token, corr_id)
    display_phone = phone_details.get("display_phone_number", "")
    phone_e164_digits = re.sub(r"[^\d]", "", display_phone)
    verified_name = phone_details.get("verified_name", "")

    # 4. Global phone number uniqueness precheck
    existing_phone = (
        sb.table("communication_accounts")
        .select("id, agency_id, agent_id, status")
        .eq("phone_number_id", body.phone_number_id)
        .neq("status", "disconnected")
        .limit(1)
        .execute()
    )
    if existing_phone.data:
        conflict = existing_phone.data[0]
        if conflict["agency_id"] != agency_id or (conflict.get("agent_id") != target_agent_id):
            raise HTTPException(
                status_code=409,
                detail=f"Phone number {display_phone or body.phone_number_id} is already connected to another account.",
            )

    # 5. Registration with PIN & coexistence detection
    random_pin = f"{secrets.randbelow(900000) + 100000}"
    reg_ok, server_is_coexistence, actually_registered_with_pin = await _register_phone_number_if_needed(
        body.phone_number_id, access_token, random_pin, phone_details, corr_id
    )

    if not reg_ok:
        logger.error("[ESU][%s] Registration failed for phone %s", corr_id, body.phone_number_id)
        # Record failure in meta_onboarding_state as 'registration_failed', status as 'pending'
        failed_upsert = {
            "agency_id": agency_id,
            "agent_id": target_agent_id,
            "channel": "whatsapp",
            "provider": "meta",
            "phone_number": phone_e164_digits,
            "phone_number_id": body.phone_number_id,
            "external_account_id": body.waba_id,
            "status": "pending",
            "is_coexistence": False,
            "meta_onboarding_state": "registration_failed",
            "metadata": {
                "waba_name": verified_name,
                "platform_type": phone_details.get("platform_type", ""),
                "registration_error": "PHONE_REGISTRATION_FAILED",
            },
        }
        try:
            existing_res = (
                sb.table("communication_accounts")
                .select("id, status, metadata")
                .eq("agency_id", agency_id)
                .eq("channel", "whatsapp")
            )
            if target_agent_id:
                existing_res = existing_res.eq("agent_id", target_agent_id)
            else:
                existing_res = existing_res.is_("agent_id", "null")
            existing = existing_res.limit(1).execute()
            if existing.data:
                existing_row = existing.data[0]
                if existing_row.get("status") == "active":
                    # Never overwrite an existing active row: record failure in metadata only
                    existing_meta = existing_row.get("metadata") or {}
                    if not isinstance(existing_meta, dict):
                        existing_meta = {}
                    existing_meta["last_registration_failure"] = {
                        "failed_at": datetime.now(timezone.utc).isoformat(),
                        "corr_id": corr_id,
                        "phone_number_id": body.phone_number_id,
                        "error_code": "PHONE_REGISTRATION_FAILED",
                    }
                    sb.table("communication_accounts").update({"metadata": existing_meta}).eq("id", existing_row["id"]).execute()
                    logger.info("[ESU][%s] Active account kept intact; recorded registration failure in metadata.", corr_id)
                else:
                    sb.table("communication_accounts").update(failed_upsert).eq("id", existing_row["id"]).execute()
            else:
                sb.table("communication_accounts").insert(failed_upsert).execute()
        except Exception as db_err:
            logger.warning("[ESU][%s] Could not record registration failure in DB: %s", corr_id, db_err)


        raise HTTPException(
            status_code=400,
            detail=f"Phone number registration with WhatsApp provider failed. Reference: {corr_id}",
        )

    # Store registration_pin_enc ONLY when register actually succeeded with that PIN; never for coexistence
    encrypted_pin = encrypt_token(random_pin) if actually_registered_with_pin else None

    # 6. Subscribe to WABA webhooks
    sub_ok = await _subscribe_app_to_waba(body.waba_id, access_token, corr_id)
    onboarding_state = "embedded_signup" if sub_ok else "webhook_subscription_failed"

    # 7. Encrypt token and prepare upsert data (NO app_secret written to metadata)
    encrypted_token = encrypt_token(access_token)

    upsert_data = {
        "agency_id": agency_id,
        "agent_id": target_agent_id,
        "channel": "whatsapp",
        "provider": "meta",
        "phone_number": phone_e164_digits,
        "phone_number_id": body.phone_number_id,
        "external_account_id": body.waba_id,
        "access_token_enc": encrypted_token,
        "token_expires_at": token_expires_at,
        "registration_pin_enc": encrypted_pin,
        "status": "active",
        "is_coexistence": server_is_coexistence,
        "meta_onboarding_state": onboarding_state,
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "disconnected_at": None,
        "metadata": {
            "waba_name": verified_name,
            "platform_type": phone_details.get("platform_type", ""),
            "templates_status": "pending",
        },
    }

    # 8. Existing-row lookup by (agency_id, agent_id, channel) regardless of provider
    try:
        existing_res = (
            sb.table("communication_accounts")
            .select("id, provider")
            .eq("agency_id", agency_id)
            .eq("channel", "whatsapp")
        )
        if target_agent_id:
            existing_res = existing_res.eq("agent_id", target_agent_id)
        else:
            existing_res = existing_res.is_("agent_id", "null")
        existing = existing_res.limit(1).execute()

        if existing.data:
            account_id = existing.data[0]["id"]
            sb.table("communication_accounts").update(upsert_data).eq("id", account_id).execute()
        else:
            insert_res = sb.table("communication_accounts").insert(upsert_data).execute()
            account_id = insert_res.data[0]["id"]

    except Exception as exc:
        err_str = str(exc).lower()
        if "unique" in err_str or "duplicate" in err_str or "23505" in err_str:
            logger.warning("[ESU][%s] DB unique constraint conflict: %s", corr_id, exc)
            raise HTTPException(
                status_code=409,
                detail=f"A WhatsApp account with these details is already registered. Reference: {corr_id}",
            )
        logger.error("[ESU][%s] DB upsert failed: %s", corr_id, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save WhatsApp account to database. Reference: {corr_id}",
        )

    # 9. Schedule standard template creation in background
    if background_tasks:
        background_tasks.add_task(_bg_auto_create_templates, body.waba_id, access_token, account_id)
    else:
        import asyncio
        asyncio.create_task(_bg_auto_create_templates(body.waba_id, access_token, account_id))

    logger.info(
        "[ESU][%s] WhatsApp connected: agency=%s agent=%s phone=%s waba=%s coexistence=%s sub=%s",
        corr_id, agency_id, target_agent_id,
        phone_e164_digits[-4:] + "****" if phone_e164_digits else "?",
        body.waba_id, server_is_coexistence, sub_ok,
    )

    resp_status = "connected" if sub_ok else "partially_connected"
    resp_msg = (
        "WhatsApp account successfully connected via Meta Embedded Signup"
        if sub_ok
        else "WhatsApp connected, but webhook subscription failed; will be retried automatically"
    )

    return api_success(
        data={
            "status": resp_status,
            "account_id": account_id,
            "phone_number": display_phone,
            "phone_number_id": body.phone_number_id,
            "waba_id": body.waba_id,
            "is_coexistence": server_is_coexistence,
            "verified_name": verified_name,
            "provider": "meta",
            "meta_onboarding_state": onboarding_state,
        },
        message=resp_msg,
    )


@router.get("/meta-esu/status")
@router.get("/whatsapp/status")
async def meta_embedded_signup_status(
    agent_id: Optional[str] = Query(None, description="Agent UUID — omit for agency default"),
    current_user: dict = Depends(verify_token),
):
    """
    Get current WhatsApp connection status scoped per agent or agency default.
    Orders by active rows first, then most recently connected.
    """
    agency_id = require_agency_id(current_user)
    current_user_id = current_user.get("id") or current_user.get("sub")
    current_user_role = current_user.get("role", "")

    if not is_management_role(current_user_role):
        target_agent_id = current_user_id
    else:
        target_agent_id = agent_id

    sb = get_supabase()
    query = (
        sb.table("communication_accounts")
        .select(
            "id, provider, phone_number, phone_number_id, external_account_id, status, "
            "is_coexistence, meta_onboarding_state, connected_at, token_expires_at, metadata"
        )
        .eq("agency_id", agency_id)
        .eq("channel", "whatsapp")
    )
    if target_agent_id:
        query = query.eq("agent_id", target_agent_id)
    else:
        query = query.is_("agent_id", "null")

    # Order by status asc ('active' before 'disconnected') and connected_at desc
    query = query.order("status", desc=False).order("connected_at", desc=True)
    result = query.limit(1).execute()

    if not result.data:
        return api_success(
            data={"status": "disconnected", "connected": False, "provider": None},
            message="No WhatsApp account connected",
        )

    row = result.data[0]
    is_active = row.get("status") == "active"
    return api_success(
        data={
            "status": row.get("status", "disconnected"),
            "connected": is_active,
            "provider": row.get("provider", "meta"),

            "account_id": row.get("id"),
            "phone_number": row.get("phone_number", ""),
            "phone_number_id": row.get("phone_number_id", ""),
            "waba_id": row.get("external_account_id", ""),
            "is_coexistence": row.get("is_coexistence", False),
            "meta_onboarding_state": row.get("meta_onboarding_state", "embedded_signup"),
            "connected_at": row.get("connected_at"),
            "token_expires_at": row.get("token_expires_at"),
            "waba_name": (row.get("metadata") or {}).get("waba_name", ""),
        },
        message="WhatsApp connection status",
    )


@router.post("/meta-esu/disconnect")
@router.delete("/whatsapp/disconnect")
async def meta_embedded_signup_disconnect(
    body: Optional[EmbeddedSignupDisconnectRequest] = None,
    agent_id: Optional[str] = Query(None),
    current_user: dict = Depends(verify_token),
):
    """
    Disconnect WhatsApp account per agent or agency default.
    Best-effort unregisters/unsubscribes from WABA, nulls tokens and PINs.
    """
    agency_id = require_agency_id(current_user)
    current_user_id = current_user.get("id") or current_user.get("sub")
    current_user_role = current_user.get("role", "")

    req_agent_id = (body.agent_id if body else None) or agent_id

    if not is_management_role(current_user_role):
        target_agent_id = current_user_id
    else:
        target_agent_id = req_agent_id

    sb = get_supabase()

    # Query existing account to get WABA ID and token for best-effort unsubscribe
    # Select only columns that exist on live schema: id, provider, external_account_id, access_token_enc
    acc_query = (
        sb.table("communication_accounts")
        .select("id, provider, external_account_id, access_token_enc")
        .eq("agency_id", agency_id)
        .eq("channel", "whatsapp")
        .eq("provider", "meta")
    )
    if target_agent_id:
        acc_query = acc_query.eq("agent_id", target_agent_id)
    else:
        acc_query = acc_query.is_("agent_id", "null")
    acc_res = acc_query.limit(1).execute()

    if acc_res and acc_res.data:
        acc_row = acc_res.data[0]
        waba_id = acc_row.get("external_account_id")
        raw_tok = acc_row.get("access_token_enc")
        tok = ""
        if raw_tok:
            try:
                tok = decrypt_token(raw_tok, account_id=acc_row.get("id"))
            except Exception as dec_err:
                logger.warning("[ESU] Disconnect: could not decrypt token for WABA unsubscribe: %s", dec_err)
                tok = ""
        if waba_id and tok:
            try:
                import httpx
                base = _graph_base_url()
                async with httpx.AsyncClient(timeout=10) as client:
                    unsub_resp = await client.delete(
                        f"{base}/{waba_id}/subscribed_apps",
                        headers={"Authorization": f"Bearer {tok}"},
                    )
                    logger.info("[ESU] Disconnect: unsubscribed app from WABA %s (status=%s)", waba_id, unsub_resp.status_code)
            except Exception as unsub_err:
                logger.warning("[ESU] Disconnect: best-effort WABA unsubscribe failed for %s: %s", waba_id, unsub_err)

        query = (
            sb.table("communication_accounts")
            .update({
                "status": "disconnected",
                "access_token_enc": None,
                "registration_pin_enc": None,
                "disconnected_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("id", acc_row["id"])
            .eq("provider", "meta")
        )
        query.execute()
        logger.info("[Connectors] WhatsApp disconnected: agency=%s agent=%s account=%s", agency_id, target_agent_id, acc_row["id"])
    else:
        logger.info("[Connectors] No active Meta WhatsApp account found to disconnect for agency=%s agent=%s", agency_id, target_agent_id)

    return api_success(message="WhatsApp account disconnected successfully")



# ─── Property Finder Test Endpoint (Specific Route) ───────────────────────────

@router.post("/property-finder/test")
async def test_property_finder_webhook(_: dict = Depends(verify_token)):
    """Send a test Property Finder webhook payload to verify pipeline."""
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


# ─── Generic Connector Wizard APIs (Registered After Specific Routes) ──────────

@router.post("/{connector_name}/connect")
async def connect_connector(
    connector_name: str,
    body: ConnectorCredentials,
    current_user: dict = Depends(verify_token),
):
    """
    Step 1 of wizard: Save API credentials for a generic connector.
    """
    if connector_name in RESERVED_SPECIFIC_CONNECTORS:
        raise HTTPException(status_code=404, detail=f"Use dedicated endpoint for {connector_name}")
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
    current_user: dict = Depends(verify_token),
):
    """
    Step 2 of wizard: Fetch available listings for this connector from stored feed/credentials.
    """
    if connector_name in RESERVED_SPECIFIC_CONNECTORS:
        raise HTTPException(status_code=404, detail=f"Use dedicated endpoint for {connector_name}")
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
    current_user: dict = Depends(verify_token),
):
    """
    Step 3 of wizard: 'Finish & Activate' connector.
    """
    if connector_name in RESERVED_SPECIFIC_CONNECTORS:
        raise HTTPException(status_code=404, detail=f"Use dedicated endpoint for {connector_name}")
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
    _: dict = Depends(verify_token),
):
    """Return the webhook URL for a given connector."""
    if connector_name in RESERVED_SPECIFIC_CONNECTORS:
        raise HTTPException(status_code=404, detail=f"Use dedicated endpoint for {connector_name}")
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
    current_user: dict = Depends(verify_token),
):
    """Disconnect any generic connector — sets is_connected=False and clears auth_data."""
    if connector_name in RESERVED_SPECIFIC_CONNECTORS:
        raise HTTPException(status_code=404, detail=f"Use dedicated endpoint for {connector_name}")
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
