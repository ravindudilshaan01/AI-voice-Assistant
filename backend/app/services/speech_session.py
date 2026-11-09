import asyncio
import logging
from time import monotonic
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect

from app.services.groq_errors import ServiceError
from app.services.tts import generate_wav
from app.services.tts import TTS_PROVIDER, TTS_VOICE
from app.config import TTS_AUTO_FALLBACK, TTS_QUEUE_SIZE
from app.services.metrics import metrics


logger = logging.getLogger(__name__)


class SpeechSession:
    """Ordered TTS producer for one WebSocket connection."""

    def __init__(self, websocket: WebSocket, session_id: str = "") -> None:
        self.websocket = websocket
        self.session_id = session_id
        self.event_sequence = 0
        self.send_lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None
        self.queue: asyncio.Queue[str | None] | None = None
        self.response_id = ""
        self.segment_count = 0
        self.voice = TTS_VOICE
        self.provider = TTS_PROVIDER

    async def send_json(self, event: dict) -> None:
        if self.session_id:
            event = dict(event)
            event.setdefault("sessionId", self.session_id)
            event.setdefault("eventSeq", self.event_sequence)
            turn_id = event.get("requestId") or event.get("responseId")
            if isinstance(turn_id, str):
                event.setdefault("turnId", turn_id)
            self.event_sequence += 1
        async with self.send_lock:
            await self.websocket.send_json(event)

    async def _send_audio(self, metadata: dict, audio: bytes) -> None:
        async def send_pair() -> None:
            async with self.send_lock:
                await self.websocket.send_json(metadata)
                await self.websocket.send_bytes(audio)

        pair = asyncio.create_task(send_pair())
        try:
            await asyncio.shield(pair)
        except asyncio.CancelledError:
            # Finish the metadata/binary pair before stopping the producer.
            with suppress(Exception):
                await pair
            raise

    async def send_audio(self, response_id: str, index: int, text: str, audio: bytes) -> None:
        """Send one atomic metadata/audio pair for priority events such as reminders."""
        await self._send_audio({
            "type": "tts_audio", "responseId": response_id,
            "segmentId": f"{response_id}:{index}", "index": index,
            "mimeType": "audio/wav", "text": text,
        }, audio)

    async def start(self, response_id: str, enabled: bool, voice: str = TTS_VOICE, provider: str = TTS_PROVIDER) -> None:
        await self.stop()
        self.response_id = response_id
        self.segment_count = 0
        self.voice = voice
        self.provider = provider
        self.queue = asyncio.Queue(maxsize=TTS_QUEUE_SIZE) if enabled else None
        if self.queue is not None:
            self.task = asyncio.create_task(self._produce(response_id, self.queue, voice, provider))

    async def enqueue(self, text: str) -> None:
        if self.queue is not None:
            await self.queue.put(text)
            self.segment_count += 1

    def finish(self) -> None:
        if self.queue is not None:
            self.queue.put_nowait(None)

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        self.task = None
        self.queue = None

    async def _produce(self, response_id: str, queue: asyncio.Queue[str | None], voice: str, provider: str) -> None:
        index = 0
        try:
            while (text := await queue.get()) is not None:
                segment_id = f"{response_id}:{index}"
                try:
                    started = monotonic()
                    used_provider = provider
                    try:
                        audio = await generate_wav(text, voice, provider)
                    except ServiceError as primary_error:
                        if not TTS_AUTO_FALLBACK:
                            raise
                        fallback = "kokoro" if provider == "groq" else "groq"
                        logger.warning("%s TTS failed; trying %s once: %s", provider, fallback, primary_error)
                        await metrics.increment(f"tts.{provider}.failures")
                        await self.send_json({
                            "type": "tts_provider_changed", "responseId": response_id,
                            "from": provider, "to": fallback,
                        })
                        audio = await generate_wav(text, voice, fallback)
                        used_provider = fallback
                    await metrics.observe(f"tts.{used_provider}.generation", (monotonic() - started) * 1000)
                    await self._send_audio({
                        "type": "tts_audio", "responseId": response_id,
                        "segmentId": segment_id, "index": index,
                        "mimeType": "audio/wav", "text": text,
                    }, audio)
                except ServiceError as error:
                    await metrics.increment(f"tts.{provider}.unrecoverable")
                    logger.warning("%s TTS stopped for response %s: %s", provider, response_id, error)
                    await self.send_json({
                        "type": "tts_error", "responseId": response_id,
                        "message": str(error),
                    })
                    break
                index += 1
            await self.send_json({"type": "tts_complete", "responseId": response_id, "segmentCount": index})
        except asyncio.CancelledError:
            raise
        except (RuntimeError, OSError, WebSocketDisconnect):
            # The WebSocket may close while an audio frame is being sent.
            pass
        except Exception as error:
            logger.exception("Unexpected %s TTS failure for response %s", provider, response_id)
            with suppress(RuntimeError, OSError, WebSocketDisconnect):
                await self.send_json({
                    "type": "tts_error", "responseId": response_id,
                    "message": "Natural voice generation failed unexpectedly. Please use Speak again.",
                })
                await self.send_json({"type": "tts_complete", "responseId": response_id, "segmentCount": index})
