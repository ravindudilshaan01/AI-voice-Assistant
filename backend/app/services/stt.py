from groq import AsyncGroq

from app.config import groq_api_key
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


async def transcribe(audio: bytes, mime_type: str) -> str:
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
            result = await client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=(f"recording.{extension}", audio, mime_type),
                response_format="json",
            )
        text = result.text.strip()
        if not text:
            raise ServiceError("No speech was recognized. Please try again.")
        return text
    except ServiceError:
        raise
    except Exception as error:
        raise friendly_error(error, "Transcription") from error
