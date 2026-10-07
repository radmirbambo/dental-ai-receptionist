"""
dispatch.py — запуск исходящего звонка через LiveKit.

Backend вызывает start_outbound_call(lead_id, phone). Мы создаём «dispatch» —
поручение воркеру (caller_agent.py) зайти в комнату call-<lead_id> и позвонить.
Метаданные передают воркеру, кому и по какому лиду звонить.

Нужны переменные окружения: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET.
"""

import json
from livekit import api


async def _dispatch(lead_id: int, metadata: dict) -> str:
    room = f"call-{lead_id}"
    lkapi = api.LiveKitAPI()  # читает LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET из env
    try:
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="dental-outbound",     # = agent_name в caller_agent.py
                room=room,
                metadata=json.dumps(metadata),
            )
        )
    finally:
        await lkapi.aclose()
    return room


async def start_outbound_call(lead_id: int, phone: str) -> str:
    """Реальный телефонный звонок через SIP (нужен транк)."""
    return await _dispatch(lead_id, {"lead_id": lead_id, "phone": phone, "mode": "sip"})


async def start_browser_call(lead_id: int) -> str:
    """Голосовой звонок через браузер (без SIP). Агент войдёт в комнату call-<lead_id>."""
    return await _dispatch(lead_id, {"lead_id": lead_id, "mode": "browser"})
