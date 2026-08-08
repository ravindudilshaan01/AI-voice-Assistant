import asyncio
import io

from faster_whisper import WhisperModel

from app.config import settings

# Loaded once at import time — model load is the slow part, do it at process start,
# not per-request.
_model = WhisperModel(
    settings.whisper_model_size,
    device="cpu",
    compute_type=settings.whisper_compute_type,
)


async def _audio_to_wav(audio_bytes: bytes) -> bytes:
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
        "-ar", "16000",
        "-ac", "1",
        "-f", "wav",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    wav_bytes, stderr = await proc.communicate(input=audio_bytes)

    if proc.returncode != 0 or not wav_bytes:
        raise RuntimeError(f"ffmpeg conversion failed: {stderr.decode(errors='ignore')}")

    return wav_bytes


async def transcribe(webm_bytes: bytes) -> str:
    wav_bytes = await _audio_to_wav(webm_bytes)

    def run_whisper() -> str:
        segments, _info = _model.transcribe(
            io.BytesIO(wav_bytes),
            language=settings.whisper_language,
            beam_size=settings.whisper_beam_size,
            vad_filter=True,
            vad_parameters={
                "min_silence_duration_ms": 350,
                "speech_pad_ms": 250,
            },
            condition_on_previous_text=False,
            initial_prompt=(
                "A clear voice-assistant command or question. Preserve place names, "
                "people's names, dates, times, and technical terms accurately."
            ),
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    # Whisper is CPU-bound and synchronous; keep it off the FastAPI event loop.
    return await asyncio.to_thread(run_whisper)
