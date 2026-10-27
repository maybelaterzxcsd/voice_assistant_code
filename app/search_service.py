"""
search_service.py - Поиск и фильтрация событий в базе данных.
"""

from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models import Event
from thefuzz import process, fuzz
import re


def get_upcoming_events(db: Session, period: str):
    """Возвращает события на сегодня, завтра, послезавтра, на неделю или на прошлую неделю."""
    today = datetime.now().date()
    
    if period == "tomorrow":
        target_date = today + timedelta(days=1)
        events = db.query(Event).filter(
            Event.status == "pending",
            Event.date == target_date.strftime("%Y-%m-%d")
        ).order_by(Event.time.asc()).all()
        
    elif period == "day_after_tomorrow":
        target_date = today + timedelta(days=2)
        events = db.query(Event).filter(
            Event.status == "pending",
            Event.date == target_date.strftime("%Y-%m-%d")
        ).order_by(Event.time.asc()).all()
        
    elif period == "this_week":
        start_date = today.strftime("%Y-%m-%d")
        end_of_week = today + timedelta(days=(6 - today.weekday()))
        end_date = end_of_week.strftime("%Y-%m-%d")
        
        events = db.query(Event).filter(
            Event.status == "pending",
            Event.date >= start_date,
            Event.date <= end_date
        ).order_by(Event.date.asc(), Event.time.asc()).all()

    elif period == "last_week":
        start_of_last_week = today - timedelta(days=today.weekday() + 7)
        end_of_last_week = start_of_last_week + timedelta(days=6)
        
        events = db.query(Event).filter(
            Event.status == "pending",
            Event.date >= start_of_last_week.strftime("%Y-%m-%d"),
            Event.date <= end_of_last_week.strftime("%Y-%m-%d")
        ).order_by(Event.date.asc(), Event.time.asc()).all()
        
    else: 
        events = db.query(Event).filter(
            Event.status == "pending",
            Event.date == today.strftime("%Y-%m-%d")
        ).order_by(Event.time.asc()).all()
        
    return [
        {"title": e.title, "date": e.date, "time": e.time, "type": e.event_type} 
        for e in events
    ]


def search_events(db: Session, query: str, limit: int = 5) -> list:
    """Умный поиск событий по тексту через нечеткое сравнение (TheFuzz)."""
    all_events = db.query(Event).filter(Event.status == "pending").all()
    if not all_events:
        return []

    stop_words = ["найди", "покажи", "задачу", "задачу", "задача", "встречу", "встреча", "про", "о", "об", "насчет", "по", "мне", "пожалуйста"]
    clean_query = query.lower()
    for word in stop_words:
        clean_query = re.sub(rf'\b{word}\b', '', clean_query)
    clean_query = re.sub(r'\s+', ' ', clean_query).strip()
    
    if not clean_query:
        clean_query = query.lower()

    documents = [f"{e.title}" for e in all_events]
    
    matches = process.extract(clean_query, documents, limit=limit, scorer=fuzz.partial_ratio)
    
    results = []
    for match_text, score in matches:
        if score > 50: 
            for event in all_events:
                if event.title == match_text:
                    results.append({
                        "id": event.id,
                        "type": event.event_type,
                        "title": event.title,
                        "date": event.date,
                        "time": event.time,
                        "status": event.status,
                        "match_score": score
                    })
                    break
    return results