import logging
from time import monotonic

from groq import AsyncGroq

from app.config import GROQ_STT_LANGUAGE, GROQ_STT_MODEL, groq_api_key
from app.services.groq_errors import ServiceError, friendly_error


SUPPORTED_MIME_TYPES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}
MAX_AUDIO_BYTES = 10 * 1024 * 1024
logger = logging.getLogger(__name__)


def _confidence(result: object, name: str) -> float | None:
    value = getattr(result, name, None)
    if value is None and isinstance(result, dict):
        value = result.get(name)
    return value if isinstance(value, (int, float)) else None


def _validated_text(result: object) -> str:
    text = str(getattr(result, "text", result.get("text", "") if isinstance(result, dict) else "")).strip()
    no_speech_prob = _confidence(result, "no_speech_prob")
    avg_logprob = _confidence(result, "avg_logprob")
    logger.info("STT confidence no_speech_prob=%s avg_logprob=%s", no_speech_prob, avg_logprob)
    if not text:
        logger.info("STT rejection_source=backend_empty_transcript")
        raise ServiceError("No clear speech detected. Please try again.")
    if no_speech_prob is not None and avg_logprob is not None and no_speech_prob >= 0.98 and avg_logprob <= -2.5:
        logger.info("STT rejection_source=backend_low_confidence")
        raise ServiceError("No clear speech detected. Please try again.")
    return text


async def transcribe(audio: bytes, mime_type: str) -> str:
    started = monotonic()
    extension = SUPPORTED_MIME_TYPES.get(mime_type.split(";", 1)[0].lower())
    if not extension:
        raise ServiceError("This browser's recording format is not supported. Try Chrome, Edge, or Firefox.")
    if not audio:
        raise ServiceError("The recording was empty. Please try again.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise ServiceError("Recording is too large. Please record a shorter message.")
    key = groq_api_key()
    if not key:
        raise ServiceError("Groq is not configured. Add GROQ_API_KEY to backend/.env.")
    try:
        async with AsyncGroq(api_key=key, timeout=30.0, max_retries=0) as client:
            request = {
                "model": GROQ_STT_MODEL,
                "file": (f"recording.{extension}", audio, mime_type),
                "response_format": "verbose_json",
                "temperature": 0,
            }
            if GROQ_STT_LANGUAGE != "auto":
                request["language"] = GROQ_STT_LANGUAGE
            result = await client.audio.transcriptions.create(**request)
        text = _validated_text(result)
        logger.info("STT completed duration_ms=%d audio_bytes=%d mime_type=%s", round((monotonic() - started) * 1000), len(audio), mime_type.split(";", 1)[0])
        return text
    except ServiceError:
        raise
    except Exception as error:
        raise friendly_error(error, "Transcription") from error
