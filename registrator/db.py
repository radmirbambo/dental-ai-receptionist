"""
db.py — слой базы данных.

Здесь всё, что связано с хранением: клиники, рабочие часы, записи пациентов.

ВАЖНО про выбор БД:
  Для обучения тут используется SQLite — он встроен в Python, не требует
  установки и сервера. Ты запускаешь код и он сразу работает.
  В продакшене (как в нашей архитектуре) это будет PostgreSQL. Переезд лёгкий:
  меняется только строка подключения и мелочи синтаксиса. В самом низу файла
  есть блок-комментарий, что именно поменять на Postgres.
"""

import sqlite3                     # встроенная в Python БД, ставить ничего не нужно
from datetime import datetime, timedelta
from pathlib import Path

# Файл базы кладём рядом с этим скриптом, чтобы путь не зависел от того,
# из какой папки ты запускаешь программу.
DB_PATH = Path(__file__).parent / "clinic.db"

# Рабочие часы клиники. В реальном продукте это лежит в таблице clinics,
# у каждой клиники свои. Для MVP держим константой.
WORK_HOURS = list(range(10, 19))   # 10, 11, 12, ... 18 — приёмы каждый час с 10:00 до 18:00


def get_connection():
    """Открывает соединение с БД.

    row_factory = sqlite3.Row нужен, чтобы обращаться к колонкам по имени
    (row["patient_name"]), а не по числовому индексу — так код читается лучше.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Создаёт таблицу записей, если её ещё нет. Вызывается один раз при старте.

    Обрати внимание на clinic_id: он есть в КАЖДОЙ строке. Это multi-tenant —
    закладка на будущее, чтобы одна и та же база обслуживала много клиник.
    Добавить её потом, когда данные уже накопились, — боль. Закладываем сразу.
    """
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS appointments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,  -- уникальный номер записи
            clinic_id     INTEGER NOT NULL,                   -- какой клинике принадлежит
            patient_name  TEXT    NOT NULL,                   -- имя пациента
            phone         TEXT    NOT NULL,                   -- телефон для связи
            slot          TEXT    NOT NULL,                   -- дата+время в формате 'YYYY-MM-DD HH:00'
            reason        TEXT,                               -- что беспокоит (болит зуб и т.п.)
            created_at    TEXT    NOT NULL                    -- когда создана запись
        )
        """
    )
    conn.commit()      # commit = «зафиксировать изменения», без него в SQLite ничего не сохранится
    conn.close()


def get_booked_slots(clinic_id: int, date: str) -> set[str]:
    """Возвращает множество уже занятых слотов на конкретную дату.

    date — строка вида '2026-07-14'. Слоты в базе хранятся как
    '2026-07-14 15:00', поэтому ищем по префиксу LIKE 'date%'.
    Возвращаем set (множество) — по нему быстро проверять «занято/свободно».
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT slot FROM appointments WHERE clinic_id = ? AND slot LIKE ?",
        (clinic_id, f"{date}%"),          # ? — это плейсхолдеры, защита от SQL-инъекций
    ).fetchall()
    conn.close()
    return {row["slot"] for row in rows}   # берём только значение колонки slot из каждой строки


def free_slots_for_date(clinic_id: int, date: str) -> list[str]:
    """Считает свободные слоты: все рабочие часы МИНУС уже занятые.

    Это и есть бизнес-логика «какие времена свободны». Мозг (LLM) сам её
    не знает — он спросит через инструмент, а мы посчитаем здесь.
    """
    booked = get_booked_slots(clinic_id, date)                 # что уже занято
    all_slots = [f"{date} {hour:02d}:00" for hour in WORK_HOURS]  # 02d = 09, 10, 11 — с ведущим нулём
    return [slot for slot in all_slots if slot not in booked]   # оставляем только свободные


def create_appointment(
    clinic_id: int, patient_name: str, phone: str, slot: str, reason: str
) -> dict:
    """Создаёт запись в БД. Возвращает результат словарём.

    Здесь же — защита от двойной записи на один слот (race condition в жизни
    случается: два пациента звонят одновременно). Проверяем занятость перед вставкой.
    """
    date = slot.split(" ")[0]                                  # '2026-07-14 15:00' -> '2026-07-14'
    if slot in get_booked_slots(clinic_id, date):              # слот уже кто-то занял?
        return {"success": False, "error": "Этот слот только что заняли, предложите другой"}

    conn = get_connection()
    cursor = conn.execute(
        """
        INSERT INTO appointments (clinic_id, patient_name, phone, slot, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (clinic_id, patient_name, phone, slot, reason, datetime.now().isoformat()),
    )
    conn.commit()
    appointment_id = cursor.lastrowid                          # id только что созданной строки
    conn.close()
    return {"success": True, "appointment_id": appointment_id, "slot": slot}


def list_appointments(clinic_id: int) -> list[dict]:
    """Все записи клиники — для Telegram-бота админа (показать календарь)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM appointments WHERE clinic_id = ? ORDER BY slot",
        (clinic_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]                         # превращаем строки БД в обычные словари


# --- маленький помощник, чтобы агенту было проще с датами ---
def resolve_date(word: str) -> str:
    """Переводит человеческие слова в дату 'YYYY-MM-DD'.

    Пациент говорит 'завтра', а не '2026-07-14'. Мозг может передать сюда
    'today'/'tomorrow', а мы вернём конкретную дату. Для остального —
    ожидаем, что придёт уже готовая 'YYYY-MM-DD'.
    """
    today = datetime.now().date()
    if word in ("today", "сегодня"):
        return today.isoformat()
    if word in ("tomorrow", "завтра"):
        return (today + timedelta(days=1)).isoformat()
    return word                                                # уже конкретная дата — возвращаем как есть


# =====================================================================
# КАК ПЕРЕЕХАТЬ НА POSTGRESQL (когда выйдешь из обучения в продакшен):
#   1. pip install psycopg[binary]
#   2. get_connection() -> psycopg.connect("postgresql://user:pass@host/db")
#   3. Плейсхолдеры: SQLite использует ?, а Postgres — %s. Замени ? на %s.
#   4. AUTOINCREMENT -> SERIAL (или GENERATED ... AS IDENTITY).
#   5. lastrowid не работает — используй "INSERT ... RETURNING id".
# Вся остальная логика (свободные слоты, проверки) остаётся без изменений —
# именно поэтому мы держим её здесь, отдельно от агента.
# =====================================================================
