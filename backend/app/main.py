import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.agent import Agent
from app.config import settings
from app.stt import transcribe
from app.tts import synthesize

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-assistant")

app = FastAPI(title="AI Voice Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = Agent()


async def process_turn(
    websocket: WebSocket,
    transcript: str,
    conversation: list[dict],
) -> None:
    """Run one text or voice turn through the shared assistant pipeline."""
    await websocket.send_json({"type": "transcript", "text": transcript})
    conversation.append({"role": "user", "content": transcript})

    try:
        reply_text = ""
        async for event in agent.respond_stream(conversation):
            if event["type"] in ("tool_call", "tool_result"):
                await websocket.send_json(event)
            elif event["type"] == "final":
                reply_text = event["text"]
    except Exception as exc:
        logger.exception("Agent error")
        err_str = str(exc)
        if "leak" in err_str.lower() or "permission_denied" in err_str.lower() or "403" in err_str:
            message = "The Gemini API key is invalid or disabled. Update GEMINI_API_KEY and try again."
        elif any(
            marker in err_str.lower()
            for marker in ("429", "quota", "rate", "resource_exhausted")
        ):
            message = "Gemini rate limit reached. Please wait a moment and try again."
        else:
            message = "The assistant could not generate a response. Please try again."
        await websocket.send_json({"type": "error", "message": message})
        await websocket.send_json({"type": "done"})
        return

    conversation.append({"role": "assistant", "content": reply_text})
    await websocket.send_json({"type": "assistant_text", "text": reply_text})

    try:
        mp3_bytes = await synthesize(reply_text)
        await websocket.send_bytes(mp3_bytes)
    except Exception:
        logger.exception("TTS failed")
        await websocket.send_json(
            {
                "type": "tts_error",
                "message": "The answer is ready, but voice playback is unavailable.",
            }
        )

    await websocket.send_json({"type": "done"})


@app.get("/health")
async def health():
    return {"status": "ok", "llm_provider": settings.llm_provider}


@app.websocket("/ws/voice")
async def voice_socket(websocket: WebSocket):
    """
    Protocol (binary + text frames on one socket):
      - Client sends binary audio chunks (webm/opus) as the user speaks.
      - Client sends {"type": "end_utterance"} text frame when the user stops talking.
      - Or client sends {"type": "text_message", "text": "..."} for typed chat.
      - Server transcribes the buffered audio, runs the agent loop, synthesizes
        speech, and sends back:
          {"type": "transcript", "text": "..."}                        (text frame)
          {"type": "tool_call", "name": "...", "arguments": {...}}      (text frame, 0+ times)
          {"type": "tool_result", "name": "...", "result": "..."}       (text frame, 0+ times)
          {"type": "assistant_text", "text": "..."}                     (text frame)
          <binary mp3 audio>                                            (binary frame)
          {"type": "done"}                                              (text frame)
    """
    await websocket.accept()
    logger.info("Client connected")

    audio_buffer = bytearray()
    conversation: list[dict] = []

    try:
        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if "bytes" in message and message["bytes"] is not None:
                audio_buffer.extend(message["bytes"])
                continue

            if "text" in message and message["text"] is not None:
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {"type": "error", "message": "Invalid message format."}
                    )
                    continue

                if payload.get("type") == "end_utterance":
                    if not audio_buffer:
                        await websocket.send_json({"type": "error", "message": "No audio received."})
                        continue

                    try:
                        transcript = await transcribe(bytes(audio_buffer))
                        audio_buffer.clear()
                    except Exception as exc:
                        audio_buffer.clear()
                        logger.error("Transcription failed: %s", exc)
                        await websocket.send_json({"type": "error", "message": f"Transcription failed: {exc}"})
                        continue

                    if not transcript:
                        await websocket.send_json({"type": "error", "message": "Could not transcribe audio — please speak clearly and try again."})
                        continue

                    await process_turn(websocket, transcript, conversation)

                elif payload.get("type") == "text_message":
                    text = str(payload.get("text", "")).strip()
                    if not text:
                        await websocket.send_json(
                            {"type": "error", "message": "Please enter a message."}
                        )
                        continue
                    await process_turn(websocket, text, conversation)

                elif payload.get("type") == "reset_conversation":
                    conversation.clear()
                    audio_buffer.clear()

    except WebSocketDisconnect:
        logger.info("Client disconnected")
