from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from uuid import UUID
from enum import Enum


class Direction(str, Enum):
    inbound = "inbound"
    outbound = "outbound"


class Channel(str, Enum):
    whatsapp = "whatsapp"
    email = "email"
    call = "call"


class SenderType(str, Enum):
    ai = "ai"
    agent = "agent"
    lead = "lead"


class ConversationCreate(BaseModel):
    lead_id: UUID
    direction: Direction
    channel: Channel = Channel.whatsapp
    message_body: str
    sender_type: SenderType
    sender_id: Optional[UUID] = None
    whatsapp_message_id: Optional[str] = None


class SendMessageRequest(BaseModel):
    message_body: str
    agent_id: Optional[UUID] = None


class ConversationResponse(BaseModel):
    id: UUID
    lead_id: UUID
    direction: str
    channel: str
    message_body: str
    sender_type: str
    sender_id: Optional[UUID]
    whatsapp_message_id: Optional[str]
    is_read: bool
    timestamp: datetime
