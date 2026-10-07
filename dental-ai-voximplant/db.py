"""
db.py — хранилище MVP: ЛИДЫ (заявки) и ЗАПИСИ на приём.

Флоу продукта исходящий: пришла заявка -> перезваниваем -> записываем.
Поэтому в центре не «слоты», а ЛИД со статусом дозвона:
    new -> calling -> booked | no_answer | failed

SQLite — чтобы демо заводилось на любом ноутбуке без сервера БД.
Переезд на Postgres — см. комментарий внизу (меняется только этот файл).
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "clinic.db"

CLINIC_ID = 1
CLINIC_NAME = "Стоматология «Улыбка»"
WORK_HOURS = list(range(10, 20))     # приёмы 10:00–19:00


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)      # timeout: агент и сервер пишут в один файл
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_id    INTEGER NOT NULL,
            name         TEXT    NOT NULL,
            phone        TEXT    NOT NULL,
            reason       TEXT,                -- что беспокоит (из заявки)
            source       TEXT,                -- откуда заявка: сайт / реклама
            status       TEXT NOT NULL DEFAULT 'new',   -- new/calling/booked/no_answer/failed
            summary      TEXT,                -- краткий итог звонка от агента
            created_at   TEXT NOT NULL,
            called_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS appointments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_id    INTEGER NOT NULL,
            lead_id      INTEGER,             -- из какой заявки родилась запись
            patient_name TEXT NOT NULL,
            phone        TEXT NOT NULL,
            slot         TEXT NOT NULL,       -- 'YYYY-MM-DD HH:00'
            reason       TEXT,
            created_at   TEXT NOT NULL
        );

        -- Реальная таблица окон записи. Заполняется на неделю вперёд (seed_week).
        CREATE TABLE IF NOT EXISTS slots (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_id  INTEGER NOT NULL,
            slot       TEXT NOT NULL,          -- 'YYYY-MM-DD HH:00'
            UNIQUE(clinic_id, slot)            -- одно окно = одна строка
        );
        """
    )
    conn.commit()
    conn.close()
    seed_week()          # наполняем окна на 7 дней вперёд (идемпотентно)


def seed_week(days: int = 7):
    """Заполняет таблицу slots окнами на `days` дней вперёд от сегодня.

    Идемпотентно: INSERT OR IGNORE не создаёт дубликаты, поэтому можно звать
    при каждом старте — новые дни добавятся, старые останутся.
    В реале это расписание пришло бы из МИС; тут просто рабочие часы клиники.
    """
    conn = get_connection()
    today = datetime.now().date()
    rows = []
    for d in range(days):
        day = (today + timedelta(days=d)).isoformat()
        for h in WORK_HOURS:
            rows.append((CLINIC_ID, f"{day} {h:02d}:00"))
    conn.executemany(
        "INSERT OR IGNORE INTO slots (clinic_id, slot) VALUES (?, ?)", rows
    )
    conn.commit()
    conn.close()


# ------------------------- ЛИДЫ -------------------------
def create_lead(name: str, phone: str, reason: str = "", source: str = "сайт") -> int:
    """Сохраняет заявку. Возвращает id лида — по нему потом запустим дозвон."""
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO leads (clinic_id, name, phone, reason, source, status, created_at)
           VALUES (?, ?, ?, ?, ?, 'new', ?)""",
        (CLINIC_ID, name, phone, reason, source, datetime.now().isoformat()),
    )
    conn.commit()
    lead_id = cur.lastrowid
    conn.close()
    return lead_id


def get_lead(lead_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_lead_status(lead_id: int, status: str, summary: str | None = None):
    """Обновляет статус дозвона (агент дёргает это по ходу звонка)."""
    conn = get_connection()
    if summary is not None:
        conn.execute(
            "UPDATE leads SET status = ?, summary = ?, called_at = ? WHERE id = ?",
            (status, summary, datetime.now().isoformat(), lead_id),
        )
    else:
        conn.execute(
            "UPDATE leads SET status = ?, called_at = ? WHERE id = ?",
            (status, datetime.now().isoformat(), lead_id),
        )
    conn.commit()
    conn.close()


def list_leads() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM leads WHERE clinic_id = ? ORDER BY id DESC", (CLINIC_ID,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ------------------------- СЛОТЫ / ЗАПИСИ -------------------------
def _booked_slots(date: str) -> set[str]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT slot FROM appointments WHERE clinic_id = ? AND slot LIKE ?",
        (CLINIC_ID, f"{date}%"),
    ).fetchall()
    conn.close()
    return {r["slot"] for r in rows}


def free_slots_for_date(date: str) -> list[str]:
    """Свободные окна на дату = окна из таблицы slots МИНУС занятые записи,
    минус уже прошедшее время (если дата — сегодня)."""
    booked = _booked_slots(date)
    conn = get_connection()
    rows = conn.execute(
        "SELECT slot FROM slots WHERE clinic_id = ? AND slot LIKE ? ORDER BY slot",
        (CLINIC_ID, f"{date}%"),
    ).fetchall()
    conn.close()
    now = datetime.now()
    free = []
    for r in rows:
        s = r["slot"]
        if s in booked:
            continue
        # не предлагать окна, которые уже прошли сегодня
        if datetime.strptime(s, "%Y-%m-%d %H:%M") <= now:
            continue
        free.append(s)
    return free


def next_available(max_days: int = 14) -> dict:
    """Ближайший день, где есть свободные окна. Возвращает {date, free_slots}.
    Ищет от сегодня вперёд до max_days. Если ничего нет — пустой список."""
    today = datetime.now().date()
    for d in range(max_days):
        date = (today + timedelta(days=d)).isoformat()
        free = free_slots_for_date(date)
        if free:
            return {"date": date, "free_slots": free}
    return {"date": None, "free_slots": []}


def list_slots_week() -> list[dict]:
    """Все окна недели со статусом free/booked — для дашборда/обзора."""
    conn = get_connection()
    slots = [r["slot"] for r in conn.execute(
        "SELECT slot FROM slots WHERE clinic_id = ? ORDER BY slot", (CLINIC_ID,)
    ).fetchall()]
    booked = {r["slot"] for r in conn.execute(
        "SELECT slot FROM appointments WHERE clinic_id = ?", (CLINIC_ID,)
    ).fetchall()}
    conn.close()
    return [{"slot": s, "status": "booked" if s in booked else "free"} for s in slots]


def create_appointment(patient_name, phone, slot, reason="", lead_id=None) -> dict:
    date = slot.split(" ")[0]
    if slot in _booked_slots(date):
        return {"success": False, "error": "Слот только что заняли, предложи другое время"}
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO appointments (clinic_id, lead_id, patient_name, phone, slot, reason, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (CLINIC_ID, lead_id, patient_name, phone, slot, reason, datetime.now().isoformat()),
    )
    conn.commit()
    apt_id = cur.lastrowid
    conn.close()
    return {"success": True, "appointment_id": apt_id, "slot": slot}


def list_appointments() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM appointments WHERE clinic_id = ? ORDER BY slot", (CLINIC_ID,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resolve_date(word: str) -> str:
    """'сегодня'/'завтра'/'today'/'tomorrow' -> 'YYYY-MM-DD'. Иначе — как есть."""
    today = datetime.now().date()
    w = (word or "").strip().lower()
    if w in ("today", "сегодня"):
        return today.isoformat()
    if w in ("tomorrow", "завтра"):
        return (today + timedelta(days=1)).isoformat()
    return word


# =====================================================================
# POSTGRES (в продакшене): pip install psycopg[binary];
# get_connection -> psycopg.connect(DSN); ? -> %s; AUTOINCREMENT -> SERIAL;
# lastrowid -> "INSERT ... RETURNING id". Логика ниже не меняется.
# =====================================================================
