import csv
from app.database import SessionLocal, engine, Base
from app.models import Event

def import_events():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    try:
        with open("events.csv", "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            count = 0
            for row in reader:
                text = row.get('text', '').strip()
                if not text:
                    continue
                
                text_lower = text.lower()
                
                if any(word in text_lower for word in ['встреча', 'созвон', 'планёрка', 'демо', 'обсудить']):
                    event_type = 'meeting'
                elif 'напомнить' in text_lower:
                    event_type = 'reminder'
                else:
                    event_type = 'task'

                date_val = row.get('date', '').strip()
                time_val = row.get('time', '').strip()
                
                if not date_val:
                    continue 
                if not time_val:
                    time_val = None 

                event = Event(
                    event_type=event_type,
                    title=text,
                    date=date_val,
                    time=time_val,
                    status='pending'
                )
                db.add(event)
                count += 1
                
        db.commit()
        print(f"✅ Успешно импортировано {count} событий в базу данных!")
        
    except FileNotFoundError:
        print("❌ Ошибка: Файл 'events.csv' не найден.")
    except Exception as e:
        print(f"❌ Произошла ошибка: {e}")
        db.rollback() 
    finally:
        db.close()

if __name__ == "__main__":
    import_events()