"""
server.py — backend демо на FastAPI. Три роли:
  1. Отдаёт лендинг заявки (/) и дашборд (/dashboard).
  2. Принимает заявку (POST /api/leads) -> сохраняет лид -> запускает исходящий звонок.
  3. Кормит дашборд данными (GET /api/state) и держит ТЕКСТОВЫЙ fallback-режим.

Запуск:  uvicorn server:app --reload --port 8000
Лендинг: http://127.0.0.1:8000/     Дашборд: http://127.0.0.1:8000/dashboard
"""

import os
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db
import brain

logger = logging.getLogger("server")
WEB = Path(__file__).parent / "web"

app = FastAPI(title="Dental AI — исходящий оператор (MVP)")


@app.on_event("startup")
def _startup():
    db.init_db()


# ------------------------- статические страницы -------------------------
@app.get("/")
def landing():
    return FileResponse(WEB / "index.html")


@app.get("/dashboard")
def dashboard():
    return FileResponse(WEB / "dashboard.html")


@app.get("/voice")
def voice_page():
    return FileResponse(WEB / "voice.html")


# ------------------------- приём заявки + дозвон -------------------------
class LeadIn(BaseModel):
    name: str
    phone: str
    reason: str = ""
    source: str = "сайт"


@app.post("/api/leads")
async def create_lead(lead: LeadIn):
    """Оставить заявку. Сразу сохраняем и пытаемся запустить исходящий звонок.
    Если телефония не настроена — лид всё равно сохраняется (демо не падает)."""
    lead_id = db.create_lead(lead.name, lead.phone, lead.reason, lead.source)
    call_started = False
    try:
        from dispatch import start_outbound_call
        await start_outbound_call(lead_id, lead.phone)
        call_started = True
    except Exception as e:
        logger.warning(f"outbound call not started (lead {lead_id}): {e}")
    return {"lead_id": lead_id, "call_started": call_started}


@app.get("/api/state")
def state():
    """Данные для дашборда: лиды (со статусами дозвона), записи и окна недели."""
    return {
        "leads": db.list_leads(),
        "appointments": db.list_appointments(),
        "slots": db.list_slots_week(),
    }


# ------------------------- ГОЛОС В БРАУЗЕРЕ (без SIP) -------------------------
@app.post("/api/leads/{lead_id}/voice-token")
async def voice_token(lead_id: int):
    """Готовит голосовой звонок через браузер:
      1) запускает агента в комнату call-<lead_id> (mode=browser, без SIP);
      2) выдаёт браузеру токен, чтобы подключить микрофон к той же комнате.
    """
    lead = db.get_lead(lead_id)
    if not lead:
        return {"error": "lead not found"}

    from livekit import api
    from dispatch import start_browser_call

    room = await start_browser_call(lead_id)      # агент заходит в комнату и заговорит первым

    token = (
        api.AccessToken(os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET"))
        .with_identity(f"patient-{lead_id}")
        .with_name(lead["name"])
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )
    return {"url": os.getenv("LIVEKIT_URL"), "token": token, "room": room}


# ------------------------- ТЕКСТОВЫЙ fallback -------------------------
# Память диалогов по лидам (живёт в процессе — для демо достаточно).
_SESSIONS: dict[int, list] = {}


class ChatIn(BaseModel):
    text: str = ""


@app.post("/api/leads/{lead_id}/chat")
def chat(lead_id: int, payload: ChatIn):
    """Тот же агент, но текстом — запасной канал для демо, если звонок недоступен.
    Пустой text => агент здоровается первым (как будто взяли трубку)."""
    lead = db.get_lead(lead_id)
    if not lead:
        return {"reply": "Лид не найден"}
    if lead_id not in _SESSIONS:
        _SESSIONS[lead_id] = brain.create_conversation(lead)
        db.update_lead_status(lead_id, "calling")
    user_text = payload.text or "(пациент взял трубку, поздоровайся первым)"
    reply = brain.run_turn(_SESSIONS[lead_id], user_text, lead)
    return {"reply": reply}
