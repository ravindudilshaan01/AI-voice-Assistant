# Friday Voice Assistant

Friday is a React, TypeScript, FastAPI, and WebSocket voice assistant powered by Groq. It uses Whisper for transcription, a configurable Groq model for streamed chat and tool calls, and `canopylabs/orpheus-v1-english` for spoken WAV responses.

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

The scheduling tool is a demonstration tool. Events are held in backend memory, are not synced to a calendar, and disappear when the backend restarts.

## Speech recognition reliability

Recordings are preserved from the first microphone tap until the user taps again to stop and send. The browser keeps the permitted microphone stream ready between turns, but it does not auto-record or auto-restart. Completed audio is sent to Groq Whisper unless the captured signal is demonstrably silent.

The backend requests `verbose_json` transcription with `temperature=0`. It rejects only empty results or recordings with jointly extreme silence/confidence metadata, so valid short, quiet, accented, or conversational phrases are not rejected solely because confidence is imperfect.

Set these optional variables in `.env`:

```dotenv
GROQ_STT_MODEL=whisper-large-v3-turbo
GROQ_STT_LANGUAGE=en
GROQ_TTS_MODEL=canopylabs/orpheus-v1-english
GROQ_TTS_VOICE=troy
GROQ_TTS_MIN_INTERVAL_SECONDS=6.2
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

## WebSocket protocol

Client messages include `message`, `audio_start` followed by one binary recording, `stop_generation`, `stop_speaking`, `clear`, and `ping`. `audio_start` may include `transcribeOnly: true` for development diagnostics. Server messages include streamed `chat_delta` events, structured tool results, transcripts, TTS metadata followed by binary WAV data, cancellation/completion events, and safe errors.

Text is limited to 4,000 characters, recordings to 10 MiB, and session history to 10 completed turns. Groq secrets remain on the backend.
