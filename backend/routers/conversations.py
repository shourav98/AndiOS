"""
Conversations Router
GET  /conversations/{lead_id}       — get full thread for a lead
POST /conversations/{lead_id}/send  — agent sends manual WhatsApp reply
POST /conversations/{lead_id}/read  — mark messages as read
"""
from fastapi import APIRouter, Depends, HTTPException
from uuid import UUID
from database.supabase_client import get_supabase
from models.conversation import SendMessageRequest, ConversationResponse
from services.whatsapp_service import send_whatsapp_message
from middleware.auth_middleware import verify_token, get_current_user_id
from utils.response import api_success, ApiResponse
from utils.tenant import verify_lead_access, require_agency_id
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/conversations", tags=["Conversations"])


@router.get("/{lead_id}", response_model=ApiResponse[list[ConversationResponse]])
async def get_conversation(lead_id: UUID, current_user: dict = Depends(verify_token)):
    """Get full conversation thread for a lead (WhatsApp chat view)."""
    sb = get_supabase()
    await verify_lead_access(str(lead_id), current_user)

    result = (
        sb.table("conversations")
        .select("*")
        .eq("lead_id", str(lead_id))
        .order("timestamp", desc=False)
        .execute()
    )
    return api_success(data=result.data, message="Conversation retrieved successfully")


@router.post("/{lead_id}/send")
async def agent_send_message(
    lead_id: UUID,
    body: SendMessageRequest,
    user_id: str = Depends(get_current_user_id),
    current_user: dict = Depends(verify_token),
):
    """Agent manually sends a WhatsApp reply to a lead."""
    sb = get_supabase()
    lead_data = await verify_lead_access(str(lead_id), current_user)
    phone = lead_data["phone"]
    result = await send_whatsapp_message(phone, body.message_body)

    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {result.get('error')}")

    # Log to conversations
    conv = sb.table("conversations").insert({
        "lead_id": str(lead_id),
        "agency_id": lead_data["agency_id"],
        "direction": "outbound",
        "channel": "whatsapp",
        "message_body": body.message_body,
        "sender_type": "agent",
        "sender_id": body.agent_id or user_id,
    }).execute()

    return api_success(data={"conversation_id": conv.data[0]["id"]}, message="Message sent successfully")


@router.post("/{lead_id}/read")
async def mark_as_read(lead_id: UUID, current_user: dict = Depends(verify_token)):
    """Mark all inbound messages for a lead as read."""
    sb = get_supabase()
    await verify_lead_access(str(lead_id), current_user)
    sb.table("conversations").update({"is_read": True}).eq("lead_id", str(lead_id)).eq(
        "direction", "inbound"
    ).execute()
    return api_success(message="Messages marked as read")
