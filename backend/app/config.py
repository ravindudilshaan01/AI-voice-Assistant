import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


# Docker Compose injects these values directly. Loading the project-level file
# as a fallback also makes `uvicorn app.main:app` work during local development.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@dataclass
class Settings:
    # Which LLM backend to use: "gemini" (default here) or "ollama" (fully local)
    llm_provider: str = os.getenv("LLM_PROVIDER", "gemini")

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

    ollama_host: str = os.getenv("OLLAMA_HOST", "http://ollama:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

    # int8 quantization keeps this usable on CPU-only laptops
    whisper_model_size: str = os.getenv("WHISPER_MODEL_SIZE", "small.en")
    whisper_compute_type: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    whisper_language: str | None = os.getenv("WHISPER_LANGUAGE", "en").strip() or None
    whisper_beam_size: int = int(os.getenv("WHISPER_BEAM_SIZE", "5"))

    edge_tts_voice: str = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")

    cors_origins: list[str] = None

    def __post_init__(self):
        if self.cors_origins is None:
            self.cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")


settings = Settings()
