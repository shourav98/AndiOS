from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime
from uuid import UUID

class DocumentBase(BaseModel):
    lead_id: UUID
    document_type: str
    file_url: str

class DocumentCreate(DocumentBase):
    pass

class DocumentResponse(DocumentBase):
    id: UUID
    extracted_data: Optional[dict[str, Any]] = None
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
