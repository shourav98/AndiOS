from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date
from uuid import UUID

class ContractBase(BaseModel):
    lead_id: UUID
    type: str  # tenancy_agreement | addendum
    property_address: str
    rent_amount: float
    security_deposit: Optional[float] = None
    start_date: date
    end_date: date
    notes: Optional[str] = None

class ContractCreate(ContractBase):
    pass

class ContractResponse(ContractBase):
    id: UUID
    status: str
    document_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
