"""
llm_service.py - Мозг голосового ассистента (Final Version).
GigaChat только извлекает текст, все даты считает Python.
Поддерживает: создание, поиск, перенос, удаление, отметку выполненной.
"""

import json
import re
import logging
import os
import uuid
import calendar
from datetime import datetime, timedelta
from typing import Optional, List, Dict

import dateparser
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY")
if not GIGACHAT_AUTH_KEY:
    raise ValueError("GIGACHAT_AUTH_KEY не найден в .env")

TOKEN_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

_access_token: Optional[str] = None
_token_expires_at: float = 0

ACTION_VERBS = [
    "напомни", "напомнить", "поставь", "поставить", "добавь", "добавить",
    "создай", "создать", "запиши", "записать", "запланируй", "запланировать",
    "хочу", "нужно", "надо", "сделай", "сделать"
]

RESCHEDULE_VERBS = [
    "перенеси", "перенести", "перемести", "переместить", "измени дату",
    "можешь перенести", "перенесем", "перенесём", "перенесите",
    "переведи", "перевести", "сдвинь", "сдвинуть", "переставь", "переставить",
    "передвинь", "передвинуть", "поменяй", "поменять", "перенос"
]


def _get_access_token() -> str:
    global _access_token, _token_expires_at
    
    if _access_token and datetime.now().timestamp() < _token_expires_at - 60:
        return _access_token
    
    try:
        headers = {
            "Authorization": f"Basic {GIGACHAT_AUTH_KEY}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded"
        }
        with httpx.Client(verify=False, timeout=10.0) as client:
            response = client.post(TOKEN_URL, headers=headers, data={"scope": "GIGACHAT_API_PERS"})
            response.raise_for_status()
            data = response.json()
            _access_token = data["access_token"]
            _token_expires_at = datetime.now().timestamp() + data.get("expires_at", 3600)
            return _access_token
    except Exception as e:
        logger.error(f"Ошибка получения токена GigaChat: {e}")
        raise


TIME_SYNONYMS = {
    "утром": "09:00", "утра": "09:00", "днём": "14:00", "дня": "14:00",
    "вечером": "18:00", "вечера": "18:00", "ночью": "22:00", "ночи": "22:00",
    "в обед": "13:00", "обед": "13:00", "полдень": "12:00", "полночь": "00:00",
}
DATE_PATTERNS = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12
}


def parse_time_expression(time_str: str) -> Optional[str]:
    if not time_str: return None
    time_str = time_str.strip().lower()
    
    match = re.match(r"(\d{1,2})[:.](\d{2})", time_str)
    if match:
        h, m = int(match.group(1)), int(match.group(2))
        if 0 <= h <= 23 and 0 <= m <= 59: return f"{h:02d}:{m:02d}"
    
    match = re.match(r"^(\d{1,2})$", time_str)
    if match:
        h = int(match.group(1))
        if 0 <= h <= 23: return f"{h:02d}:00"
    
    match = re.match(r"через\s+(\d+)\s+(час|часа|часов)", time_str)
    if match: return (datetime.now() + timedelta(hours=int(match.group(1)))).strftime("%H:%M")
    
    match = re.match(r"через\s+(\d+)\s+(минуту|минуты|минут)", time_str)
    if match: return (datetime.now() + timedelta(minutes=int(match.group(1)))).strftime("%H:%M")
    
    for syn, default in TIME_SYNONYMS.items():
        if syn in time_str:
            h_match = re.search(r"(\d{1,2})", time_str)
            if h_match:
                h = int(h_match.group(1))
                if syn in ["вечером", "вечера", "ночью", "ночи"] and h < 12: h += 12
                return f"{h:02d}:00"
            return default
    return None


def parse_date_expression(date_str: str) -> Optional[str]:
    if not date_str: return None
    date_str = date_str.strip().lower()
    
    for word, offset in DATE_PATTERNS.items():
        if word in date_str:
            return (datetime.now().date() + timedelta(days=offset)).strftime("%Y-%m-%d")
    
    match = re.match(r"через\s+(\d+)\s+(день|дня|дней)", date_str)
    if match:
        return (datetime.now().date() + timedelta(days=int(match.group(1)))).strftime("%Y-%m-%d")
    
    match = re.search(r"(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)", date_str)
    if match:
        day, month = int(match.group(1)), MONTHS_RU[match.group(2)]
        year = datetime.now().year
        try:
            target = datetime(year, month, day).date()
            if target < datetime.now().date(): target = datetime(year + 1, month, day).date()
            return target.strftime("%Y-%m-%d")
        except ValueError: pass
    
    match = re.search(r"(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})", date_str)
    if match:
        d, m, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if y < 100: y += 2000
        try: return datetime(y, m, d).date().strftime("%Y-%m-%d")
        except ValueError: pass
    
    weekdays = {"понедельник": 0, "вторник": 1, "среда": 2, "среду": 2, "четверг": 3, "четвергу": 3,
                "пятница": 4, "пятницу": 4, "суббота": 5, "субботу": 5, "воскресенье": 6, "воскресению": 6}
    for day_name, day_num in weekdays.items():
        if day_name in date_str:
            today = datetime.now().date()
            days_ahead = day_num - today.weekday()
            if days_ahead <= 0: days_ahead += 7
            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    
    if "следующей недел" in date_str:
        today = datetime.now().date()
        return (today + timedelta(days=(7 - today.weekday()) % 7 or 7) + timedelta(days=7)).strftime("%Y-%m-%d")
    
    if "этой недел" in date_str or "на недел" in date_str:
        today = datetime.now().date()
        return (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    
    if "до конца недели" in date_str or "к концу недели" in date_str:
        today = datetime.now().date()
        days_ahead = 6 - today.weekday()
        if days_ahead <= 0: days_ahead += 7
        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    
    if "к концу месяца" in date_str or "до конца месяца" in date_str:
        today = datetime.now().date()
        last_day = calendar.monthrange(today.year, today.month)[1]
        return datetime(today.year, today.month, last_day).date().strftime("%Y-%m-%d")
    
    parsed = dateparser.parse(date_str, languages=["ru"], settings={"RELATIVE_BASE": datetime.now()})
    if parsed: return parsed.strftime("%Y-%m-%d")
    
    return None


class LLMAssistant:
    def _call_gigachat(self, system_prompt: str, user_text: str, history: Optional[List[Dict]] = None, json_mode: bool = False) -> Optional[str]:
        try:
            token = _get_access_token()
            messages = [{"role": "system", "content": system_prompt}]
            
            if history:
                for msg in history[-6:]:
                    messages.append({"role": msg["role"], "content": msg["content"]})
            
            user_content = user_text
            if json_mode:
                user_content += "\n\nОтвечай СТРОГО валидным JSON, без markdown-обёрток (без ```json)."
            
            messages.append({"role": "user", "content": user_content})
            
            payload = {
                "model": "GigaChat:latest",
                "messages": messages,
                "temperature": 0.1 if json_mode else 0.3,
                "max_tokens": 512
            }
            
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            
            with httpx.Client(verify=False, timeout=30.0) as client:
                response = client.post(CHAT_URL, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"GigaChat API error: {e}")
            return None

    def classify_intent(self, user_text: str, history: Optional[List[Dict]] = None) -> str:
        system_prompt = """Ты - классификатор намерений. Твоя задача - понять, что хочет пользователь.

ВАРИАНТЫ:
1. "create" - Пользователь ДИКТУЕТ событие. Примеры: "встреча с дизайнером в четверг в 11", "завтра в 10 позвонить маме", "добавь задачу купить хлеб".
2. "upcoming" - Пользователь СПРАШИВАЕТ, что у него есть. Примеры: "что у меня сегодня?", "какие планы на завтра?", "что на неделе?"
3. "search" - Пользователь просит НАЙТИ. Примеры: "найди задачу про презентацию", "когда была встреча с Иваном?"
4. "reschedule" - Пользователь просит ПЕРЕНЕСТИ. Примеры: "перенеси встречу на завтра", "сдвинь задачу на час"
5. "delete" - Пользователь просит УДАЛИТЬ. Примеры: "удали задачу про маму", "убери встречу с Иваном"
6. "complete" - Пользователь просит ОТМЕТИТЬ ВЫПОЛНЕННОЙ. Примеры: "отметь задачу как выполненную", "я сделал это", "задача выполнена"
7. "clarification" - Короткий ответ на уточняющий вопрос. Примеры: "в 14:00", "завтра", "да", "нет".

ВЕРНИ ТОЛЬКО ОДНО СЛОВО ИЗ СПИСКА ВЫШЕ."""
        
        result = self._call_gigachat(system_prompt, user_text, history, json_mode=False)
        
        if result:
            intent = result.strip().lower().rstrip(".").strip('"').strip()
            if intent in ("create", "search", "upcoming", "reschedule", "clarification", "delete", "complete"):
                text_lower = user_text.lower()
                
                if any(w in text_lower for w in ["удали", "убери", "сотри", "удалить"]):
                    return "delete"
                
                if any(w in text_lower for w in ["выполнено", "выполнил", "сделал", "сделано", "готово", "отметь"]):
                    return "complete"
                
                if any(marker in text_lower for marker in ["найди", "покажи", "когда", "что было", "какие задачи", "какие встречи"]):
                    return "search"
                
                if any(w in text_lower for w in RESCHEDULE_VERBS) or re.search(r"перенес[ттьи]|перевед[иь]|сдвин[ьь]|перестав[ьь]|поменя[йй]", text_lower):
                    return "reschedule"
                
                if any(w in text_lower for w in ACTION_VERBS):
                    return "create"
                
                return intent
        
        return self._heuristic_intent(user_text)

    def _heuristic_intent(self, user_text: str) -> str:
        text = user_text.lower()
        
        if any(w in text for w in ["удали", "убери", "сотри", "удалить"]):
            return "delete"
        
        if any(w in text for w in ["выполнено", "выполнил", "сделал", "сделано", "готово", "отметь"]):
            return "complete"
        
        if any(w in text for w in ["найди", "покажи", "когда", "что было", "какие задачи", "какие встречи"]) or "что с " in text or "как дела с " in text:
            return "search"
        
        if any(w in text for w in RESCHEDULE_VERBS) or re.search(r"перенес[ттьи]|перевед[иь]|сдвин[ьь]|перестав[ьь]|поменя[йй]", text):
            return "reschedule"
            
        if "у меня" in text and ("встреча" in text or "созвон" in text) and re.search(r"\d{1,2}[:.]\d{2}", text):
            return "create"
            
        if any(w in text for w in ACTION_VERBS):
            return "create"
            
        if any(w in text for w in ["что у меня", "какие планы", "есть ли у меня", "че завтра", "что на неделе", "ближайшее время", "ближайшие события", "ближайш"]):
            return "upcoming"
            
        return "unknown"

    def extract_event(self, user_text: str, history: Optional[List[Dict]] = None) -> dict:
        system_prompt = """Ты - помощник-планировщик. Извлеки данные о событии в JSON.

ПОЛЯ:
- event_type: "task" (задача), "meeting" (встреча/созвон), "reminder" (напоминание)
- title: 3-6 слов, сохрани смысл
- date_text: КАК ПОЛЬЗОВАТЕЛЬ СКАЗАЛ ДАТУ (например: "завтра", "в пятницу", "26 октября", "до конца недели"). Если даты нет - null.
- time_text: КАК ПОЛЬЗОВАТЕЛЬ СКАЗАЛ ВРЕМЯ (например: "в 10 утра", "в 15:00"). Если времени нет - null.
- needs_clarification: true, если не хватает критичных данных
- clarification_question: короткий естественный вопрос (без markdown, без кавычек)

КОНТЕКСТ ДИАЛОГА:
Если передана история и пользователь отвечает на уточняющий вопрос - возьми title и event_type из истории, а из текущей фразы извлеки только дату/время.

Отвечай СТРОГО валидным JSON, без markdown-обёрток."""
        
        raw = self._call_gigachat(system_prompt, user_text, history, json_mode=True)
        if not raw: return self._fallback_extract(user_text)
        
        try:
            raw = re.sub(r"```(?:json)?\s*", "", raw)
            raw = re.sub(r"```", "", raw).strip()
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"GigaChat вернул мусор: {raw[:100]}")
            return self._fallback_extract(user_text)
        
        return self._normalize_extracted_data(data, user_text)

    def extract_reschedule_info(self, user_text: str, event_title: str, event_date: str, event_time: str) -> dict:
        system_prompt = f"""Пользователь хочет перенести событие: "{event_title}" (сейчас: {event_date} {event_time or 'без времени'}).

Из фразы пользователя извлеки ТОЛЬКО новую дату и время:
"{user_text}"

Верни JSON:
{{"date_text": "текст даты или null", "time_text": "текст времени или null"}}

Отвечай СТРОГО JSON, без markdown."""
        
        raw = self._call_gigachat(system_prompt, user_text, json_mode=True)
        if not raw:
            return {"date": None, "time": None}
        
        try:
            raw = re.sub(r"```(?:json)?\s*", "", raw)
            raw = re.sub(r"```", "", raw).strip()
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"GigaChat вернул мусор при reschedule: {raw[:100]}")
            return {"date": None, "time": None}
        
        date = parse_date_expression(data.get("date_text")) if data.get("date_text") else None
        time = parse_time_expression(data.get("time_text")) if data.get("time_text") else None
        
        return {
            "date": date,
            "time": time
        }

    def _normalize_extracted_data(self, data: dict, original_text: str) -> dict:
        event_type = data.get("event_type", "task").lower()
        if event_type not in ("task", "meeting", "reminder"):
            tl = original_text.lower()
            if any(w in tl for w in ["встреч", "созвон", "планёрк"]): event_type = "meeting"
            elif any(w in tl for w in ["напомни", "напомнить"]): event_type = "reminder"
            else: event_type = "task"
        
        date_text = data.get("date_text")
        time_text = data.get("time_text")
        
        date = parse_date_expression(date_text) if date_text else None
        time = parse_time_expression(time_text) if time_text else None
        
        needs_clarification = data.get("needs_clarification", False)
        clarification_question = data.get("clarification_question", "")
        missing_fields = data.get("missing_fields", [])
        
        if event_type in ("meeting", "reminder"):
            if not date or not time:
                needs_clarification = True
                missing_fields = []
                if not date: missing_fields.append("date")
                if not time: missing_fields.append("time")
                if not clarification_question:
                    if not date and not time: clarification_question = "На какую дату и время?"
                    elif not date: clarification_question = "На какую дату?"
                    else: clarification_question = "На какое время?"
        elif event_type == "task":
            if not date:
                needs_clarification = True
                missing_fields = ["date"]
                if not clarification_question:
                    clarification_question = "На какую дату поставить задачу?"
        
        return {
            "event_type": event_type,
            "title": data.get("title", original_text),
            "date": date,
            "time": time,
            "needs_clarification": needs_clarification,
            "clarification_question": clarification_question,
            "missing_fields": missing_fields
        }

    def _fallback_extract(self, user_text: str) -> dict:
        text = user_text.lower()
        event_type = "meeting" if any(w in text for w in ["встреч", "созвон"]) else ("reminder" if any(w in text for w in ["напомни", "напомнить"]) else "task")
        time_match = re.search(r"(\d{1,2})[:.](\d{2})", text)
        time = f"{int(time_match.group(1)):02d}:{time_match.group(2)}" if time_match else None
        
        date = None
        for word, offset in DATE_PATTERNS.items():
            if word in text:
                date = (datetime.now().date() + timedelta(days=offset)).strftime("%Y-%m-%d")
                break
                
        return {
            "event_type": event_type, "title": user_text, "date": date, "time": time,
            "needs_clarification": not date, "clarification_question": "На какую дату?", "missing_fields": ["date"] if not date else []
        }

    def generate_clarification(self, missing_fields: list, event_type: str) -> str:
        q = {"date": {"task": "На какую дату?", "meeting": "На какую дату?", "reminder": "Когда напомнить?"},
             "time": {"task": "На какое время?", "meeting": "На какое время?", "reminder": "В какое время?"}}
        if len(missing_fields) == 1:
            return q.get(missing_fields[0], {}).get(event_type, "Уточни детали.")
        return "Не хватает информации. Уточни, пожалуйста."


_assistant: Optional[LLMAssistant] = None
def _get_assistant() -> LLMAssistant:
    global _assistant
    if _assistant is None: _assistant = LLMAssistant()
    return _assistant

def determine_intent(user_text: str, history: Optional[List[Dict]] = None) -> str:
    return _get_assistant().classify_intent(user_text, history)

def extract_event_info(user_text: str, history: Optional[List[Dict]] = None) -> dict:
    return _get_assistant().extract_event(user_text, history)

def extract_reschedule_info(user_text: str, event_title: str, event_date: str, event_time: str) -> dict:
    return _get_assistant().extract_reschedule_info(user_text, event_title, event_date, event_time)