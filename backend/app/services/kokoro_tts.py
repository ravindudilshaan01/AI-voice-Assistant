"""Lazy, thread-isolated Kokoro ONNX synthesis."""

import io
import threading
from functools import lru_cache

from app.config import KOKORO_FEMALE_VOICE, KOKORO_MALE_VOICE, KOKORO_MODEL_PATH, KOKORO_VOICES_PATH
from app.services.groq_errors import ServiceError


_model = None
_model_lock = threading.Lock()
_synthesis_lock = threading.Lock()


def _get_model():
    global _model
    if _model is not None:
        return _model
    if not KOKORO_MODEL_PATH.is_file() or not KOKORO_VOICES_PATH.is_file():
        raise ServiceError(
            "Kokoro voice files are missing. Download the model into backend/models/kokoro or switch TTS_PROVIDER to groq."
        )
    try:
        from kokoro_onnx import Kokoro
    except ImportError as error:
        raise ServiceError("Kokoro is not installed. Install backend requirements and restart the server.") from error
    with _model_lock:
        if _model is None:
            _model = Kokoro(str(KOKORO_MODEL_PATH), str(KOKORO_VOICES_PATH))
    return _model


def warmup() -> None:
    """Load the ONNX graph without synthesizing audio."""
    _get_model()


def _voice_name(voice: str) -> str:
    if voice in ("female", "autumn"):
        return KOKORO_FEMALE_VOICE
    return KOKORO_MALE_VOICE


@lru_cache(maxsize=64)
def _synthesize_cached(text: str, voice: str) -> bytes:
    try:
        import numpy as np
        import soundfile as sf
    except ImportError as error:
        raise ServiceError("Kokoro audio support is not installed. Install backend requirements and restart the server.") from error
    try:
        # The ONNX runtime/model is shared but synthesis is serialized for stable CPU use.
        with _synthesis_lock:
            samples, sample_rate = _get_model().create(text, voice=_voice_name(voice), speed=1.0, lang="en-us")
            samples = np.asarray(samples, dtype=np.float32)
            peak = float(np.max(np.abs(samples))) if samples.size else 0.0
            if peak > 0.001:
                # Raise quiet generations while retaining headroom and avoiding clipping.
                samples = samples * min(0.89 / peak, 2.5)
            buffer = io.BytesIO()
            sf.write(buffer, samples, sample_rate, format="WAV", subtype="PCM_16")
            return buffer.getvalue()
    except ServiceError:
        raise
    except Exception as error:
        raise ServiceError("Kokoro could not synthesize this response. Please try again.") from error


async def generate_wav(text: str, voice: str) -> bytes:
    import asyncio

    return await asyncio.to_thread(_synthesize_cached, text, voice)
