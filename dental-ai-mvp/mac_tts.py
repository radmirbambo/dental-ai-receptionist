"""
mac_tts.py — БЕСПЛАТНАЯ офлайн-озвучка через встроенный в macOS голос `say`.

Зачем: ElevenLabs на free-тарифе не даёт API, а платный нельзя оплатить РФ-картой.
macOS умеет синтезировать русскую речь голосами Milena/Yuri прямо в системе —
без интернета, без ключей, без оплаты. Для демо звучит вполне достойно.

Это кастомный TTS-плагин для livekit-agents: агент вызывает его так же, как
ElevenLabs, но звук генерит команда `say`. Работает только на macOS.

Голос выбирается переменной MAC_TTS_VOICE (по умолчанию Milena). Список голосов:
    say -v '?'      # покажет все, ищите с пометкой ru_RU (Milena, Yuri)
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import wave

from livekit.agents import (
    APIConnectOptions,
    DEFAULT_API_CONNECT_OPTIONS,
    tts,
)
from livekit.agents.tts import AudioEmitter, ChunkedStream

SAMPLE_RATE = 24000                              # частота PCM, которую отдаёт say
DEFAULT_VOICE = os.getenv("MAC_TTS_VOICE", "Milena")   # русский голос macOS


class MacTTS(tts.TTS):
    """TTS на базе системной команды macOS `say`."""

    def __init__(self, *, voice: str = DEFAULT_VOICE, sample_rate: int = SAMPLE_RATE):
        # streaming=False: генерим фразу целиком, потом отдаём (для демо ок).
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._voice = voice

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "ChunkedStream":
        return _MacStream(tts=self, input_text=text, conn_options=conn_options, voice=self._voice)


class _MacStream(ChunkedStream):
    def __init__(self, *, tts: MacTTS, input_text: str, conn_options: APIConnectOptions, voice: str):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._voice = voice

    async def _run(self, output_emitter: AudioEmitter) -> None:
        sr = self._tts.sample_rate
        path = tempfile.mktemp(suffix=".wav")
        try:
            # say -> WAV с линейным PCM int16 на нужной частоте.
            proc = await asyncio.create_subprocess_exec(
                "say", "-v", self._voice,
                "-o", path,
                f"--data-format=LEI16@{sr}",
                self._input_text,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"`say` failed: {err.decode(errors='ignore') if err else proc.returncode}")

            pcm = await asyncio.to_thread(_read_pcm, path)

            output_emitter.initialize(
                request_id="mac-tts",
                sample_rate=sr,
                num_channels=1,
                mime_type="audio/pcm",        # отдаём сырой PCM — декодировать не нужно
            )
            output_emitter.push(pcm)
            output_emitter.flush()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


def _read_pcm(path: str) -> bytes:
    """Читает сырой PCM (int16 LE) из WAV, сгенерированного say."""
    with wave.open(path, "rb") as w:
        return w.readframes(w.getnframes())
