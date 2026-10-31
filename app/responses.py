import random
import re
from datetime import datetime


def _human_date(date_str: str) -> str:
    if not date_str: return ""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now().date()
        diff = (dt - today).days
        if diff == 0: return "сегодня"
        if diff == 1: return "завтра"
        if diff == 2: return "послезавтра"
        if diff == -1: return "вчера"
        weekdays = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
        return f"{dt.day} {months[dt.month - 1]} ({weekdays[dt.weekday()]})"
    except:
        return date_str


def _human_time(time_str: str) -> str:
    """Возвращает время БЕЗ предлога 'в' - шаблон сам добавит 'в'."""
    if not time_str: return ""
    try:
        h, m = map(int, time_str.split(":"))
        if m == 0:
            if h == 0: return "полночь"
            if h == 12: return "полдень"
            return f"{h} часов"
        elif m == 30:
            return f"{h} тридцать"
        else:
            return f"{h} {m:02d}"
    except:
        return time_str


def get_response(template_key: str, **kwargs) -> str:
    kwargs["human_date"] = _human_date(kwargs.get("date")) if kwargs.get("date") else ""
    kwargs["human_time"] = _human_time(kwargs.get("time")) if kwargs.get("time") else ""
    kwargs["new_human_date"] = _human_date(kwargs.get("new_date")) if kwargs.get("new_date") else ""
    kwargs["new_human_time"] = _human_time(kwargs.get("new_time")) if kwargs.get("new_time") else ""
    
    templates = RESPONSES.get(template_key, RESPONSES["unknown"])
    response = random.choice(templates).format(**kwargs)
    
    return re.sub(r'[ \t]+', ' ', response).strip()


RESPONSES = {
    "created": ["Записал! {title} — {human_date} {human_time}.", "Готово, добавил: {title} на {human_date} {human_time}."],
    "created_task": ["Задача '{title}' на {human_date} {human_time} — записано!", "Добавил задачу: {title} на {human_date} {human_time}."],
    "created_meeting": ["Встреча '{title}' запланирована на {human_date} {human_time}.", "Записал встречу: {title} — {human_date} {human_time}."],
    "created_reminder": ["Напомню: {title} — {human_date} {human_time}.", "Ок, напомню о '{title}' {human_date} {human_time}."],
    "empty": ["На {period} пока пусто. Может, что-то запланируем?", "На {period} ничего нет — свободное время!"],
    "moved": ["Перенёс: {title} теперь {new_human_date} {new_human_time}.", "Готово! {title} перенесён на {new_human_date} {new_human_time}."],
    "not_found": ["Не нашёл '{title}'. Вот что есть:\n{events_list}\nУточни?", "Такого события нет. Может, одно из этих:\n{events_list}?"],
    "no_events": ["У тебя вообще нет событий для переноса.", "Пока нечего переносить — расписание пустое."],
    "unknown": ["Не совсем понял. Можешь сказать иначе?", "Не расслышал. Попробуй, например: 'Напомни завтра купить молоко'."],
    "clarification_ask": ["{question}", "Уточни, пожалуйста: {question}"],
    "search_found": ["Нашёл {count} событий:\n{events_list}", "Вот что нашёл ({count}):\n{events_list}"],
    "search_empty": ["Ничего не нашёл по запросу '{query}'.", "По '{query}' ничего нет."],
    "upcoming_list": ["{type_text} на {period}:\n{events_list}", "На {period} у тебя:\n{events_list}"],
    "welcome": ["Привет! Я готов помочь. Скажи или напиши, что нужно сделать.", "Привет! Чем могу помочь?"]
}