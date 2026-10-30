import asyncio
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect

from app.services.groq_errors import ServiceError
from app.services.tts import generate_wav


class SpeechSession:
    """Ordered TTS producer for one WebSocket connection."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.send_lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None
        self.queue: asyncio.Queue[str | None] | None = None
        self.response_id = ""
        self.segment_count = 0

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

    async def start(self, response_id: str, enabled: bool) -> None:
        await self.stop()
        self.response_id = response_id
        self.segment_count = 0
        self.queue = asyncio.Queue() if enabled else None
        if self.queue is not None:
            self.task = asyncio.create_task(self._produce(response_id, self.queue))

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

    async def _produce(self, response_id: str, queue: asyncio.Queue[str | None]) -> None:
        index = 0
        fallback = False
        try:
            while (text := await queue.get()) is not None:
                segment_id = f"{response_id}:{index}"
                if not fallback:
                    try:
                        audio = await generate_wav(text)
                        await self._send_audio({
                            "type": "tts_audio", "responseId": response_id,
                            "segmentId": segment_id, "index": index,
                            "mimeType": "audio/wav", "text": text,
                        }, audio)
                    except ServiceError:
                        fallback = True
                if fallback:
                    await self.send_json({
                        "type": "tts_fallback", "responseId": response_id,
                        "segmentId": segment_id, "index": index, "text": text,
                        "message": "Cloud voice is unavailable. Using browser voice.",
                    })
                index += 1
            await self.send_json({"type": "tts_complete", "responseId": response_id, "segmentCount": index})
        except asyncio.CancelledError:
            raise
        except (RuntimeError, OSError, WebSocketDisconnect):
            # The WebSocket may close while an audio frame is being sent.
            pass
