from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date
from uuid import UUID

class ChequeBase(BaseModel):
    contract_id: UUID
    cheque_number: str
    bank_name: str
    amount: float
    due_date: date

class ChequeCreate(ChequeBase):
    pass

class ChequeResponse(ChequeBase):
    id: UUID
    status: str
    front_image_url: Optional[str] = None
    back_image_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
