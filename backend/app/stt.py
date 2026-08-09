import asyncio
import array
import io
import logging
import math
import sys
import wave

from faster_whisper import WhisperModel
from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger("voice-assistant.stt")


class AudioTooQuietError(RuntimeError):
    """Raised when the browser recording contains no usable microphone signal."""

# Loaded once at import time — model load is the slow part, do it at process start,
# not per-request.
_model = WhisperModel(
    settings.whisper_model_size,
    device="cpu",
    compute_type=settings.whisper_compute_type,
)

_gemini_client = (
    genai.Client(api_key=settings.gemini_api_key)
    if settings.gemini_api_key
    else None
)


async def _run_ffmpeg(input_bytes: bytes, audio_filter: str) -> bytes:
    """
    Explicit ffmpeg conversion step: the browser sends a supported recording
    format (usually webm/opus), while whisper wants
    16kHz mono PCM WAV. Doing this ourselves (rather than relying on
    faster-whisper's internal decoder) makes conversion failures visible and
    debuggable instead of silently swallowed.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-i", "pipe:0",
        "-af", audio_filter,
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        "-f", "wav",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    wav_bytes, stderr = await proc.communicate(input=input_bytes)

    if proc.returncode != 0 or not wav_bytes:
        raise RuntimeError(f"ffmpeg conversion failed: {stderr.decode(errors='ignore')}")

    return wav_bytes


async def _audio_to_wav(audio_bytes: bytes) -> bytes:
    """Decode browser audio without boosting it so its real level can be checked."""
    return await _run_ffmpeg(audio_bytes, "highpass=f=80,lowpass=f=7600")


async def _normalize_wav(wav_bytes: bytes) -> bytes:
    return await _run_ffmpeg(
        wav_bytes,
        "dynaudnorm=f=150:g=15:p=0.95:m=20",
    )


def _signal_levels(wav_bytes: bytes) -> tuple[float, float]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        sample_width = wav_file.getsampwidth()
        if sample_width != 2:
            raise RuntimeError(f"Unsupported PCM sample width: {sample_width}")
        frames = wav_file.readframes(wav_file.getnframes())

    samples = array.array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return -120.0, -120.0

    peak = max(abs(sample) for sample in samples)
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    full_scale = 32768.0
    peak_dbfs = 20 * math.log10(max(peak, 1) / full_scale)
    rms_dbfs = 20 * math.log10(max(rms, 1) / full_scale)
    return rms_dbfs, peak_dbfs


async def _transcribe_with_gemini(wav_bytes: bytes) -> str:
    if _gemini_client is None:
        return ""

    content = types.Content(
        role="user",
        parts=[
            types.Part.from_text(
                text=(
                    "Transcribe the speech in this audio accurately. The speaker may "
                    "use Sri Lankan English. Preserve names, Sri Lankan place names, "
                    "dates, times, numbers, product names, and technical terms. Return "
                    "only the spoken words, with no quotes or explanation. If there is "
                    "no intelligible speech, return an empty response."
                )
            ),
            types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
        ],
    )
    response = await _gemini_client.aio.models.generate_content(
        model=settings.gemini_model,
        contents=content,
        config=types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=300,
            thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
        ),
    )
    return (response.text or "").strip().strip('"')


def _transcribe_with_whisper(wav_bytes: bytes) -> str:
    # The user explicitly controls the recording boundary, so running VAD
    # here only risks deleting quiet/accented speech. Decode the complete
    # normalized utterance and let Whisper's no-speech scoring reject noise.
    segments, _info = _model.transcribe(
        io.BytesIO(wav_bytes),
        language=settings.whisper_language,
        beam_size=settings.whisper_beam_size,
        patience=1.2,
        temperature=0.0,
        condition_on_previous_text=False,
        vad_filter=False,
        initial_prompt=(
            "A voice-assistant request spoken in Sri Lankan English. Preserve "
            "Sri Lankan place names, people's names, dates, times, product "
            "names, and technical terms accurately."
        ),
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


async def transcribe(webm_bytes: bytes) -> str:
    raw_wav_bytes = await _audio_to_wav(webm_bytes)
    rms_dbfs, peak_dbfs = _signal_levels(raw_wav_bytes)
    logger.info("Microphone signal level: rms=%.1f dBFS peak=%.1f dBFS", rms_dbfs, peak_dbfs)

    # Do not amplify silence and ask an AI model to guess what it contains.
    if peak_dbfs < -45 or rms_dbfs < -60:
        raise AudioTooQuietError(
            "No clear microphone signal was detected. Select the correct microphone "
            "and check that its input meter moves while you speak."
        )

    wav_bytes = await _normalize_wav(raw_wav_bytes)

    if _gemini_client is not None:
        try:
            transcript = await asyncio.wait_for(
                _transcribe_with_gemini(wav_bytes), timeout=30
            )
            if transcript:
                logger.info("Audio transcribed with Gemini")
                return transcript
        except Exception as exc:
            logger.warning("Gemini audio transcription failed; using Whisper: %s", exc)

    # Local, private fallback for network errors or exhausted cloud quota.
    transcript = await asyncio.to_thread(_transcribe_with_whisper, wav_bytes)
    if transcript:
        logger.info("Audio transcribed with local Whisper fallback")
    return transcript
