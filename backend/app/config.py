import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def groq_api_key() -> str:
    key = os.getenv("GROQ_API_KEY", "").strip()
    return "" if key == "your_groq_api_key_here" else key


LLM_MODEL = os.getenv("GROQ_LLM_MODEL", "llama-3.1-8b-instant")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
GROQ_STT_LANGUAGE = os.getenv("GROQ_STT_LANGUAGE", "en").strip().lower() or "en"
