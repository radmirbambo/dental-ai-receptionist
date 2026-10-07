"""
caller_agent.py — ИСХОДЯЩИЙ голосовой агент на LiveKit Agents.

Правая («реалтайм-голос») зона архитектуры, на своём пайплайне:
    SIP/браузер  ->  Deepgram STT (ru)  ->  LLM (OpenRouter)  ->  ElevenLabs TTS (ru)
Агент сам набирает номер лида (или общается через браузер), ведёт разговор и
записывает на осмотр в ту же БД, что и дашборд.

Запуск:  python caller_agent.py dev
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random

from dotenv import load_dotenv
from livekit import rtc, api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
    get_job_context,
    RoomInputOptions,
)
from livekit.plugins import deepgram, openai, silero

import db
import brain  # переиспользуем системный промпт — поведение = как в текстовом режиме

load_dotenv(".env")
logger = logging.getLogger("dental-outbound")
logger.setLevel(logging.INFO)

# ID выходного SIP-транка (создаётся один раз, см. README). Через него уходит звонок.
OUTBOUND_TRUNK_ID = os.getenv("SIP_OUTBOUND_TRUNK_ID")


def build_tts():
    """Выбор озвучки через переменную TTS_PROVIDER:
        yandex — Yandex SpeechKit, русский голос, работает из РФ (нужны YANDEX_*);
        eleven (по умолчанию) — ElevenLabs (из РФ заблокирован, нужен VPN);
        mac    — бесплатная офлайн-озвучка macOS (запасной вариант).
    """
    provider = os.getenv("TTS_PROVIDER", "eleven").lower()
    if provider == "yandex":
        from yandex_tts import YandexTTS
        return YandexTTS()
    if provider == "mac":
        from mac_tts import MacTTS
        return MacTTS()
    from livekit.plugins import elevenlabs
    return elevenlabs.TTS(
        voice_id=os.getenv("ELEVEN_VOICE_ID", ""),
        model=os.getenv("ELEVEN_MODEL", "eleven_turbo_v2_5"),
        language="ru",
        voice_settings=elevenlabs.VoiceSettings(
            stability=0.4,
            similarity_boost=0.8,
            style=0.35,
            use_speaker_boost=True,
        ),
        api_key=os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY"),
    )


class DentalCaller(Agent):
    """Агент-регистратор. Инструменты работают с той же БД, что и дашборд."""

    def __init__(self, lead: dict):
        super().__init__(instructions=brain.build_system_prompt(lead))
        self.lead = lead
        self.participant: rtc.RemoteParticipant | None = None

    def set_participant(self, p: rtc.RemoteParticipant):
        self.participant = p

    async def _hangup(self):
        ctx = get_job_context()
        await ctx.api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))

    @function_tool()
    async def get_available_slots(self, ctx: RunContext, date: str = ""):
        """Свободные окна для записи.

        Args:
            date: НЕ указывай при первом предложении — тогда вернутся окна
                  ближайшего свободного дня. Указывай ('YYYY-MM-DD', 'today',
                  'tomorrow' или конкретный день), только если пациент попросил
                  другой день.
        """
        ctx.session.say(
            random.choice([
                "Секундочку, посмотрю расписание.",
                "Минутку, гляну свободные окошки.",
                "Сейчас уточню, что есть.",
            ]),
            add_to_chat_ctx=False,
        )
        if date:
            real_date = db.resolve_date(date)
            result = {"date": real_date, "free_slots": db.free_slots_for_date(real_date)}
        else:
            result = db.next_available()
        logger.info(f"slots (date={date or 'nearest'}): {result}")
        return result

    @function_tool()
    async def book_appointment(self, ctx: RunContext, slot: str, reason: str = ""):
        """Записать пациента на приём. Вызывать ТОЛЬКО после устного подтверждения времени.

        Args:
            slot: выбранное время в формате 'YYYY-MM-DD HH:00'
            reason: что беспокоит пациента
        """
        ctx.session.say(
            random.choice(["Отлично, записываю вас.", "Секунду, оформляю запись."]),
            add_to_chat_ctx=False,
        )
        result = db.create_appointment(
            patient_name=self.lead["name"],
            phone=self.lead["phone"],
            slot=slot,
            reason=reason or self.lead.get("reason", ""),
            lead_id=self.lead["id"],
        )
        if result.get("success"):
            db.update_lead_status(self.lead["id"], "booked", f"Записан на {result['slot']}")
            logger.info(f"booked lead {self.lead['id']} -> {slot}")
        return result

    @function_tool()
    async def end_call(self, ctx: RunContext):
        """Завершить звонок, когда разговор окончен или пациент попрощался."""
        speech = ctx.session.current_speech
        if speech:
            await speech.wait_for_playout()
        await self._hangup()

    @function_tool()
    async def detected_answering_machine(self, ctx: RunContext):
        """Вызвать, если ответил автоответчик/голосовая почта."""
        db.update_lead_status(self.lead["id"], "no_answer", "Автоответчик")
        await self._hangup()


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    meta = json.loads(ctx.job.metadata or "{}")
    lead_id = meta.get("lead_id")
    lead = db.get_lead(lead_id) if lead_id else None
    if not lead:
        logger.error(f"lead not found: {meta}")
        ctx.shutdown()
        return

    # mode: "browser" — говорим через микрофон в браузере; "sip" — телефонный звонок.
    mode = meta.get("mode", "sip")
    db.update_lead_status(lead_id, "calling")

    agent = DentalCaller(lead)

    session = AgentSession(
        vad=silero.VAD.load(min_silence_duration=0.25),
        stt=deepgram.STT(model="nova-2", language="ru"),
        llm=openai.LLM(
            model=os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        ),
        tts=build_tts(),
        preemptive_generation=True,
        min_endpointing_delay=0.3,
        max_endpointing_delay=3.0,
    )

    session_task = asyncio.create_task(
        session.start(agent=agent, room=ctx.room, room_input_options=RoomInputOptions())
    )

    GREETING = ("Поздоровайся, представься администратором клиники «Улыбка», "
                "скажи, что звонишь по оставленной заявке, и спроси, удобно ли говорить.")

    if mode == "browser":
        await session_task
        participant = await ctx.wait_for_participant()
        agent.set_participant(participant)
        logger.info(f"browser participant joined (lead {lead_id})")
        await session.generate_reply(instructions=GREETING)
        return

    phone = lead["phone"]
    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=OUTBOUND_TRUNK_ID,
                sip_call_to=phone,
                participant_identity=phone,
                wait_until_answered=True,
            )
        )
        await session_task
        participant = await ctx.wait_for_participant(identity=phone)
        agent.set_participant(participant)
        logger.info(f"call answered by {phone} (lead {lead_id})")
        await session.generate_reply(instructions=GREETING)

    except api.TwirpError as e:
        code = e.metadata.get("sip_status_code")
        logger.error(f"SIP error: {e.message} status={code}")
        db.update_lead_status(lead_id, "no_answer" if code else "failed", f"SIP {code}")
        ctx.shutdown()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="dental-outbound"))
