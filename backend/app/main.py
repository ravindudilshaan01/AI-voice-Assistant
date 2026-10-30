import asyncio
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.agents.voice_agent import stream_agent
from app.config import FRONTEND_ORIGIN
from app.services.groq_errors import ServiceError
from app.services.llm import MAX_TEXT_CHARS, MAX_TURNS
from app.services.speech_session import SpeechSession
from app.services.stt import MAX_AUDIO_BYTES, SUPPORTED_MIME_TYPES, transcribe
from app.services.tts import SentenceSegmenter

app = FastAPI(title="NOVA AI API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def answer(speech: SpeechSession, history: list[dict[str, str]], text: str, request_id: str, speak: bool) -> None:
    await speech.start(request_id, speak)
    await speech.send_json({"type": "chat_started", "requestId": request_id, "speak": speak})
    parts: list[str] = []
    segmenter = SentenceSegmenter()
    try:
        async for event in stream_agent(history, text):
            if event["type"] == "chat_delta":
                parts.append(event["text"])
                if speak:
                    for segment in segmenter.feed(event["text"]):
                        speech.enqueue(segment)
            await speech.send_json({**event, "requestId": request_id})
        response = "".join(parts).strip()
        if not response:
            raise ServiceError("NOVA returned an empty answer. Please try again.")
        if speak:
            for segment in segmenter.flush():
                speech.enqueue(segment)
            speech.finish()
        history.extend(({"role": "user", "content": text}, {"role": "assistant", "content": response}))
        del history[: max(0, len(history) - MAX_TURNS * 2)]
        await speech.send_json({"type": "chat_done", "requestId": request_id})
    except ServiceError as error:
        await speech.stop()
        await speech.send_json({"type": "error", "requestId": request_id, "message": str(error)})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if origin is not None and origin != FRONTEND_ORIGIN:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    speech = SpeechSession(websocket)
    await speech.send_json({"type": "connection", "status": "connected"})
    history: list[dict[str, str]] = []
    try:
        while True:
            packet = await websocket.receive()
            if packet["type"] == "websocket.disconnect":
                break
            if packet.get("bytes") is not None:
                await speech.send_json({"type": "error", "message": "Send audio metadata before audio data."})
                continue
            try:
                message = json.loads(packet.get("text") or "")
            except (ValueError, TypeError):
                await speech.send_json({"type": "error", "message": "Expected a JSON message."})
                continue
            if not isinstance(message, dict):
                await speech.send_json({"type": "error", "message": "Expected a JSON object."})
                continue
            kind = message.get("type")
            request_id = message.get("requestId")
            if kind == "ping":
                await speech.send_json({"type": "pong"})
            elif kind == "clear":
                await speech.stop()
                history.clear()
                await speech.send_json({"type": "cleared"})
            elif kind == "stop_speaking":
                await speech.stop()
                await speech.send_json({"type": "tts_stopped"})
            elif kind == "message" and isinstance(message.get("text"), str) and isinstance(request_id, str):
                text = message["text"].strip()
                if len(text) > MAX_TEXT_CHARS:
                    await speech.send_json({"type": "error", "requestId": request_id, "message": "Message is too long. Please shorten it."})
                elif text:
                    await answer(speech, history, text, request_id, message.get("speak") is not False)
                else:
                    await speech.send_json({"type": "error", "requestId": request_id, "message": "Message cannot be empty."})
            elif kind == "audio_start" and isinstance(request_id, str):
                mime_type = message.get("mimeType")
                if not isinstance(mime_type, str) or mime_type.split(";", 1)[0].lower() not in SUPPORTED_MIME_TYPES:
                    await speech.send_json({"type": "error", "requestId": request_id, "message": "Unsupported recording format."})
                    continue
                size = message.get("size")
                if not isinstance(size, int) or size < 1 or size > MAX_AUDIO_BYTES:
                    await speech.send_json({"type": "error", "requestId": request_id, "message": "Recording is empty or too large."})
                    continue
                try:
                    audio_packet = await asyncio.wait_for(websocket.receive(), timeout=15.0)
                except asyncio.TimeoutError:
                    await speech.send_json({"type": "error", "requestId": request_id, "message": "Audio upload timed out. Please try again."})
                    continue
                if audio_packet["type"] == "websocket.disconnect":
                    break
                audio = audio_packet.get("bytes")
                if audio is None or len(audio) != size:
                    await speech.send_json({"type": "error", "requestId": request_id, "message": "Audio upload was incomplete. Please try again."})
                    continue
                try:
                    text = await transcribe(audio, mime_type)
                    if len(text) > MAX_TEXT_CHARS:
                        raise ServiceError("Transcription is too long. Please record a shorter message.")
                    await speech.send_json({"type": "transcript", "requestId": request_id, "text": text})
                    await answer(speech, history, text, request_id, message.get("speak") is not False)
                except ServiceError as error:
                    await speech.send_json({"type": "error", "requestId": request_id, "message": str(error)})
            else:
                await speech.send_json({"type": "error", "message": "Unsupported message."})
    except WebSocketDisconnect:
        pass
    finally:
        await speech.stop()
