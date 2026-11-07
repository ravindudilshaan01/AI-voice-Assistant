import asyncio
import logging
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect

from app.services.groq_errors import ServiceError
from app.services.tts import generate_wav
from app.services.tts import TTS_VOICE


logger = logging.getLogger(__name__)


class SpeechSession:
    """Ordered TTS producer for one WebSocket connection."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.send_lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None
        self.queue: asyncio.Queue[str | None] | None = None
        self.response_id = ""
        self.segment_count = 0
        self.voice = TTS_VOICE

    async def send_json(self, event: dict) -> None:
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

    async def start(self, response_id: str, enabled: bool, voice: str = TTS_VOICE) -> None:
        await self.stop()
        self.response_id = response_id
        self.segment_count = 0
        self.voice = voice
        self.queue = asyncio.Queue() if enabled else None
        if self.queue is not None:
            self.task = asyncio.create_task(self._produce(response_id, self.queue, voice))

    def enqueue(self, text: str) -> None:
        if self.queue is not None:
            self.queue.put_nowait(text)
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

    async def _produce(self, response_id: str, queue: asyncio.Queue[str | None], voice: str) -> None:
        index = 0
        try:
            while (text := await queue.get()) is not None:
                segment_id = f"{response_id}:{index}"
                try:
                    audio = await generate_wav(text, voice)
                    await self._send_audio({
                        "type": "tts_audio", "responseId": response_id,
                        "segmentId": segment_id, "index": index,
                        "mimeType": "audio/wav", "text": text,
                    }, audio)
                except ServiceError as error:
                    logger.warning("Cloud TTS stopped for response %s: %s", response_id, error)
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
            logger.exception("Unexpected cloud TTS failure for response %s", response_id)
            with suppress(RuntimeError, OSError, WebSocketDisconnect):
                await self.send_json({
                    "type": "tts_error", "responseId": response_id,
                    "message": "Natural voice generation failed unexpectedly. Please use Speak again.",
                })
                await self.send_json({"type": "tts_complete", "responseId": response_id, "segmentCount": index})
