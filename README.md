# NOVA AI Voice Assistant

NOVA is a React, TypeScript, FastAPI, and WebSocket voice assistant powered by Groq. It uses `whisper-large-v3-turbo` for transcription, a configurable Groq Llama model for streamed chat and tool calls, and `canopylabs/orpheus-v1-english` for spoken WAV responses.

## Features

- Streamed text chat over one WebSocket session
- Push-to-talk and hands-free microphone modes
- Automatic utterance end after silence and barge-in during spoken playback
- Selectable microphone with a live input meter
- Groq Whisper transcription and Groq Orpheus speech
- Weather, calculator, web search, and session-memory scheduling tools
- Ordered audio playback, interruption, mute, and browser speech fallback
- Docker Compose setup plus backend protocol and frontend playback tests

The scheduling tool is a demonstration tool. Events are held in backend memory, are not synced to a calendar, and disappear when the backend restarts.

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

Unit and protocol tests mock Groq. A real API key, network connection, microphone, and speakers are required for an end-to-end voice test.

## WebSocket protocol

Client messages include `message`, `audio_start` followed by one binary recording, `stop_speaking`, `clear`, and `ping`. Server messages include streamed `chat_delta` events, tool activity, transcripts, TTS metadata followed by binary WAV data, completion events, and safe errors.

Text is limited to 4,000 characters, recordings to 10 MiB, and session history to 10 completed turns. Groq secrets remain on the backend.
