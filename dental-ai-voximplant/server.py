"""
server.py — бэкенд Voximplant-версии AI-оператора.

Отличие от LiveKit-версии: голосовой слой (телефония, ASR/TTS, сценарий звонка)
живёт в облаке Voximplant. Наш бэкенд — «мозг и память»: LLM-диалог, слоты,
запись, дашборд. Сценарий Voximplant дёргает нас по HTTP на каждый ход диалога.

Эндпоинты для Voximplant:
    POST /api/vox/turn   {lead_id, text}            -> {reply, end_call}
    POST /api/vox/event  {lead_id, status, summary} -> статусы дозвона на дашборд

Запуск:  uvicorn server:app --reload --port 8000
ВАЖНО: бэкенд должен быть доступен из интернета (облако Voximplant должно до него
достучаться) — для локального демо поднимите туннель (ngrok / localtunnel), URL
пропишите в PUBLIC_BACKEND_URL.
"""

import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db
import brain

logger = logging.getLogger("vox-server")
WEB = Path(__file__).parent / "web"

app = FastAPI(title="Dental AI — оператор на Voximplant (MVP)")

# память диалогов по лидам (для демо достаточно; в проде — Redis)
_SESSIONS: dict[int, list] = {}

FAREWELLS = ("до свидания", "ждём вас", "ждем вас", "всего доброго", "хорошего дня")


@app.on_event("startup")
def _startup():
    db.init_db()


# ------------------------- страницы -------------------------
@app.get("/")
def landing():
    return FileResponse(WEB / "index.html")


@app.get("/dashboard")
def dashboard():
    return FileResponse(WEB / "dashboard.html")


# ------------------------- заявки -------------------------
class LeadIn(BaseModel):
    name: str
    phone: str
    reason: str = ""
    source: str = "сайт"


def _try_start_call(lead_id: int) -> bool:
    """Запуск звонка через Voximplant. Без ключей просто вернёт False (демо не падает)."""
    if not os.getenv("VOX_ACCOUNT_ID"):
        return False
    try:
        from vox_dispatch import start_outbound_call
        start_outbound_call(db.get_lead(lead_id))
        return True
    except Exception as e:
        logger.warning(f"vox call not started (lead {lead_id}): {e}")
        return False


@app.post("/api/leads")
async def create_lead(lead: LeadIn):
    lead_id = db.create_lead(lead.name, lead.phone, lead.reason, lead.source)
    started = await asyncio.to_thread(_try_start_call, lead_id)
    return {"lead_id": lead_id, "call_started": started}


@app.post("/api/leads/{lead_id}/call")
async def recall(lead_id: int):
    _SESSIONS.pop(lead_id, None)           # повторный дозвон — тоже с чистого листа
    started = await asyncio.to_thread(_try_start_call, lead_id)
    return {"call_started": started}


@app.get("/api/state")
def state():
    return {
        "leads": db.list_leads(),
        "appointments": db.list_appointments(),
        "slots": db.list_slots_week(),
    }


# ------------------------- ходы диалога от Voximplant -------------------------
class InboundIn(BaseModel):
    phone: str = ""


def _lead_by_phone(phone: str) -> dict | None:
    """Последний лид с таким номером (для входящих звонков)."""
    for lead in db.list_leads():        # список отсортирован по id DESC
        if lead["phone"] == phone:
            return lead
    return None


@app.post("/api/vox/inbound")
def vox_inbound(payload: InboundIn):
    """Входящий звонок: находим лид по номеру или заводим новый.

    Важно: каждый звонок — НОВЫЙ разговор (память прошлой сессии сбрасывается),
    а если по номеру уже есть завершённая запись — создаём новый лид: повторный
    звонок это новое обращение, а не продолжение старого."""
    phone = payload.phone or "неизвестен"
    lead = _lead_by_phone(phone)
    if not lead or lead.get("status") == "booked":
        lead_id = db.create_lead("Гость", phone, "входящий звонок", source="входящий")
        lead = db.get_lead(lead_id)
    _SESSIONS.pop(lead["id"], None)        # свежий диалог: без памяти прошлого звонка
    db.update_lead_status(lead["id"], "calling")
    return {"lead_id": lead["id"]}


class TurnIn(BaseModel):
    lead_id: int | None = None
    phone: str = ""
    text: str


@app.post("/api/vox/turn")
def vox_turn(payload: TurnIn):
    """Один ход диалога: текст пациента (ASR Voximplant) -> ответ агента (текст для TTS).
    Лид ищем по id, а если его нет (гонка на входящем) — по номеру телефона."""
    lead = db.get_lead(payload.lead_id) if payload.lead_id else None
    if not lead and payload.phone:
        lead = _lead_by_phone(payload.phone)
    if not lead:
        return {"reply": "Извините, произошла ошибка. До свидания.", "end_call": True}
    payload.lead_id = lead["id"]
    if payload.lead_id not in _SESSIONS:
        _SESSIONS[payload.lead_id] = brain.create_conversation(lead)
    reply = brain.run_turn(_SESSIONS[payload.lead_id], payload.text, lead)

    # Эвристика завершения: пациент записан и агент прощается -> кладём трубку.
    booked = (db.get_lead(payload.lead_id) or {}).get("status") == "booked"
    end_call = booked and any(f in reply.lower() for f in FAREWELLS)
    return {"reply": reply, "end_call": end_call}


class EventIn(BaseModel):
    lead_id: int
    status: str
    summary: str = ""


@app.post("/api/vox/event")
def vox_event(payload: EventIn):
    """Статусы дозвона из сценария (calling / no_answer / failed) -> на дашборд."""
    if payload.status in ("calling", "no_answer", "failed"):
        db.update_lead_status(payload.lead_id, payload.status, payload.summary or None)
    return {"ok": True}


# ------------------------- текстовый fallback (как в LiveKit-версии) -------------------------
class ChatIn(BaseModel):
    text: str = ""


@app.post("/api/leads/{lead_id}/chat")
def chat(lead_id: int, payload: ChatIn):
    lead = db.get_lead(lead_id)
    if not lead:
        return {"reply": "Лид не найден"}
    if lead_id not in _SESSIONS:
        _SESSIONS[lead_id] = brain.create_conversation(lead)
        db.update_lead_status(lead_id, "calling")
    user_text = payload.text or "(пациент взял трубку, поздоровайся первым)"
    reply = brain.run_turn(_SESSIONS[lead_id], user_text, lead)
    return {"reply": reply}
