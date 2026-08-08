# AI Voice Assistant

Full-stack conversational chat and voice assistant: responsive React/TypeScript UI,
FastAPI backend, WebSocket audio streaming, a Gemini/Ollama agent loop with
function-calling tools, and speech-to-text / text-to-speech — all containerized
with Docker Compose.

## Pipeline

```
Browser records audio or sends a typed message
  → MediaRecorder records audio (webm/opus, ogg/opus, or mp4)
  → sends binary chunks over WebSocket to FastAPI
  → ffmpeg converts webm → 16kHz WAV
  → faster-whisper transcribes WAV → text
  → transcript sent back to UI (JSON)
  → Gemini runs agent loop with tool schemas
  → if tool call needed: executes tool, feeds result back to Gemini
  → tool events streamed to UI (JSON)
  → final reply text sent to UI (JSON)
  → edge-tts synthesizes reply → MP3 bytes sent back as binary
  → browser plays audio
```

## LLM backend

Swappable via `LLM_PROVIDER` in `.env` — both implement the same `LLMProvider`
interface (`backend/app/llm/base.py`), so `agent.py` doesn't change either way:

- `gemini` (default, Gemini 3.6 Flash) — free tier, cloud, fast, no GPU strain. Best choice on a
  weak laptop, and what live demos should use.
- `ollama` — fully local, no API key, runs in its own container. Works, but
  expect a few seconds of extra latency per response on CPU-only hardware.

## A note on "local" and "free"

- **faster-whisper (STT)**: genuinely local and free — runs entirely on your CPU.
- **Ollama (LLM)**: genuinely local and free — runs entirely on your machine.
- **edge-tts (TTS)**: free and needs no API key, but it calls Microsoft's Edge
  read-aloud service over the network — it is *not* offline. If you need a fully
  offline TTS path, swap in Piper (runs as a local subprocess, no network calls).

## Quick start (Gemini, default)

```bash
cp .env.example .env
# paste a free key from https://aistudio.google.com/apikey into GEMINI_API_KEY
docker compose up --build
```

Open http://localhost:5173. Type a message, or tap the microphone, speak, and
tap stop.

## Switching to Ollama instead (fully local, no API key)

Uncomment the `ollama` service block in `docker-compose.yml`, then:

```bash
# in .env:
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5:3b

docker compose up --build
docker compose exec ollama ollama pull qwen2.5:3b
```

If your laptop has under 16GB RAM, stick with `qwen2.5:3b` or `llama3.2:3b` —
8B+ models will be slow on CPU-only hardware.

## Project layout

```
backend/
  app/
    main.py         WebSocket gateway — audio in, transcript/tool events/reply/audio out
    agent.py         Agent loop as an async generator — streams tool_call/tool_result/final events
    stt.py            ffmpeg webm->wav conversion + faster-whisper transcription
    tts.py             edge-tts wrapper (MP3 output)
    llm/
      base.py           Provider-agnostic interface
      gemini_provider.py
      ollama_provider.py
    tools/
      weather.py      Open-Meteo (free, no key)
      search.py         DuckDuckGo instant answers (free, no key)
      scheduler.py     In-memory demo — swap for a real calendar API later
frontend/
  src/
    hooks/useVoiceSocket.ts   Mic capture + WebSocket + tool-event handling + audio playback
    components/VoiceAssistant.tsx
docker-compose.yml
```

## WebSocket protocol

Client → server:
```
<binary audio chunks while recording>
{"type": "end_utterance"}
{"type": "text_message", "text": "..."}      # typed chat alternative
{"type": "reset_conversation"}                # start a fresh chat
```

Server → client, in order:
```
{"type": "transcript", "text": "..."}
{"type": "tool_call", "name": "...", "arguments": {...}}    # 0 or more
{"type": "tool_result", "name": "...", "result": "..."}     # 0 or more
{"type": "assistant_text", "text": "..."}
<binary mp3 audio>
{"type": "tts_error", "message": "..."}                   # only when playback generation fails
{"type": "done"}
```

## Known limitations

- Push-to-talk, not continuous listening — no VAD yet, so utterance boundaries
  are marked by tapping stop, not automatic silence detection.
- The scheduling tool is in-memory and resets on backend restart.
- edge-tts requires internet access even though it needs no API key.
