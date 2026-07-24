from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from uuid import UUID
from enum import Enum


class ViewingStatus(str, Enum):
    scheduled = "scheduled"
    confirmed = "confirmed"
    completed = "completed"
    cancelled = "cancelled"
    no_show = "no_show"


class ViewingCreate(BaseModel):
    lead_id: UUID
    agent_id: Optional[UUID] = None
    property_address: str
    property_ref: Optional[str] = None
    viewing_datetime: datetime
    duration_minutes: int = 60
    notes: Optional[str] = None


class ViewingUpdate(BaseModel):
    agent_id: Optional[UUID] = None
    property_address: Optional[str] = None
    viewing_datetime: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    status: Optional[ViewingStatus] = None
    notes: Optional[str] = None


class AvailableSlotsRequest(BaseModel):
    agent_id: Optional[UUID] = None
    date_from: datetime
    date_to: datetime
    duration_minutes: int = 60


class ViewingResponse(BaseModel):
    id: UUID
    lead_id: UUID
    agent_id: Optional[UUID]
    property_address: str
    property_ref: Optional[str]
    viewing_datetime: datetime
    duration_minutes: int
    status: str
    google_event_id: Optional[str]
    google_meet_link: Optional[str]
    reminder_24h_sent: bool
    reminder_2h_sent: bool
    feedback_requested: bool
    feedback_received: Optional[str]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime


class TimeSlot(BaseModel):
    start: datetime
    end: datetime
    agent_id: Optional[UUID]
    agent_name: Optional[str]
