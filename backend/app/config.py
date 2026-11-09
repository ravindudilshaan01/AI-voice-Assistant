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
TOOL_LLM_MODEL = os.getenv("GROQ_TOOL_MODEL", "openai/gpt-oss-20b").strip() or "openai/gpt-oss-20b"
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
FRONTEND_ORIGINS = {
    FRONTEND_ORIGIN.rstrip("/"),
    "http://localhost:5173",
    "http://127.0.0.1:5173",
}
GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
GROQ_STT_LANGUAGE = os.getenv("GROQ_STT_LANGUAGE", "en").strip().lower() or "en"
GROQ_STT_PROMPT = os.getenv(
    "GROQ_STT_PROMPT",
    "Sri Lankan English conversation. Place names may include Gampaha, Colombo, Kandy, Galle, Jaffna, and Sri Lanka.",
).strip()
GROQ_TTS_MODEL = os.getenv("GROQ_TTS_MODEL", "canopylabs/orpheus-v1-english").strip() or "canopylabs/orpheus-v1-english"
GROQ_TTS_VOICE = os.getenv("GROQ_TTS_VOICE", "troy").strip().lower() or "troy"
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "groq").strip().lower() or "groq"
KOKORO_MODEL_PATH = Path(os.getenv("KOKORO_MODEL_PATH", str(PROJECT_ROOT / "backend" / "models" / "kokoro" / "kokoro-v1.0.int8.onnx")))
KOKORO_VOICES_PATH = Path(os.getenv("KOKORO_VOICES_PATH", str(PROJECT_ROOT / "backend" / "models" / "kokoro" / "voices-v1.0.bin")))
if not KOKORO_MODEL_PATH.is_absolute():
    KOKORO_MODEL_PATH = PROJECT_ROOT / KOKORO_MODEL_PATH
if not KOKORO_VOICES_PATH.is_absolute():
    KOKORO_VOICES_PATH = PROJECT_ROOT / KOKORO_VOICES_PATH
KOKORO_MALE_VOICE = os.getenv("KOKORO_MALE_VOICE", "am_michael").strip() or "am_michael"
KOKORO_FEMALE_VOICE = os.getenv("KOKORO_FEMALE_VOICE", "af_bella").strip() or "af_bella"
ONNX_PROVIDER = os.getenv("ONNX_PROVIDER", "CPUExecutionProvider").strip() or "CPUExecutionProvider"
KOKORO_PRELOAD = os.getenv("KOKORO_PRELOAD", "1").strip() == "1"
TTS_AUTO_FALLBACK = os.getenv("TTS_AUTO_FALLBACK", "1").strip() == "1"
TTS_QUEUE_SIZE = max(1, min(int(os.getenv("TTS_QUEUE_SIZE", "8")), 32))
TOOL_TIMEOUT_SECONDS = max(1.0, min(float(os.getenv("TOOL_TIMEOUT_SECONDS", "12")), 60.0))
try:
    GROQ_TTS_MIN_INTERVAL_SECONDS = max(0.0, float(os.getenv("GROQ_TTS_MIN_INTERVAL_SECONDS", "6.2")))
except ValueError:
    GROQ_TTS_MIN_INTERVAL_SECONDS = 6.2
WEATHER_PREFERRED_COUNTRY = os.getenv("WEATHER_PREFERRED_COUNTRY", "").strip().casefold()
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Colombo").strip() or "Asia/Colombo"
