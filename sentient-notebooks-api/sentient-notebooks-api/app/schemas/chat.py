from typing import Literal

from pydantic import BaseModel


class ChatRequest(BaseModel):
    query: str
    mode: Literal["files", "web", "both"] = "files"
