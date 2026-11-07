import asyncio
import json
from contextlib import suppress
from datetime import datetime
from typing import Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.agents.voice_agent import stream_agent
from app.config import FRONTEND_ORIGIN
from app.services.groq_errors import ServiceError
from app.services.llm import MAX_TEXT_CHARS, MAX_TURNS
from app.services.speech_session import SpeechSession
from app.services.stt import MAX_AUDIO_BYTES, SUPPORTED_MIME_TYPES, transcribe
from app.services.tts import SUPPORTED_TTS_VOICES, TTS_VOICE, SentenceSegmenter, generate_wav

app = FastAPI(title="Friday Voice Assistant API")
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


async def deliver_reminder(speech: SpeechSession, reminder: dict, voice: str) -> None:
    due = datetime.fromisoformat(str(reminder["due_at"]))
    title = str(reminder["title"])
    spoken = f"Reminder. {title}."
    response_id = f"reminder-{int(due.timestamp() * 1000)}"
    # Prepare speech early so network generation does not make the reminder late.
    audio_task = asyncio.create_task(generate_wav(spoken, voice))
    try:
        # Recheck the wall clock after sleeping so event-loop jitter can never
        # deliver the reminder a few milliseconds before its due timestamp.
        while (delay := (due - datetime.now(due.tzinfo)).total_seconds()) > 0:
            await asyncio.sleep(delay)
        await speech.send_json({
            "type": "reminder", "reminderId": response_id,
            "title": title, "dueAt": due.isoformat(),
        })
        try:
            audio = await audio_task
            await speech.send_audio(response_id, 0, spoken, audio)
        except ServiceError as error:
            await speech.send_json({
                "type": "tts_error", "responseId": response_id,
                "message": str(error),
            })
            await speech.send_json({"type": "tts_complete", "responseId": response_id, "segmentCount": 0})
            return
        await speech.send_json({"type": "tts_complete", "responseId": response_id, "segmentCount": 1})
    except asyncio.CancelledError:
        audio_task.cancel()
        await asyncio.gather(audio_task, return_exceptions=True)
        raise


async def answer(
    speech: SpeechSession,
    history: list[dict[str, str]],
    text: str,
    request_id: str,
    speak: bool,
    voice: str = TTS_VOICE,
    schedule_reminder: Callable[[dict, str], None] | None = None,
) -> None:
    await speech.start(request_id, speak, voice)
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
            elif event["type"] == "tool_result" and event.get("name") == "schedule_event" and event.get("ok"):
                if schedule_reminder and isinstance(event.get("result"), dict):
                    schedule_reminder(event["result"], voice)
            await speech.send_json({**event, "requestId": request_id})
        response = "".join(parts).strip()
        if not response:
            raise ServiceError("The assistant returned an empty answer. Please try again.")
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

    try:
        await websocket.accept()
        speech = SpeechSession(websocket)
        await speech.send_json({"type": "connection", "status": "connected"})
    except (RuntimeError, OSError, WebSocketDisconnect):
        return
    history: list[dict[str, str]] = []
    active_answer: asyncio.Task[None] | None = None
    active_request_id = ""
    reminder_tasks: set[asyncio.Task[None]] = set()

    def schedule_reminder(reminder: dict, voice: str) -> None:
        task = asyncio.create_task(deliver_reminder(speech, reminder, voice))
        reminder_tasks.add(task)
        task.add_done_callback(reminder_tasks.discard)

    async def cancel_answer(notify: bool = True) -> None:
        nonlocal active_answer, active_request_id
        if active_answer is None:
            return
        if active_answer.done():
            with suppress(Exception):
                await active_answer
            active_answer = None
            active_request_id = ""
            return
        request_id = active_request_id
        active_answer.cancel()
        with suppress(asyncio.CancelledError):
            await active_answer
        active_answer = None
        active_request_id = ""
        await speech.stop()
        if notify:
            await speech.send_json({"type": "chat_cancelled", "requestId": request_id})

    async def start_answer(text: str, request_id: str, speak: bool, voice: str = TTS_VOICE) -> None:
        nonlocal active_answer, active_request_id
        if active_answer is not None and not active_answer.done():
            await speech.send_json({"type": "error", "requestId": request_id, "message": "The assistant is already answering. Stop the current response first."})
            return
        if active_answer is not None:
            with suppress(Exception):
                await active_answer
        active_request_id = request_id
        active_answer = asyncio.create_task(answer(speech, history, text, request_id, speak, voice, schedule_reminder))

    def selected_voice(message: dict) -> str:
        voice = message.get("voice")
        return voice if isinstance(voice, str) and voice in SUPPORTED_TTS_VOICES else TTS_VOICE
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
                await cancel_answer(notify=False)
                await speech.stop()
                history.clear()
                await speech.send_json({"type": "cleared"})
            elif kind == "stop_generation":
                await cancel_answer()
            elif kind == "stop_speaking":
                await speech.stop()
                await speech.send_json({"type": "tts_stopped"})
            elif kind == "message" and isinstance(message.get("text"), str) and isinstance(request_id, str):
                text = message["text"].strip()
                if len(text) > MAX_TEXT_CHARS:
                    await speech.send_json({"type": "error", "requestId": request_id, "message": "Message is too long. Please shorten it."})
                elif text:
                    await start_answer(text, request_id, message.get("speak") is not False, selected_voice(message))
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
                    if message.get("transcribeOnly"):
                        await speech.send_json({"type": "transcript_only", "requestId": request_id, "text": text})
                    else:
                        await speech.send_json({"type": "transcript", "requestId": request_id, "text": text})
                        await start_answer(text, request_id, message.get("speak") is not False, selected_voice(message))
                except ServiceError as error:
                    await speech.send_json({"type": "error", "requestId": request_id, "message": str(error)})
            else:
                await speech.send_json({"type": "error", "message": "Unsupported message."})
    except WebSocketDisconnect:
        pass
    finally:
        await cancel_answer(notify=False)
        for task in reminder_tasks:
            task.cancel()
        if reminder_tasks:
            await asyncio.gather(*reminder_tasks, return_exceptions=True)
        await speech.stop()
