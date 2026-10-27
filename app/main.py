import json
import re
import os
import calendar
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import engine, Base, get_db
from app.models import Event
from app.schemas import ChatRequest, ChatResponse, EventResponse, EventCreate
from app.llm_service import (
    determine_intent,
    extract_event_info,
    extract_reschedule_info,
    parse_date_expression,
    parse_time_expression
)
from app.search_service import search_events, get_upcoming_events
from app.session_manager import session_manager, PendingEvent
from app.responses import get_response, _human_date, _human_time

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Voice Assistant",
    description="Голосовой ассистент с контекстом диалога (GigaChat by Sber)",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="."), name="static")


@app.get("/ui", response_class=HTMLResponse)
def get_ui():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/")
def root():
    return {"status": "ok", "message": "Voice Assistant API is running!", "ui": "/ui"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db)):
    user_text = request.message.strip()
    if not user_text:
        return ChatResponse(response=get_response("unknown"), intent="unknown")
    
    session = None
    if request.session_id:
        session = session_manager.get_session(request.session_id)
    
    if not session:
        session_id = session_manager.create_session()
        session = session_manager.get_session(session_id)
    
    session.add_message("user", user_text)
    
    history = session.get_history()
    intent = determine_intent(user_text, history)
    
    print(f"\n=== CHAT ===")
    print(f"session_id: {session.session_id}")
    print(f"intent: {intent}")
    print(f"pending_event: {session.pending_event}")
    print(f"user_text: {user_text}")
    
    if intent in ("search", "create", "upcoming", "delete", "complete") and session.pending_event:
        print("СБРОС ЗАВИСШЕГО PENDING_EVENT")
        session.pending_event = None
    
    if session.pending_event:
        response = handle_clarification(user_text, session, db)
    elif intent == "create":
        response = handle_create(user_text, session, db)
    elif intent == "search":
        response = handle_search(user_text, db)
    elif intent == "upcoming":
        response = handle_upcoming(user_text, db)
    elif intent == "reschedule":
        response = handle_reschedule(user_text, session, db)
    elif intent == "delete":
        response = handle_delete(user_text, db)
    elif intent == "complete":
        response = handle_complete(user_text, db)
    else:
        response = ChatResponse(response=get_response("unknown"), intent="unknown", session_id=session.session_id)
    
    session.add_message("assistant", response.response)
    return response


def handle_clarification(user_text: str, session, db: Session) -> ChatResponse:
    pending = session.pending_event
    if not pending:
        return ChatResponse(response=get_response("unknown"), intent="unknown", session_id=session.session_id)
    
    if pending.event_type == "reschedule":
        user_text_lower = user_text.lower().strip()
        if user_text_lower in ["да", "yes", "ок", "ok", "подтверждаю"]:
            existing_event = db.query(Event).filter(
                Event.title.contains(pending.title),
                Event.status == "pending"
            ).first()
            if existing_event:
                if pending.date: existing_event.date = pending.date
                if pending.time: existing_event.time = pending.time
                db.commit()
                db.refresh(existing_event)
                session.pending_event = None
                return ChatResponse(
                    response=get_response("moved", title=existing_event.title, new_date=pending.date, new_time=pending.time),
                    intent="clarification",
                    data={"event_id": existing_event.id},
                    session_id=session.session_id
                )
        elif user_text_lower in ["нет", "no", "отмена", "cancel"]:
            session.pending_event = None
            return ChatResponse(response="Отменил перенос.", intent="clarification", session_id=session.session_id)
        else:
            new_data = extract_reschedule_info(user_text, pending.title, pending.date, pending.time)
            merged_date = new_data.get("date") or pending.date
            merged_time = new_data.get("time") or pending.time
            
            if not merged_date and not merged_time:
                return ChatResponse(response="Не понял дату. Скажи, например: 'на завтра' или 'на 28 октября'.", intent="clarification", session_id=session.session_id)
            
            session.pending_event = PendingEvent(event_type="reschedule", title=pending.title, date=merged_date, time=merged_time, missing_fields=[])
            return ChatResponse(
                response=f"Переношу '{pending.title}' на {_human_date(merged_date)} {_human_time(merged_time)}. Подтверди: 'да' или 'нет'.",
                intent="clarification",
                session_id=session.session_id
            )
    else:
        history = session.get_history()
        new_data = extract_event_info(user_text, history)
        merged_date = new_data.get("date") or pending.date
        merged_time = new_data.get("time") or pending.time
        merged_title = new_data.get("title") or pending.title
        merged_type = new_data.get("event_type") or pending.event_type
        
        missing_fields = []
        if not merged_date: missing_fields.append("date")
        if not merged_time and merged_type != "task": missing_fields.append("time")
        
        if missing_fields:
            question = _build_clarification_question(missing_fields, merged_type)
            session.pending_event = PendingEvent(
                event_type=merged_type, 
                title=merged_title, 
                date=merged_date, 
                time=merged_time, 
                recurrence=pending.recurrence, 
                missing_fields=missing_fields
            )
            return ChatResponse(response=get_response("clarification_ask", question=question), intent="clarification", session_id=session.session_id)
        
        recurrence = pending.recurrence
        events_created = 0
        
        if recurrence:
            current_date = datetime.strptime(merged_date, "%Y-%m-%d")
            for i in range(5):
                if recurrence == "daily":
                    new_date = (current_date + timedelta(days=i)).strftime("%Y-%m-%d")
                elif recurrence == "weekly":
                    new_date = (current_date + timedelta(weeks=i)).strftime("%Y-%m-%d")
                elif recurrence == "monthly":
                    new_month = current_date.month + i
                    new_year = current_date.year + (new_month - 1) // 12
                    new_month = (new_month - 1) % 12 + 1
                    last_day = calendar.monthrange(new_year, new_month)[1]
                    new_day = min(current_date.day, last_day)
                    new_date = datetime(new_year, new_month, new_day).strftime("%Y-%m-%d")
                else:
                    new_date = merged_date
                
                new_event = Event(event_type=merged_type, title=merged_title, date=new_date, time=merged_time, status="pending", recurrence=recurrence)
                db.add(new_event)
                events_created += 1
            db.commit()
            session.pending_event = None
            return ChatResponse(
                response=f"✅ Создана повторяющаяся задача: «{merged_title}» ({recurrence}) на {events_created} раз(а) вперед.", 
                intent="clarification", 
                session_id=session.session_id
            )
        else:
            new_event = Event(event_type=merged_type, title=merged_title, date=merged_date, time=merged_time, status="pending", recurrence=None)
            db.add(new_event)
            db.commit()
            db.refresh(new_event)
            print(f"✅ СОЗДАНА ЗАДАЧА: ID={new_event.id}, Title={new_event.title}, Date={new_event.date}")
            session.pending_event = None
            template_key = f"created_{merged_type}" if merged_type in ("task", "meeting", "reminder") else "created"
            return ChatResponse(response=get_response(template_key, title=merged_title, date=merged_date, time=merged_time), intent="clarification", data={"event_id": new_event.id}, session_id=session.session_id)


def handle_create(user_text: str, session, db: Session) -> ChatResponse:
    history = session.get_history()
    event_data = extract_event_info(user_text, history)
    
    if "error" in event_data:
        return ChatResponse(response=f"Ошибка: {event_data['error']}", intent="create", session_id=session.session_id)
    
    if event_data.get("needs_clarification"):
        session.pending_event = PendingEvent(
            event_type=event_data.get("event_type", "task"), 
            title=event_data.get("title", user_text), 
            date=event_data.get("date"), 
            time=event_data.get("time"), 
            recurrence=event_data.get("recurrence"),
            missing_fields=event_data.get("missing_fields", [])
        )
        return ChatResponse(response=get_response("clarification_ask", question=event_data.get("clarification_question", "Уточни детали.")), intent="create", session_id=session.session_id)
    
    event_date = event_data.get("date")
    text_lower = user_text.lower()
    
    if not event_date:
        if "до конца недели" in text_lower or "к концу недели" in text_lower:
            today = datetime.now().date()
            days_ahead = 6 - today.weekday()
            if days_ahead <= 0: days_ahead += 7
            event_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        elif "на следующей неделе" in text_lower:
            today = datetime.now().date()
            days_ahead = (7 - today.weekday()) % 7 or 7
            event_date = (today + timedelta(days=days_ahead + 7)).strftime("%Y-%m-%d")
        elif "к концу месяца" in text_lower or "до конца месяца" in text_lower:
            today = datetime.now().date()
            last_day = calendar.monthrange(today.year, today.month)[1]
            event_date = datetime(today.year, today.month, last_day).date().strftime("%Y-%m-%d")
        else:
            event_date = datetime.now().date().strftime("%Y-%m-%d")
    
    event_time = event_data.get("time")
    recurrence = event_data.get("recurrence")
    
    if not recurrence:
        if event_data.get("event_type") == "meeting" and event_time:
            conflict = db.query(Event).filter(
                Event.date == event_date,
                Event.time == event_time,
                Event.status == "pending",
                Event.event_type == "meeting"
            ).first()
            if conflict:
                return ChatResponse(
                    response=f"⚠️ У вас уже есть встреча на это время: «{conflict.title}» ({_human_date(conflict.date)} {_human_time(conflict.time)}). Пожалуйста, выберите другое время.",
                    intent="create",
                    session_id=session.session_id
                )

        existing = db.query(Event).filter(
            Event.title == event_data.get("title"),
            Event.date == event_date,
            Event.time == event_time,
            Event.status == "pending"
        ).first()
        
        if existing:
            return ChatResponse(response=f"Такое событие уже есть: '{existing.title}' на {_human_date(existing.date)} {_human_time(existing.time)}.", intent="create", session_id=session.session_id)

    events_created = 0
    if recurrence:
        current_date = datetime.strptime(event_date, "%Y-%m-%d")
        for i in range(5):
            if recurrence == "daily":
                new_date = (current_date + timedelta(days=i)).strftime("%Y-%m-%d")
            elif recurrence == "weekly":
                new_date = (current_date + timedelta(weeks=i)).strftime("%Y-%m-%d")
            elif recurrence == "monthly":
                new_month = current_date.month + i
                new_year = current_date.year + (new_month - 1) // 12
                new_month = (new_month - 1) % 12 + 1
                last_day = calendar.monthrange(new_year, new_month)[1]
                new_day = min(current_date.day, last_day)
                new_date = datetime(new_year, new_month, new_day).strftime("%Y-%m-%d")
            else:
                new_date = event_date
            
            new_event = Event(
                event_type=event_data.get("event_type", "task"), 
                title=event_data.get("title", user_text), 
                date=new_date, 
                time=event_time, 
                status="pending",
                recurrence=recurrence
            )
            db.add(new_event)
            events_created += 1
    else:
        new_event = Event(
            event_type=event_data.get("event_type", "task"), 
            title=event_data.get("title", user_text), 
            date=event_date, 
            time=event_time, 
            status="pending",
            recurrence=None
        )
        db.add(new_event)
        events_created = 1

    db.commit()
    
    if recurrence:
        return ChatResponse(
            response=f"✅ Создана повторяющаяся задача: «{event_data.get('title')}» ({recurrence}) на {events_created} раз(а) вперед.", 
            intent="create", 
            session_id=session.session_id
        )
    else:
        db.refresh(new_event)
        template_key = f"created_{event_data.get('event_type', 'task')}"
        return ChatResponse(
            response=get_response(template_key, title=event_data.get('title'), date=event_date, time=event_time), 
            intent="create", 
            data={"event_id": new_event.id}, 
            session_id=session.session_id
        )


def handle_search(user_text: str, db: Session) -> ChatResponse:
    results = search_events(db, user_text)
    if not results:
        return ChatResponse(response=get_response("search_empty", query=user_text), intent="search")
    
    events_list = "\n".join([f"• {r['title']} ({_human_date(r['date'])}{' ' + _human_time(r['time']) if r.get('time') else ''})" for r in results[:5]])
    return ChatResponse(response=get_response("search_found", count=len(results), events_list=events_list), intent="search", data={"results": results})


def handle_upcoming(user_text: str, db: Session) -> ChatResponse:
    text_lower = user_text.lower()
    
    if "прошл" in text_lower and "недел" in text_lower: 
        period = "last_week"
    elif "послезавтра" in text_lower: 
        period = "day_after_tomorrow"
    elif "завтра" in text_lower: 
        period = "tomorrow"
    elif "недел" in text_lower or "ближайш" in text_lower or "вообще" in text_lower: 
        period = "this_week"
    else: 
        period = "today"
    
    event_type_filter = None
    if "по задачам" in text_lower or "по зада" in text_lower or "задач" in text_lower: event_type_filter = "task"
    elif "по встречам" in text_lower or "по встреч" in text_lower or "встреч" in text_lower: event_type_filter = "meeting"
    elif "по напоминаниям" in text_lower or "напоминан" in text_lower: event_type_filter = "reminder"
    
    events = get_upcoming_events(db, period)
    if event_type_filter:
        events = [e for e in events if e.get("type") == event_type_filter]
    
    period_names = {
        "today": "сегодня", 
        "tomorrow": "завтра", 
        "day_after_tomorrow": "послезавтра", 
        "this_week": "этой неделе",
        "last_week": "прошлой неделе"
    }
    
    if not events:
        return ChatResponse(response=get_response("empty", period=period_names.get(period, period)), intent="upcoming")
    
    type_names = {"task": "Задачи", "meeting": "Встречи", "reminder": "Напоминания"}
    type_text = type_names.get(event_type_filter, "События")
    events_list = "\n".join([f"• {e['title']} ({_human_date(e['date'])}{' в ' + _human_time(e['time']) if e.get('time') else ''})" for e in events[:10]])
    
    return ChatResponse(response=get_response("upcoming_list", type_text=type_text, period=period_names.get(period, period), events_list=events_list), intent="upcoming", data={"events": events})


def handle_reschedule(user_text: str, session, db: Session) -> ChatResponse:
    all_events = db.query(Event).filter(Event.status == "pending").all()
    if not all_events:
        return ChatResponse(response="У тебя пока нет событий для переноса.", intent="reschedule", session_id=session.session_id)
    
    from app.llm_service import _get_assistant
    from thefuzz import process, fuzz
    
    event_titles = [e.title for e in all_events]
    best_match = process.extractOne(user_text, event_titles, scorer=fuzz.partial_ratio)
    
    if best_match and best_match[1] > 70:
        matched_title = best_match[0]
        filtered_events = [e for e in all_events if e.title == matched_title]
    else:
        return ChatResponse(
            response="Я не совсем понял, какое именно событие вы хотите перенести. Уточните название, пожалуйста.",
            intent="reschedule",
            session_id=session.session_id
        )
    
    events_list = [{"id": e.id, "title": e.title, "date": e.date, "time": e.time} for e in filtered_events]
    
    assistant = _get_assistant()
    
    system_prompt = f"""Ты - инструмент управления календарем. Извлеки новую дату и время из запроса.

ДОСТУПНОЕ СОБЫТИЕ (или список событий):
{json.dumps(events_list, ensure_ascii=False, indent=2)}

ЗАПРОС ПОЛЬЗОВАТЕЛЯ: "{user_text}"

Верни СТРОГО JSON:
{{
  "event_id": <ID события из списка выше>,
  "new_date_text": "как сказал пользователь (завтра, 30 октября) или null",
  "new_time_text": "как сказал пользователь (в 15:00) или null"
}}
"""
    
    try:
        raw = assistant._call_gigachat(system_prompt, user_text, json_mode=True)
        if not raw: raise ValueError("Пустой ответ")
        
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
        raw = re.sub(r"```", "", raw).strip()
        data = json.loads(raw)
        
        event_id = data.get("event_id")
        new_date_text = data.get("new_date_text")
        new_time_text = data.get("new_time_text")
        
        target_event = next((e for e in all_events if e.id == event_id), None)
        
        if not target_event:
            return ChatResponse(response="Не смог найти это событие. Попробуй назвать его точнее.", intent="reschedule", session_id=session.session_id)
        
        final_date = parse_date_expression(new_date_text) if new_date_text else None
        final_time = parse_time_expression(new_time_text) if new_time_text else None
        
        if not final_date and not final_time:
            session.pending_event = PendingEvent(event_type="reschedule", title=target_event.title, date=target_event.date, time=target_event.time, missing_fields=["date", "time"])
            return ChatResponse(response=f"Нашёл: «{target_event.title}» ({_human_date(target_event.date)} {_human_time(target_event.time)}).\nНа какую дату и время перенести?", intent="reschedule", session_id=session.session_id)
        
        session.pending_event = PendingEvent(
            event_type="reschedule",
            title=target_event.title,
            date=final_date,
            time=final_time,
            missing_fields=[]
        )
        
        return ChatResponse(
            response=f"Нашёл задачу: «{target_event.title}».\nПеренести её на {_human_date(final_date)} {_human_time(final_time)}? (Напиши 'да' или 'нет')",
            intent="reschedule",
            session_id=session.session_id
        )
        
    except Exception as e:
        print(f"Reschedule Error: {e}")
        return ChatResponse(response="Произошла ошибка. Попробуй сказать проще, например: 'Перенеси встречу на завтра'.", intent="reschedule", session_id=session.session_id)


def handle_delete(user_text: str, db: Session) -> ChatResponse:
    all_events = db.query(Event).filter(Event.status == "pending").all()
    if not all_events:
        return ChatResponse(response="У тебя пока нет событий для удаления.", intent="delete")
    
    from thefuzz import process, fuzz
    
    event_titles = [e.title for e in all_events]
    best_match = process.extractOne(user_text, event_titles, scorer=fuzz.partial_ratio)
    
    if best_match and best_match[1] > 70:
        matched_title = best_match[0]
        target_event = next((e for e in all_events if e.title == matched_title), None)
        
        if target_event:
            db.delete(target_event)
            db.commit()
            return ChatResponse(
                response=f"✅ Удалил: «{target_event.title}» ({_human_date(target_event.date)}).",
                intent="delete"
            )
    
    return ChatResponse(
        response="Я не нашел такую задачу. Попробуй назвать её точнее.",
        intent="delete"
    )


def handle_complete(user_text: str, db: Session) -> ChatResponse:
    all_events = db.query(Event).filter(Event.status == "pending").all()
    if not all_events:
        return ChatResponse(response="У тебя нет активных задач.", intent="complete")
    
    from thefuzz import process, fuzz
    
    event_titles = [e.title for e in all_events]
    best_match = process.extractOne(user_text, event_titles, scorer=fuzz.partial_ratio)
    
    if best_match and best_match[1] > 70:
        matched_title = best_match[0]
        target_event = next((e for e in all_events if e.title == matched_title), None)
        
        if target_event:
            target_event.status = "completed"
            db.commit()
            return ChatResponse(
                response=f"✅ Отметил как выполненную: «{target_event.title}».",
                intent="complete"
            )
    
    return ChatResponse(
        response="Не нашел такую задачу.",
        intent="complete"
    )


def _build_clarification_question(missing_fields: list, event_type: str) -> str:
    questions = {
        "date": {"task": "На какую дату поставить задачу?", "meeting": "На какую дату?", "reminder": "Когда напомнить?", "reschedule": "На какую дату перенести?"},
        "time": {"task": "На какое время?", "meeting": "На какое время?", "reminder": "В какое время?", "reschedule": "На какое время?"}
    }
    if len(missing_fields) == 1:
        return questions.get(missing_fields[0], {}).get(event_type, "Уточни, пожалуйста.")
    missing_ru = {"date": "дату", "time": "время"}
    missing_list = " и ".join(missing_ru.get(f, f) for f in missing_fields)
    return f"Укажи, пожалуйста, {missing_list}."


@app.get("/events", response_model=List[EventResponse])
def get_events(skip: int = 0, limit: int = 2000, db: Session = Depends(get_db)): 
    return db.query(Event).order_by(Event.id.desc()).offset(skip).limit(limit).all() 


@app.post("/events", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
def create_event(event: EventCreate, db: Session = Depends(get_db)):
    new_event = Event(event_type=event.event_type, title=event.title, date=event.date, time=event.time, status="pending")
    db.add(new_event)
    db.commit()
    db.refresh(new_event)
    print(f"✅ СОЗДАНА ЗАДАЧА ЧЕРЕЗ API: ID={new_event.id}, Title={new_event.title}")
    return new_event


@app.put("/events/{event_id}/complete", response_model=EventResponse)
def mark_event_complete(event_id: int, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    event.status = "completed"
    db.commit()
    db.refresh(event)
    return event