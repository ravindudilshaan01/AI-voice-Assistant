import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Local development uses the repository-level .env as the shared source of truth.
# backend/.env remains a fallback for running a copied backend by itself.
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PROJECT_ROOT / "backend" / ".env")


def groq_api_key() -> str:
    key = os.getenv("GROQ_API_KEY", "").strip()
    return "" if key == "your_groq_api_key_here" else key


LLM_MODEL = os.getenv("GROQ_LLM_MODEL", "llama-3.1-8b-instant")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
GROQ_STT_LANGUAGE = os.getenv("GROQ_STT_LANGUAGE", "en").strip().lower() or "en"
GROQ_STT_PROMPT = os.getenv(
    "GROQ_STT_PROMPT",
    "Sri Lankan English conversation. Place names may include Gampaha, Colombo, Kandy, Galle, Jaffna, and Sri Lanka.",
).strip()
GROQ_TTS_MODEL = os.getenv("GROQ_TTS_MODEL", "canopylabs/orpheus-v1-english").strip() or "canopylabs/orpheus-v1-english"
GROQ_TTS_VOICE = os.getenv("GROQ_TTS_VOICE", "troy").strip().lower() or "troy"
try:
    GROQ_TTS_MIN_INTERVAL_SECONDS = max(0.0, float(os.getenv("GROQ_TTS_MIN_INTERVAL_SECONDS", "6.2")))
except ValueError:
    GROQ_TTS_MIN_INTERVAL_SECONDS = 6.2
WEATHER_PREFERRED_COUNTRY = os.getenv("WEATHER_PREFERRED_COUNTRY", "").strip().casefold()
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Colombo").strip() or "Asia/Colombo"
