from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime
from uuid import UUID
from enum import Enum


class AgentRole(str, Enum):
    agent = "agent"
    senior_agent = "senior_agent"
    manager = "manager"
    owner = "owner"


class AgentCreate(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    role: AgentRole = AgentRole.agent
    calendar_id: Optional[str] = None
    whatsapp_number: Optional[str] = None
    branch: Optional[str] = None


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[AgentRole] = None
    calendar_id: Optional[str] = None
    whatsapp_number: Optional[str] = None
    branch: Optional[str] = None
    is_active: Optional[bool] = None


class AgentResponse(BaseModel):
    id: UUID
    name: str
    phone: Optional[str]
    email: str
    role: str
    calendar_id: Optional[str]
    whatsapp_number: Optional[str]
    branch: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime
