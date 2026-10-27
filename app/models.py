from sqlalchemy import Column, Integer, String, DateTime, Text
from app.database import Base
from datetime import datetime

class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, index=True) 
    title = Column(Text, nullable=False)
    date = Column(String, nullable=False)    
    time = Column(String, nullable=True)     
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="pending")