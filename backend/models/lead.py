from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
from datetime import datetime
from uuid import UUID
from enum import Enum


class LeadSource(str, Enum):
    property_finder = "property_finder"
    bayut = "bayut"
    dubizzle = "dubizzle"
    direct = "direct"
    referral = "referral"


class LeadStatus(str, Enum):
    new = "new"
    qualifying = "qualifying"
    viewing_booked = "viewing_booked"
    viewing_done = "viewing_done"
    negotiating = "negotiating"
    closed = "closed"
    lost = "lost"
    handover = "handover"


class AIStage(str, Enum):
    greeting = "greeting"
    qualifying = "qualifying"
    slot_offering = "slot_offering"
    confirmed = "confirmed"
    handover = "handover"
    done = "done"


class LeadPurpose(str, Enum):
    rent = "rent"
    buy = "buy"


# ─── Request bodies ────────────────────────────────────────────────────────────

class LeadCreate(BaseModel):
    name: str
    phone: str
    email: Optional[EmailStr] = None
    source: LeadSource = LeadSource.property_finder
    external_lead_id: Optional[str] = None
    property_ref: Optional[str] = None
    property_address: Optional[str] = None
    bedrooms: Optional[int] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    currency: str = "AED"
    location_pref: Optional[str] = None
    purpose: LeadPurpose = LeadPurpose.rent
    assigned_agent_id: Optional[UUID] = None
    notes: Optional[str] = None


class LeadUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    status: Optional[LeadStatus] = None
    assigned_agent_id: Optional[UUID] = None
    bedrooms: Optional[int] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    location_pref: Optional[str] = None
    is_ai_handling: Optional[bool] = None
    notes: Optional[str] = None


class HandoverRequest(BaseModel):
    reason: str
    agent_id: Optional[UUID] = None


# ─── Response bodies ───────────────────────────────────────────────────────────

class LeadResponse(BaseModel):
    id: UUID
    external_lead_id: Optional[str]
    name: str
    phone: str
    email: Optional[str]
    source: str
    property_ref: Optional[str]
    property_address: Optional[str]
    bedrooms: Optional[int]
    budget_min: Optional[float]
    budget_max: Optional[float]
    currency: str
    location_pref: Optional[str]
    purpose: str
    status: str
    ai_stage: str
    assigned_agent_id: Optional[UUID]
    is_ai_handling: bool
    handover_reason: Optional[str]
    qualification_score: Optional[int]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime


class LeadStats(BaseModel):
    total: int
    new: int
    qualifying: int
    viewing_booked: int
    viewing_done: int
    closed: int
    lost: int
    handover: int
    avg_response_time_seconds: Optional[float]
    lead_to_viewing_pct: Optional[float]
    viewing_to_close_pct: Optional[float]
