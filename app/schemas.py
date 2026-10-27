from pydantic import BaseModel
from typing import Optional, List


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    intent: str
    data: Optional[dict] = None
    session_id: Optional[str] = None


class EventResponse(BaseModel):
    id: int
    event_type: str
    title: str
    date: str
    time: Optional[str]
    status: str

    class Config:
        from_attributes = True


class EventCreate(BaseModel):
    event_type: str
    title: str
    date: str
    time: Optional[str] = None