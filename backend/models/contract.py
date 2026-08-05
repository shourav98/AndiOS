from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date
from uuid import UUID

class ContractBase(BaseModel):
    lead_id: Optional[UUID] = None
    type: str = "tenancy_agreement"
    property_unit: Optional[str] = None
    area_community: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    rent_amount: Optional[float] = None
    rent_words: Optional[str] = None
    number_of_cheques: Optional[int] = None
    security_deposit: Optional[float] = None
    broker_fee: Optional[float] = None
    
    owner_name: Optional[str] = None
    owner_phone: Optional[str] = None
    owner_email: Optional[str] = None
    owner_emirates_id: Optional[str] = None
    
    tenant_name: Optional[str] = None
    tenant_phone: Optional[str] = None
    tenant_email: Optional[str] = None
    tenant_emirates_id: Optional[str] = None
    
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
