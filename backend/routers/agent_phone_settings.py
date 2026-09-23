"""
Agent Phone Settings Router — BYON Voice Setup

Provides endpoints for agents to:
  1. View the Central Platform DID and forwarding dial codes
  2. Initiate and confirm BYON outbound caller ID verification (Twilio OutgoingCallerIds)
  3. Update their forwarding status

GET  /agents/me/forwarding                      — Central DID + dial codes
POST /agents/me/outbound-caller-id/initiate     — Start Twilio caller ID verification
POST /agents/me/outbound-caller-id/confirm      — Confirm 6-digit verification code
PATCH /agents/me/forwarding/status              — Update forwarding_status
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel, Field
from config import settings
from database.supabase_client import get_supabase
from utils.response import api_success, api_error

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents/me", tags=["Agent Phone Settings"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_agent_context(request: Request) -> tuple[str, str]:
    """
    Extract agent_id and agency_id from request state (set by auth middleware).
    Raises 401 if not authenticated.
    """
    agent_id = getattr(request.state, "agent_id", None)
    agency_id = getattr(request.state, "agency_id", None)
    if not agent_id or not agency_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return agent_id, agency_id


# ─── Schemas ──────────────────────────────────────────────────────────────────

class CallerIdInitiateRequest(BaseModel):
    phone_number: str = Field(
        ...,
        description="Agent's personal mobile number in E.164 format (e.g. +971501234567)",
        examples=["+971501234567"],
    )


class CallerIdConfirmRequest(BaseModel):
    phone_number: str = Field(..., description="Same phone number used in initiation")
    validation_code: str = Field(
        ...,
        description="6-digit code received via Twilio automated call/SMS",
        min_length=4,
        max_length=10,
    )


class ForwardingStatusUpdate(BaseModel):
    forwarding_status: str = Field(
        ...,
        description="New forwarding status",
        pattern="^(not_configured|configured|verified|disabled)$",
    )


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/forwarding")
async def get_forwarding_info(request: Request):
    """
    Return the Central Platform DID that agents configure conditional call
    forwarding to, together with carrier-specific dial codes.

    Agents dial one of these codes on their personal phone:
      Etisalat/du (UAE):  **61*CENTRAL_DID#   (no-answer forwarding)
      Generic GSM:        *67*CENTRAL_DID#     (on-busy forwarding)
      Unconditional:      *21*CENTRAL_DID#     (forward all calls)

    Also returns the agent's current forwarding_status from communication_accounts.
    """
    agent_id, agency_id = _get_agent_context(request)

    central_did = (getattr(settings, "CENTRAL_INBOUND_DID", "") or "").strip()
    if not central_did:
        central_did = "<CENTRAL_DID_NOT_CONFIGURED>"
        logger.warning("[AgentPhoneSettings] CENTRAL_INBOUND_DID is not set in config")

    # Look up agent's current voice comm account
    sb = get_supabase()
    result = (
        sb.table("communication_accounts")
        .select("forwarding_status, phone_number, verified_caller_id_sid")
        .eq("agency_id", agency_id)
        .eq("agent_id", agent_id)
        .eq("channel", "voice")
        .maybe_single()
        .execute()
    )
    current_status = "not_configured"
    agent_phone = None
    verified_sid = None
    if result and result.data:
        current_status = result.data.get("forwarding_status", "not_configured")
        agent_phone = result.data.get("phone_number")
        verified_sid = result.data.get("verified_caller_id_sid")

    # Build dial codes
    did_digits = central_did.lstrip("+")
    dial_codes = {
        "etisalat_du_no_answer": f"**61*+{did_digits}#",
        "generic_on_busy": f"*67*+{did_digits}#",
        "unconditional": f"*21*+{did_digits}#",
        "cancel_all": "##002#",
    }

    return api_success(
        data={
            "central_inbound_did": central_did,
            "forwarding_status": current_status,
            "agent_phone_registered": agent_phone,
            "caller_id_verified": bool(verified_sid),
            "dial_codes": dial_codes,
            "instructions": (
                f"1. On your UAE mobile phone, dial: **61*+{did_digits}# "
                f"(or *67*+{did_digits}# for on-busy forwarding).\n"
                "2. Press Call. You will hear a confirmation tone.\n"
                "3. Return to AndiOS and tap 'Mark Forwarding Configured'.\n"
                "4. Optionally complete Caller ID verification so your number "
                "shows on outbound calls."
            ),
        },
        message="Forwarding configuration info",
    )


@router.post("/outbound-caller-id/initiate")
async def initiate_caller_id_verification(
    request: Request,
    body: CallerIdInitiateRequest,
):
    """
    Initiate Twilio OutgoingCallerIds verification for the agent's personal number.

    Twilio will call (or SMS) the agent's phone with a 6-digit validation code.
    The agent then submits that code via the /confirm endpoint.

    On success, returns the validation_code so the confirm endpoint can reference it.
    """
    agent_id, agency_id = _get_agent_context(request)
    phone = body.phone_number.strip()

    from services.voice_service import initiate_outbound_caller_id_verification
    result = await initiate_outbound_caller_id_verification(
        agent_phone=phone,
        agency_id=agency_id,
        agent_id=agent_id,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.get("message", "Verification failed"))

    return api_success(data=result, message="Caller ID verification initiated")


@router.post("/outbound-caller-id/confirm")
async def confirm_caller_id_verification(
    request: Request,
    body: CallerIdConfirmRequest,
):
    """
    Confirm the 6-digit code sent to the agent's phone by Twilio.

    On success, stores the OutgoingCallerId SID in communication_accounts
    and returns verified status.
    """
    agent_id, agency_id = _get_agent_context(request)

    from services.voice_service import confirm_outbound_caller_id_verification
    result = await confirm_outbound_caller_id_verification(
        agent_phone=body.phone_number.strip(),
        validation_code=body.validation_code.strip(),
        agency_id=agency_id,
        agent_id=agent_id,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.get("message", "Confirmation failed"))

    return api_success(data=result, message="Caller ID verification confirmed")


@router.patch("/forwarding/status")
async def update_forwarding_status(
    request: Request,
    body: ForwardingStatusUpdate,
):
    """
    Update the agent's call forwarding status.

    Agents call this after dialling the forwarding code on their phone to mark
    their setup as 'configured'. The system may later auto-promote to 'verified'
    after a successful test call.
    """
    agent_id, agency_id = _get_agent_context(request)

    sb = get_supabase()
    try:
        sb.table("communication_accounts").upsert(
            {
                "agency_id": agency_id,
                "agent_id": agent_id,
                "channel": "voice",
                "provider": "twilio",
                "forwarding_status": body.forwarding_status,
                "status": "active" if body.forwarding_status in ("configured", "verified") else "disconnected",
            },
            on_conflict="agency_id,agent_id,channel",
        ).execute()
    except Exception as e:
        logger.error(f"[AgentPhoneSettings] Failed to update forwarding_status: {e}")
        raise HTTPException(status_code=500, detail="Failed to update forwarding status")

    return api_success(
        data={"forwarding_status": body.forwarding_status},
        message=f"Forwarding status updated to '{body.forwarding_status}'",
    )
