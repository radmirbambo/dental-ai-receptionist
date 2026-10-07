"""
main.py — тот же агент, но обёрнутый в HTTP-API на FastAPI.

Зачем: в нашей архитектуре именно так голосовой оркестратор (LiveKit/Pipecat)
будет общаться с мозгом — он шлёт распознанный текст пациента на HTTP-эндпоинт
и получает ответ, который потом озвучит через TTS. То есть это тот самый
"FastAPI — инструменты/агент" из схемы. Голос ничего не знает про логику —
он просто дёргает этот API.

Запуск:  uvicorn main:app --reload
Проверка: открой http://127.0.0.1:8000/docs — там интерактивная форма.

ВАЖНО про состояние: тут разговоры хранятся в памяти процесса (dict SESSIONS).
Для обучения ок. В продакшене память разговора кладут в Redis или БД,
чтобы она пережила перезапуск и работала на нескольких серверах.
"""

from fastapi import FastAPI
from pydantic import BaseModel

import db
import agent

app = FastAPI(title="Агент-регистратор стоматологии")

# session_id -> messages (память конкретного звонка). Живёт только пока запущен процесс.
SESSIONS: dict[str, list] = {}


@app.on_event("startup")
def startup():
    """При старте сервера создаём таблицу, если её ещё нет."""
    db.init_db()


class ChatIn(BaseModel):
    """Входные данные эндпоинта /chat. Pydantic сам проверит типы."""
    session_id: str          # идентификатор звонка/разговора
    text: str                # распознанная реплика пациента (то, что дал STT)


class ChatOut(BaseModel):
    reply: str               # ответ агента (его отдадут в TTS для озвучки)


@app.post("/chat", response_model=ChatOut)
def chat(payload: ChatIn):
    """Одна реплика диалога. Оркестратор зовёт этот эндпоинт на каждую фразу пациента."""
    # Находим память этого звонка или заводим новую, если звонок только начался.
    if payload.session_id not in SESSIONS:
        SESSIONS[payload.session_id] = agent.create_conversation()

    messages = SESSIONS[payload.session_id]
    reply = agent.run_turn(messages, payload.text)   # тот же самый цикл, что и в CLI
    return ChatOut(reply=reply)


@app.get("/appointments")
def appointments():
    """Список записей — это данные для Telegram-бота админа (календарь)."""
    return db.list_appointments(clinic_id=1)
