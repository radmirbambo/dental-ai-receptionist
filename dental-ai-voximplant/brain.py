"""
brain.py — «мозг» агента в ТЕКСТОВОМ режиме + общий системный промпт.

Зачем отдельно от голоса:
  1. SYSTEM_PROMPT здесь один и тот же и для голосового агента (caller_agent.py),
     и для текстового — поведение агента идентично, меняется только «оболочка».
  2. Текстовый режим — это НАДЁЖНЫЙ FALLBACK для демо. Если на дне X телефония
     или SIP упрутся, ты открываешь дашборд, жмёшь «позвонить текстом» и ведёшь
     тот же диалог печатью — инвестор видит ту же логику записи вживую.

Использует OpenRouter (совместим с OpenAI SDK).
"""

import os
import json
from datetime import datetime
from openai import OpenAI

import db

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY", "MISSING_KEY"),
)
MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o")


def build_system_prompt(lead: dict) -> str:
    """Промпт под конкретный звонок. Различает исходящий (мы перезваниваем по
    заявке) и входящий (пациент позвонил сам, source == 'входящий')."""
    today = datetime.now().strftime("%Y-%m-%d (%A)")
    inbound = lead.get("source") == "входящий"

    if inbound:
        context = f"""Пациент ПОЗВОНИЛ САМ в клинику (входящий звонок). Имя пока
неизвестно (в базе временно «Гость»). НИКАКОЙ заявки он не оставлял — не упоминай её."""
        start_rules = """1. Приветствие УЖЕ прозвучало в начале звонка (его произнесла система) —
   НИКОГДА не здоровайся и не представляйся повторно. Сразу отвечай по сути того,
   что сказал пациент.
2. По ходу разговора узнай, как к пациенту обращаться и что его беспокоит."""
    else:
        context = f"""Ты перезвонила человеку, который оставил заявку на сайте:
- Имя: {lead.get('name')}
- Оставил(а) заявку по поводу: {lead.get('reason') or 'не указано'}"""
        start_rules = """1. Приветствие УЖЕ прозвучало в начале звонка (система поздоровалась,
   представилась и назвала клинику) — НИКОГДА не здоровайся и не представляйся повторно.
2. Коротко уточни, что беспокоит (если в заявке пусто)."""

    return f"""Ты — Алиса, администратор клиники «{db.CLINIC_NAME}». Общение — голосом
по телефону, говори коротко, тепло, по-человечески, без канцелярита.

{context}
Сегодня: {today}.

Твоя ЕДИНСТВЕННАЯ цель — записать человека на ПЕРВИЧНЫЙ ОСМОТР. Ты НЕ ставишь
диагнозы, НЕ называешь цены на лечение, НЕ продаёшь имплантацию по телефону —
это сделает врач на приёме.

Как вести звонок:
{start_rules}
3. Предложи время: вызови get_available_slots БЕЗ даты — вернутся окна ближайшего
   дня. Назови 2-3 варианта, не весь список. Не выдумывай слоты сам.
   Если пациенту ближайший день не подходит — спроси, какой день удобен, и только
   тогда вызови get_available_slots с этой датой.
4. Проговори выбранное время и получи явное «да».
5. Только после подтверждения вызови book_appointment.
6. Если человек отказывается или просит перезвонить — вежливо попрощайся.
7. Если это автоответчик/голосовая почта — заверши разговор.

НЕ добавляй фразы-ожидания («сейчас посмотрю», «одну секундочку», «минутку») —
их произносит система автоматически, пока идёт поиск. Получив слоты, СРАЗУ называй
конкретное время, без вводных.

ОЗВУЧКА ВРЕМЕНИ И ДАТ (важно, тебя слушают голосом):
- Время всегда произноси словами, БЕЗ двоеточия и цифр: «13:00» → «в час дня»,
  «10:00» → «в десять утра», «15:00» → «в три часа дня», «19:00» → «в семь вечера».
  Никогда не говори «тринадцать ноль ноль» и не читай двоеточие.
- Даты произноси словами: «2026-08-03» → «третьего августа», а лучше «завтра»/«сегодня»,
  если это уместно. Не диктуй дату цифрами.
- Телефон и числа тоже проговаривай естественно, а не по одной цифре.

Отвечай одной-двумя короткими фразами за раз, как в живом телефонном разговоре."""


# --- Инструменты (те же, что у голосового агента) ---
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_available_slots",
            "description": (
                "Свободные окна для записи. Без параметра date возвращает окна "
                "БЛИЖАЙШЕГО свободного дня — вызывай так при первом предложении. "
                "date ('YYYY-MM-DD', 'today', 'tomorrow') указывай только если "
                "пациент попросил другой день."
            ),
            "parameters": {
                "type": "object",
                "properties": {"date": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Записать на приём после подтверждения пациентом времени.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot": {"type": "string", "description": "'YYYY-MM-DD HH:00'"},
                    "reason": {"type": "string"},
                    "patient_name": {"type": "string", "description": "Имя пациента, если узнал его в разговоре"},
                },
                "required": ["slot"],
            },
        },
    },
]


def run_tool(name: str, args: dict, lead: dict) -> str:
    """Исполняет инструмент. lead нужен, чтобы привязать запись к заявке."""
    if name == "get_available_slots":
        date = args.get("date")
        if date:
            real = db.resolve_date(date)
            result = {"date": real, "free_slots": db.free_slots_for_date(real)}
        else:
            result = db.next_available()      # ближайший свободный день
        return json.dumps(result, ensure_ascii=False)
    if name == "book_appointment":
        result = db.create_appointment(
            patient_name=args.get("patient_name") or lead["name"],
            phone=lead["phone"],
            slot=args["slot"],
            reason=args.get("reason", lead.get("reason", "")),
            lead_id=lead["id"],
        )
        if result.get("success"):
            db.update_lead_status(lead["id"], "booked", f"Записан на {result['slot']}")
        return json.dumps(result, ensure_ascii=False)
    return json.dumps({"error": "unknown tool"}, ensure_ascii=False)


def create_conversation(lead: dict) -> list[dict]:
    return [{"role": "system", "content": build_system_prompt(lead)}]


def run_turn(messages: list[dict], user_text: str, lead: dict) -> str:
    """Один ход текстового диалога с тем же циклом function calling, что и в голосе."""
    messages.append({"role": "user", "content": user_text})
    for _ in range(8):
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS_SCHEMA
        )
        msg = resp.choices[0].message
        messages.append(msg)
        if not msg.tool_calls:
            return msg.content
        for tc in msg.tool_calls:
            result = run_tool(tc.function.name, json.loads(tc.function.arguments), lead)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    return "Извините, оператор перезвонит вам чуть позже."
