# Friday Voice Assistant

Friday is a React, TypeScript, FastAPI, and WebSocket voice assistant powered by Groq. It uses Whisper for transcription, a configurable Groq model for streamed chat and tool calls, and `canopylabs/orpheus-v1-english` for spoken WAV responses.

## Architecture at a glance

The browser is the user interface and audio endpoint. The FastAPI backend owns the API key, conversation history, tool execution, speech synthesis, cancellation, and per-connection reminders.

```text
React + TypeScript browser
  ├─ MediaRecorder: complete utterance for authoritative transcription
  ├─ AudioWorklet: 16 kHz PCM activity/diagnostic frames
  └─ WebSocket /ws
       └─ FastAPI session coordinator
            ├─ Groq Whisper: audio → final transcript
            ├─ Groq chat model: streamed text + function calls
            ├─ Tools: weather, web search, calculator, reminders
            └─ TTS queue: Kokoro ONNX or Groq → ordered WAV segments
```

### Example request

For “What is the weather in Colombo, and remind me in ten minutes to call Mum?”:

1. `frontend/src/App.tsx` records the utterance and sends an `audio_start` JSON message followed by binary audio through `frontend/src/services/websocket.ts`.
2. `backend/app/main.py` validates the upload and calls `backend/app/services/stt.py`, which sends it to Groq Whisper.
3. `backend/app/agents/voice_agent.py` streams the response and selects `get_weather` and `schedule_event` from `backend/app/tools/registry.py`.
4. The weather request calls Open-Meteo; the reminder is parsed in `APP_TIMEZONE` and held in backend memory for the active WebSocket connection.
5. Text deltas are rendered immediately. Completed prose is split into short segments, synthesized by `backend/app/services/speech_session.py`, and played in order by `frontend/src/services/audioPlayback.ts`.
6. If the user starts another turn, the browser sends cancellation; the backend cancels the active model/TTS tasks and rejects late audio using response and turn IDs.

The protocol carries session IDs, request/turn IDs, monotonically increasing event sequence numbers, tool events, transcript events, TTS metadata followed by WAV bytes, cancellation events, and safe errors. Secrets remain backend-only. History is limited to 10 completed turns; reminders and metrics are in memory and reset when the backend restarts.

## Features

- Streamed text chat over one WebSocket session
- Always-ready microphone capture with explicit tap-to-start and tap-to-stop controls
- Reused microphone stream between turns to avoid repeated permission and startup delays
- Conservative silence validation before audio reaches the LLM
- Manual utterance boundaries that avoid silence-timer and auto-restart glitches
- Selectable microphone with a live input meter
- Configurable Groq Whisper model/language with verbose confidence diagnostics
- Weather, calculator, web search, and session-memory scheduling tools
- Ordered audio playback, interruption, mute, and browser speech fallback
- Docker Compose setup plus backend protocol and frontend playback tests
- AudioWorklet capture with numbered 16 kHz, 20 ms PCM monitoring frames
- Explicit session, turn, and server-event sequence metadata
- Bounded sentence-level TTS queues with optional Kokoro/Orpheus failover
- Monotonic latency metrics with P50/P95 summaries at `/metrics`

The scheduling tool is a demonstration tool. Events are held in backend memory, are not synced to a calendar, and disappear when the backend restarts.

## Speech recognition reliability

Recordings are preserved from the first microphone tap until the user taps again to stop and send. The browser keeps the permitted microphone stream ready between turns, but it does not auto-record or auto-restart. Completed audio is sent to Groq Whisper unless the captured signal is demonstrably silent.

The backend requests `verbose_json` transcription with `temperature=0`. It rejects only empty results or recordings with jointly extreme silence/confidence metadata, so valid short, quiet, accented, or conversational phrases are not rejected solely because confidence is imperfect.

### Audio pipeline

The browser requests echo cancellation, noise suppression, automatic gain control, mono input, and a preferred 48 kHz device rate. These constraints are preferences; the diagnostics panel displays the rate actually selected by the browser.

An `AudioWorklet` resamples the live microphone signal to 16 kHz mono signed PCM and emits 320-sample (20 ms) frames. Every binary frame contains a little-endian 16-byte header followed by PCM samples:

| Offset | Type | Meaning |
| --- | --- | --- |
| 0 | `uint32` | frame sequence |
| 4 | `float64` | browser monotonic timestamp in milliseconds |
| 12 | `uint32` | sample rate (16,000) |
| 16 | `int16[]` | mono PCM samples |

The backend validates frame sequence and calculates adaptive energy-based speech activity without logging or retaining PCM content. The existing MediaRecorder stream remains the authoritative final utterance sent to Groq Whisper, which preserves browser codec compatibility and provides a fallback when AudioWorklet is unavailable.

Groq's file transcription API is not represented as native streaming STT. This version does **not** fabricate partial transcripts. The UI displays the final authoritative Groq transcript. A local incremental recognizer remains an optional future extension because its model size and CPU requirements are hardware-dependent.

### Concurrent response pipeline

Groq LLM tokens are streamed to the UI. A structure-aware sentence buffer excludes function-call JSON and sends completed prose segments into a bounded asynchronous TTS queue. Kokoro inference runs outside the event loop; Groq Orpheus is rate-limit aware. Numbered audio segments are delivered atomically as metadata followed by WAV bytes and played in order by the browser.

Starting a microphone turn while a response is active stops playback and sends generation cancellation. Server tasks are cancelled, the TTS queue is cleared, and late audio is rejected by response/turn identity. Browser echo cancellation and sustained-speech thresholds reduce self-interruption, although physical microphone/speaker behavior still requires manual device testing.

Set these optional variables in `.env`:

```dotenv
GROQ_STT_MODEL=whisper-large-v3-turbo
GROQ_STT_LANGUAGE=en
GROQ_TTS_MODEL=canopylabs/orpheus-v1-english
GROQ_TTS_VOICE=troy
GROQ_TTS_MIN_INTERVAL_SECONDS=6.2
TTS_AUTO_FALLBACK=1
TTS_QUEUE_SIZE=8
TOOL_TIMEOUT_SECONDS=12
APP_TIMEZONE=Asia/Colombo
```

Use `GROQ_STT_MODEL=whisper-large-v3` when prioritizing accuracy over speed, and `GROQ_STT_LANGUAGE=auto` to let Whisper detect the language.

In development builds, the **Audio diagnostics** panel records a five-second sample, plays it back, shows the active microphone and sample rate, and submits the sample directly to Whisper without invoking the LLM. It does not persist recordings or log audio contents.

## Run with Docker

1. Copy `.env.example` to `.env`.
2. Put your Groq API key in `.env`.
3. Start Docker Desktop.
4. Run `docker compose up --build`.
5. Open <http://localhost:5173>.

## Run locally

Backend:

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Set `GROQ_API_KEY` in `backend/.env` before starting the API.

Frontend:

```powershell
cd frontend
copy .env.example .env
npm ci
npm run dev
```

The browser requires microphone permission. Hands-free mode becomes active after the first microphone click, which satisfies browser permission and audio policies.

## Tests

```powershell
cd backend
python -m unittest discover -s tests -v

cd ..\frontend
npm test
npm run build
```

Unit and protocol tests mock Groq and cover playback ordering, voice selection, low-confidence rejection, cancellation, and direct transcription. A real API key, network connection, microphone, speakers, and browser permission are required to validate device selection, Sri Lankan English accents, echo cancellation, and end-to-end voice behavior.

Validate Docker configuration with:

```powershell
docker compose config
```

### Performance metrics

`GET /metrics` returns bounded, in-memory aggregate measurements and counters. Durations use backend monotonic clocks, while browser playback timing is calculated entirely with the browser's monotonic `performance.now()` clock before being submitted as a duration. Cross-device wall-clock timestamps are never subtracted.

The development Audio diagnostics drawer can load the current metrics snapshot. It includes observed P50, P95, minimum, maximum, and count values. Metrics contain no transcripts, API keys, or audio. Values reset when the backend restarts and are real observations only; the repository does not ship invented benchmark results.

## WebSocket protocol

Client messages include `message`, legacy `audio_start` followed by one binary recording, `pcm_stream_start`, numbered binary PCM frames, `pcm_stream_end`, `client_metric`, `stop_generation`, `stop_speaking`, `clear`, and `ping`. `audio_start` may include `transcribeOnly: true` for development diagnostics. Server messages include session metadata, listening/speech events, streamed `chat_delta` events, structured tool results, final transcripts, provider-change notices, TTS metadata followed by binary WAV data, cancellation/completion events, and safe errors.

Each server connection receives a UUID session ID and monotonic event sequence. Request/response events include a turn ID derived from the client's request ID. Existing whole-recording messages remain supported for compatibility.

Text is limited to 4,000 characters, recordings to 10 MiB, and session history to 10 completed turns. Groq secrets remain on the backend.

## Known limitations

- Groq remains required for final transcription and intelligence; this is a hybrid system, not a fully offline assistant.
- Partial local transcription is intentionally not enabled without a compatible local model and measured hardware performance.
- Energy VAD and browser echo cancellation reduce false triggers but cannot guarantee echo removal on every microphone/speaker combination.
- Weather and web search require internet access.
- Reminders live only for the current WebSocket connection.
- P50/P95 values become representative only after enough real turns have been observed.
