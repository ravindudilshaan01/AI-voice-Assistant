import asyncio
import json
import logging
import math
import struct
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from time import monotonic
from typing import Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.agents.voice_agent import stream_agent
from app.config import FRONTEND_ORIGINS, KOKORO_PRELOAD, TTS_PROVIDER
from app.services.groq_errors import ServiceError
from app.services.llm import MAX_TEXT_CHARS, MAX_TURNS
from app.services.speech_session import SpeechSession
from app.services.stt import MAX_AUDIO_BYTES, SUPPORTED_MIME_TYPES, transcribe
from app.services.tts import SUPPORTED_TTS_PROVIDERS, SUPPORTED_TTS_VOICES, TTS_VOICE, SentenceSegmenter, generate_wav
from app.services.kokoro_tts import warmup
from app.services.metrics import TurnTimer, metrics

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if TTS_PROVIDER == "kokoro" and KOKORO_PRELOAD:
        try:
            await asyncio.to_thread(warmup)
        except Exception:
            logger.exception("Kokoro preload failed; the first voice request will report the provider error")
    yield


app = FastAPI(title="Friday Voice Assistant API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(FRONTEND_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def performance_metrics() -> dict:
    return await metrics.snapshot()


async def deliver_reminder(speech: SpeechSession, reminder: dict, voice: str, provider: str = TTS_PROVIDER) -> None:
    due = datetime.fromisoformat(str(reminder["due_at"]))
    title = str(reminder["title"])
    spoken = f"Reminder. {title}."
    response_id = f"reminder-{int(due.timestamp() * 1000)}"
    # Prepare speech early so network generation does not make the reminder late.
    audio_task = asyncio.create_task(generate_wav(spoken, voice, provider))
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


def tool_result_fallback(results: list[dict]) -> str:
    """Produce a safe concise answer when a tool round has no final LLM text."""
    sentences: list[str] = []
    for event in results:
        if not event.get("ok") or not isinstance(event.get("result"), dict):
            continue
        name = event.get("name")
        result = event["result"]
        if name == "calculate":
            sentences.append(f"The result is {result.get('result', 'not available')}.")
        elif name == "get_weather":
            location = result.get("location", "that location")
            temperature = result.get("temperature")
            unit = result.get("temperature_unit", "")
            condition = result.get("condition", "current conditions")
            rain = result.get("today_rain_chance")
            sentence = f"In {location}, it is {temperature}{unit} with {str(condition).lower()}"
            if rain is not None:
                sentence += f", with a {rain} percent chance of rain today"
            sentences.append(sentence + ".")
        elif name == "schedule_event":
            sentences.append(f"I set the reminder for {result.get('display_time', result.get('when', 'the requested time'))}.")
        elif name == "web_search":
            answer = str(result.get("answer", "")).strip()
            if answer:
                sentences.append(answer)
    return " ".join(sentences)


async def answer(
    speech: SpeechSession,
    history: list[dict[str, str]],
    text: str,
    request_id: str,
    speak: bool,
    voice: str = TTS_VOICE,
    provider: str = TTS_PROVIDER,
    schedule_reminder: Callable[[dict, str, str], None] | None = None,
) -> None:
    timer = TurnTimer()
    await speech.start(request_id, speak, voice, provider)
    await speech.send_json({"type": "chat_started", "requestId": request_id, "speak": speak, "state": "THINKING"})
    parts: list[str] = []
    tool_results: list[dict] = []
    # Release completed prose sentences for either TTS provider. This is what
    # lets the first sentence begin synthesis while the Groq LLM is still
    # streaming later sentences.
    segmenter = SentenceSegmenter(eager=True)
    try:
        async for event in stream_agent(history, text):
            if event["type"] == "chat_delta":
                if "first_token" not in timer.marks:
                    timer.mark("first_token")
                    await metrics.observe("llm.time_to_first_token", timer.elapsed_ms(end="first_token"))
                parts.append(event["text"])
                if speak:
                    for segment in segmenter.feed(event["text"]):
                        await speech.enqueue(segment)
            elif event["type"] == "tool_result" and event.get("name") == "schedule_event" and event.get("ok"):
                tool_results.append(event)
                if schedule_reminder and isinstance(event.get("result"), dict):
                    schedule_reminder(event["result"], voice, provider)
            elif event["type"] == "tool_result":
                tool_results.append(event)
            await speech.send_json({**event, "requestId": request_id})
        response = "".join(parts).strip()
        if not response:
            response = tool_result_fallback(tool_results)
            if response:
                await speech.send_json({"type": "chat_delta", "requestId": request_id, "text": response})
                parts.append(response)
                if speak:
                    for segment in segmenter.feed(response):
                        await speech.enqueue(segment)
        if not response:
            raise ServiceError("The assistant returned an empty answer. Please try again.")
        if speak:
            for segment in segmenter.flush():
                await speech.enqueue(segment)
            speech.finish()
        history.extend(({"role": "user", "content": text}, {"role": "assistant", "content": response}))
        del history[: max(0, len(history) - MAX_TURNS * 2)]
        await speech.send_json({"type": "chat_done", "requestId": request_id})
        await metrics.observe("turn.response_complete", timer.elapsed_ms())
        await metrics.increment("turn.success")
    except ServiceError as error:
        await metrics.increment("turn.failure")
        await speech.stop()
        await speech.send_json({"type": "error", "requestId": request_id, "message": str(error)})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in FRONTEND_ORIGINS:
        await websocket.close(code=1008)
        return

    try:
        await websocket.accept()
        session_id = str(uuid.uuid4())
        speech = SpeechSession(websocket, session_id)
        await speech.send_json({"type": "connection", "status": "connected", "sessionId": session_id, "state": "IDLE"})
    except (RuntimeError, OSError, WebSocketDisconnect):
        return
    history: list[dict[str, str]] = []
    active_answer: asyncio.Task[None] | None = None
    active_request_id = ""
    reminder_tasks: set[asyncio.Task[None]] = set()
    pcm_stream: dict | None = None

    def schedule_reminder(reminder: dict, voice: str, provider: str) -> None:
        task = asyncio.create_task(deliver_reminder(speech, reminder, voice, provider))
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
        cancel_started = monotonic()
        active_answer.cancel()
        with suppress(asyncio.CancelledError):
            await active_answer
        active_answer = None
        active_request_id = ""
        await speech.stop()
        await metrics.observe("interruption.cancellation", (monotonic() - cancel_started) * 1000)
        await metrics.increment("interruption.success")
        if notify:
            await speech.send_json({"type": "chat_cancelled", "requestId": request_id})

    async def start_answer(text: str, request_id: str, speak: bool, voice: str = TTS_VOICE, provider: str = TTS_PROVIDER) -> None:
        nonlocal active_answer, active_request_id
        if active_answer is not None and not active_answer.done():
            await speech.send_json({"type": "error", "requestId": request_id, "message": "The assistant is already answering. Stop the current response first."})
            return
        if active_answer is not None:
            with suppress(Exception):
                await active_answer
        active_request_id = request_id
        active_answer = asyncio.create_task(answer(speech, history, text, request_id, speak, voice, provider, schedule_reminder))

    def selected_voice(message: dict) -> str:
        voice = message.get("voice")
        return voice if isinstance(voice, str) and voice in SUPPORTED_TTS_VOICES else TTS_VOICE

    def selected_provider(message: dict) -> str:
        provider = message.get("provider")
        return provider if isinstance(provider, str) and provider in SUPPORTED_TTS_PROVIDERS else TTS_PROVIDER
    try:
        while True:
            packet = await websocket.receive()
            if packet["type"] == "websocket.disconnect":
                break
            if packet.get("bytes") is not None:
                if pcm_stream is None:
                    await speech.send_json({"type": "error", "message": "Send PCM stream metadata before audio frames."})
                    continue
                frame = packet["bytes"]
                if len(frame) < 18 or len(frame) > 64 * 1024:
                    await speech.send_json({"type": "error", "requestId": pcm_stream["requestId"], "message": "Invalid PCM frame."})
                    continue
                sequence, client_timestamp, sample_rate = struct.unpack_from("<IdI", frame, 0)
                if sample_rate != pcm_stream["sampleRate"] or sequence != pcm_stream["nextSeq"]:
                    await metrics.increment("audio.sequence_gap")
                    pcm_stream["nextSeq"] = sequence + 1
                else:
                    pcm_stream["nextSeq"] += 1
                samples = memoryview(frame)[16:].cast("h")
                if not samples:
                    continue
                rms = math.sqrt(sum((sample / 32768.0) ** 2 for sample in samples) / len(samples))
                pcm_stream["frames"] += 1
                pcm_stream["bytes"] += len(frame) - 16
                threshold = max(0.012, pcm_stream["noiseFloor"] * 2.8)
                now = monotonic()
                if rms >= threshold:
                    pcm_stream["speechFrames"] += 1
                    pcm_stream["lastSpeech"] = now
                    if not pcm_stream["speechStarted"] and pcm_stream["speechFrames"] >= 10:
                        pcm_stream["speechStarted"] = True
                        await speech.send_json({"type": "speech_started", "requestId": pcm_stream["requestId"]})
                elif not pcm_stream["speechStarted"]:
                    pcm_stream["noiseFloor"] = pcm_stream["noiseFloor"] * 0.97 + rms * 0.03
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
            elif kind == "client_metric":
                name, duration = message.get("name"), message.get("durationMs")
                allowed = {"speech_end_to_first_playback", "playback_segment", "audio_capture"}
                if isinstance(name, str) and name in allowed and isinstance(duration, (int, float)) and 0 <= duration <= 600_000:
                    await metrics.observe(f"frontend.{name}", float(duration))
            elif kind == "pcm_stream_start" and isinstance(request_id, str):
                sample_rate = message.get("sampleRate")
                if sample_rate != 16000 or message.get("format") != "s16le":
                    await speech.send_json({"type": "error", "requestId": request_id, "message": "PCM must be 16 kHz signed 16-bit mono audio."})
                    continue
                pcm_stream = {"requestId": request_id, "sampleRate": sample_rate, "nextSeq": 0, "frames": 0,
                              "bytes": 0, "speechFrames": 0, "speechStarted": False, "lastSpeech": 0.0,
                              "noiseFloor": 0.004, "started": monotonic()}
                await speech.send_json({"type": "listening", "requestId": request_id, "state": "LISTENING"})
            elif kind == "pcm_stream_end" and pcm_stream is not None and request_id == pcm_stream["requestId"]:
                await metrics.observe("audio.capture", (monotonic() - pcm_stream["started"]) * 1000)
                await metrics.increment("audio.stream_completed")
                await speech.send_json({"type": "speech_ended", "requestId": request_id, "frames": pcm_stream["frames"],
                                        "speechDetected": pcm_stream["speechStarted"], "state": "TRANSCRIBING"})
                pcm_stream = None
            elif kind == "speak_text" and isinstance(message.get("text"), str) and isinstance(request_id, str):
                text = message["text"].strip()
                if not text or len(text) > MAX_TEXT_CHARS:
                    await speech.send_json({"type": "error", "requestId": request_id, "message": "Text to speak is empty or too long."})
                else:
                    await speech.start(request_id, True, selected_voice(message), selected_provider(message))
                    segmenter = SentenceSegmenter()
                    for segment in segmenter.feed(text) + segmenter.flush():
                        await speech.enqueue(segment)
                    speech.finish()
            elif kind == "message" and isinstance(message.get("text"), str) and isinstance(request_id, str):
                text = message["text"].strip()
                if len(text) > MAX_TEXT_CHARS:
                    await speech.send_json({"type": "error", "requestId": request_id, "message": "Message is too long. Please shorten it."})
                elif text:
                    await start_answer(text, request_id, message.get("speak") is not False, selected_voice(message), selected_provider(message))
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
                    stt_started = monotonic()
                    text = await transcribe(audio, mime_type)
                    await metrics.observe("stt.final", (monotonic() - stt_started) * 1000)
                    if len(text) > MAX_TEXT_CHARS:
                        raise ServiceError("Transcription is too long. Please record a shorter message.")
                    if message.get("transcribeOnly"):
                        await speech.send_json({"type": "transcript_only", "requestId": request_id, "text": text})
                    else:
                        await speech.send_json({"type": "transcript", "requestId": request_id, "text": text})
                        await start_answer(text, request_id, message.get("speak") is not False, selected_voice(message), selected_provider(message))
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
