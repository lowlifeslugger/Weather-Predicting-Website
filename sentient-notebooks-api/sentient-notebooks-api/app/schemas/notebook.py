from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NotebookCreate(BaseModel):
    name: str
    llm_model: Optional[str] = None


class NotebookUpdate(BaseModel):
    name: Optional[str] = None
    llm_model: Optional[str] = None


class NotebookOut(BaseModel):
    id: UUID
    name: str
    llm_model: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
