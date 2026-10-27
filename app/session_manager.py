import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass, field

@dataclass
class Message:
    role: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class PendingEvent:
    event_type: str
    title: str
    date: Optional[str] = None
    time: Optional[str] = None
    recurrence: Optional[str] = None
    missing_fields: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class Session:
    session_id: str
    messages: List[Message] = field(default_factory=list)
    pending_event: Optional[PendingEvent] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now) 
    
    def is_expired(self, minutes: int = 30) -> bool:
        return (datetime.now() - self.updated_at) > timedelta(minutes=minutes)
    
    def add_message(self, role: str, content: str):
        self.messages.append(Message(role=role, content=content))
        self.updated_at = datetime.now() 
    
    def get_history(self, max_messages: int = 10) -> List[Dict[str, str]]:
        history = []
        for msg in self.messages[-max_messages:]:
            history.append({"role": msg.role, "content": msg.content})
        return history

class SessionManager:
    def __init__(self):
        self._sessions: Dict[str, Session] = {}
    
    def create_session(self) -> str:
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = Session(session_id=session_id)
        return session_id
    
    def get_session(self, session_id: str) -> Optional[Session]:
        session = self._sessions.get(session_id)
        if session and session.is_expired():
            del self._sessions[session_id]
            return None
        return session
    
    def delete_session(self, session_id: str):
        self._sessions.pop(session_id, None)

session_manager = SessionManager()