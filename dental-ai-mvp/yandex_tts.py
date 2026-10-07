"""
yandex_tts.py — озвучка через Yandex SpeechKit (хороший русский голос, работает из РФ).

Зачем: ElevenLabs блокирует Россию, macOS-голос роботичный. Yandex SpeechKit —
нативный русский, доступен из РФ, платится рублёвой картой, данные не уходят за
рубеж (плюс к 152-ФЗ). Кастомный TTS-плагин для livekit-agents: подключается через
TTS_PROVIDER=yandex, так же как mac.

API v1 synthesize: возвращает сырой LPCM (int16 LE) — отдаём прямо в аудио.
Нужны переменные окружения:
    YANDEX_API_KEY     — API-ключ сервисного аккаунта Yandex Cloud
    YANDEX_FOLDER_ID   — id каталога (нужен для API-ключа; см. README)
    YANDEX_VOICE       — голос (по умолчанию alena); варианты: alena, filipp,
                         ermil, jane, omazh, zahar, madirus, dasha, julia, lera, masha
"""

from __future__ import annotations

import os
import aiohttp

from livekit.agents import (
    APIConnectOptions,
    DEFAULT_API_CONNECT_OPTIONS,
    tts,
)
from livekit.agents.tts import AudioEmitter, ChunkedStream

URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
SAMPLE_RATE = 48000
DEFAULT_VOICE = os.getenv("YANDEX_VOICE", "alena")


class YandexTTS(tts.TTS):
    def __init__(self, *, voice: str = DEFAULT_VOICE, sample_rate: int = SAMPLE_RATE):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._voice = voice
        self._api_key = os.getenv("YANDEX_API_KEY", "")
        self._folder = os.getenv("YANDEX_FOLDER_ID", "")

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "ChunkedStream":
        return _YandexStream(tts=self, input_text=text, conn_options=conn_options)


class _YandexStream(ChunkedStream):
    def __init__(self, *, tts: YandexTTS, input_text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)

    async def _run(self, output_emitter: AudioEmitter) -> None:
        t: YandexTTS = self._tts
        sr = t.sample_rate
        data = {
            "text": self._input_text,
            "lang": "ru-RU",
            "voice": t._voice,
            "format": "lpcm",             # сырой PCM int16 LE — отдаём как есть
            "sampleRateHertz": str(sr),
        }
        if t._folder:
            data["folderId"] = t._folder
        headers = {"Authorization": f"Api-Key {t._api_key}"}

        async with aiohttp.ClientSession() as session:
            async with session.post(URL, data=data, headers=headers) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:300]
                    raise RuntimeError(f"Yandex TTS {resp.status}: {body}")
                pcm = await resp.read()

        output_emitter.initialize(
            request_id="yandex-tts",
            sample_rate=sr,
            num_channels=1,
            mime_type="audio/pcm",
        )
        output_emitter.push(pcm)
        output_emitter.flush()
