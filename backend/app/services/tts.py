import asyncio
import logging
import re

from groq import AsyncGroq, RateLimitError

from app.config import GROQ_TTS_MIN_INTERVAL_SECONDS, GROQ_TTS_MODEL, GROQ_TTS_VOICE, TTS_PROVIDER, groq_api_key
from app.services.groq_errors import ServiceError, friendly_error


SUPPORTED_TTS_VOICES = {"male", "female", "troy", "autumn"}
SUPPORTED_TTS_PROVIDERS = {"kokoro", "groq"}
TTS_MODEL = GROQ_TTS_MODEL
TTS_VOICE = GROQ_TTS_VOICE if GROQ_TTS_VOICE in SUPPORTED_TTS_VOICES else "troy"
MAX_TTS_CHARS = 200
SENTENCE_END = re.compile(r"(?<=[.!?])(?:\s+|$)")
UNSPEAKABLE = re.compile(r"[{}]|```|\b(?:tool_calls|tool_call_id|parameters|function_call)\b", re.I)
_tts_rate_lock = asyncio.Lock()
_next_tts_request_at = 0.0
_daily_quota_unavailable_until = 0.0
MAX_RATE_LIMIT_ATTEMPTS = 5
logger = logging.getLogger(__name__)


async def _wait_for_tts_slot() -> None:
    """Serialize request starts to stay below Orpheus' organization RPM limit."""
    global _next_tts_request_at
    async with _tts_rate_lock:
        loop = asyncio.get_running_loop()
        delay = _next_tts_request_at - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        _next_tts_request_at = loop.time() + GROQ_TTS_MIN_INTERVAL_SECONDS


def _rate_limit_details(error: RateLimitError) -> tuple[float, bool]:
    response = getattr(error, "response", None)
    headers = response.headers if response is not None else {}
    remaining_daily = headers.get("x-ratelimit-remaining-requests")
    body = str(getattr(error, "body", "")).casefold()
    daily_exhausted = remaining_daily == "0" or "tokens per day" in body or "tpd" in body
    try:
        retry_after = float(headers.get("retry-after") or GROQ_TTS_MIN_INTERVAL_SECONDS)
    except (TypeError, ValueError):
        retry_after = GROQ_TTS_MIN_INTERVAL_SECONDS
    return max(GROQ_TTS_MIN_INTERVAL_SECONDS, min(retry_after, 30.0)), daily_exhausted


def _quota_wait_text(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"
    hours = max(1, round(minutes / 60))
    return f"about {hours} hour{'s' if hours != 1 else ''}"


def spoken_text(text: str) -> str:
    """Keep only prose; never send JSON, code blocks or tool internals to TTS."""
    if UNSPEAKABLE.search(text) or text.lstrip().startswith(("[", "<")):
        return ""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[`*_#>]", "", text)
    # Orpheus can pronounce punctuation literally or spell the following unit.
    # Convert written ranges and hyphenated durations into natural spoken prose.
    text = re.sub(r"(?<=\d)\s*[-\u2013\u2014]\s*(?=\d)", " to ", text)
    text = re.sub(r"(?<=\d)\s*[-\u2013\u2014]\s*(?=(?:seconds?|minutes?|hours?|days?|weeks?)\b)", " ", text, flags=re.I)
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:mins?|min\.)\b", r"\1 minutes", text, flags=re.I)
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:hrs?|hr\.)\b", r"\1 hours", text, flags=re.I)
    text = re.sub(r"\s+[-\u2013\u2014]\s+", ", ", text)
    # Give the speech model unambiguous words for common numeric notation.
    # Without this, values such as 26.5 can be read digit-by-digit.
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s*\u00b0\s*C\b", r"\1 degrees Celsius", text, flags=re.I)
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s*\u00b0\s*F\b", r"\1 degrees Fahrenheit", text, flags=re.I)
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s*°\s*C\b", r"\1 degrees Celsius", text, flags=re.I)
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s*°\s*F\b", r"\1 degrees Fahrenheit", text, flags=re.I)
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s*km\s*/\s*h\b", r"\1 kilometers per hour", text, flags=re.I)
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s*%", r"\1 percent", text)
    text = re.sub(r"(?<!\w)(-?\d+)\.(\d+)(?!\w)", r"\1 point \2", text)
    return " ".join(text.split()).strip()


def split_long(text: str) -> list[str]:
    result: list[str] = []
    remaining = text.strip()
    while len(remaining) > MAX_TTS_CHARS:
        split_at = remaining.rfind(" ", 0, MAX_TTS_CHARS + 1)
        if split_at < MAX_TTS_CHARS // 2:
            split_at = MAX_TTS_CHARS
        result.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        result.append(remaining)
    return result


class SentenceSegmenter:
    def __init__(self, eager: bool = False) -> None:
        self.buffer = ""
        self.eager = eager
        self._structure_depth = 0
        self._in_string = False
        self._escaped = False
        self._in_code = False
        self._ticks = 0
        self._after_structure = False

    def _prose(self, delta: str) -> str:
        visible: list[str] = []
        for char in delta:
            if char == "`":
                self._ticks += 1
                if self._ticks == 3:
                    self._in_code = not self._in_code
                    self._ticks = 0
                continue
            self._ticks = 0
            if self._in_code:
                continue
            if self._structure_depth:
                if self._in_string:
                    if self._escaped:
                        self._escaped = False
                    elif char == "\\":
                        self._escaped = True
                    elif char == '"':
                        self._in_string = False
                elif char == '"':
                    self._in_string = True
                elif char in "{[":
                    self._structure_depth += 1
                elif char in "}]":
                    self._structure_depth -= 1
                    if self._structure_depth == 0:
                        visible.append(" ")
                        self._after_structure = True
                continue
            if self._after_structure:
                if char.isspace() or char in ".!,;:":
                    continue
                self._after_structure = False
            if char in "{[":
                self._structure_depth = 1
                visible.append(" ")
                continue
            visible.append(char)
        return "".join(visible)

    def feed(self, delta: str) -> list[str]:
        self.buffer += self._prose(delta)
        ready: list[str] = []
        if self.eager:
            while (match := SENTENCE_END.search(self.buffer)) is not None:
                clean = spoken_text(self.buffer[:match.end()])
                if clean:
                    ready.extend(split_long(clean))
                self.buffer = self.buffer[match.end():].lstrip()
        while len(spoken_text(self.buffer)) > MAX_TTS_CHARS:
            # Keep adjacent short sentences together. A single synthesis call
            # preserves voice, pacing and prosody much better than one request
            # per sentence.
            boundary = 0
            for match in SENTENCE_END.finditer(self.buffer):
                if len(spoken_text(self.buffer[:match.end()])) <= MAX_TTS_CHARS:
                    boundary = match.end()
                else:
                    break
            if not boundary:
                boundary = self.buffer.rfind(" ", 0, MAX_TTS_CHARS + 1)
                if boundary < MAX_TTS_CHARS // 2:
                    boundary = MAX_TTS_CHARS
            clean = spoken_text(self.buffer[:boundary])
            if clean:
                ready.extend(split_long(clean))
            self.buffer = self.buffer[boundary:].lstrip()
        return ready

    def flush(self) -> list[str]:
        remaining = self.buffer
        self.buffer = ""
        return split_long(spoken_text(remaining))


async def generate_wav(text: str, voice: str = TTS_VOICE, provider: str | None = None) -> bytes:
    global _daily_quota_unavailable_until
    clean = spoken_text(text)
    if not clean or len(clean) > MAX_TTS_CHARS:
        raise ServiceError("Speech segment is invalid or too long.")
    if voice not in SUPPORTED_TTS_VOICES:
        raise ServiceError("The selected voice is unavailable.")
    selected_provider = provider or TTS_PROVIDER
    if selected_provider == "kokoro":
        from app.services.kokoro_tts import generate_wav as generate_kokoro_wav

        return await generate_kokoro_wav(clean, voice)
    if selected_provider not in SUPPORTED_TTS_PROVIDERS:
        raise ServiceError("The configured TTS provider is unsupported.")
    key = groq_api_key()
    if not key:
        raise ServiceError("Cloud speech is not configured.")
    loop = asyncio.get_running_loop()
    if _daily_quota_unavailable_until > loop.time():
        remaining = _daily_quota_unavailable_until - loop.time()
        raise ServiceError(f"Groq's daily natural-voice quota is used. It should reset in {_quota_wait_text(remaining)}.")
    for attempt in range(MAX_RATE_LIMIT_ATTEMPTS):
        await _wait_for_tts_slot()
        try:
            async with AsyncGroq(api_key=key, timeout=30.0, max_retries=0) as client:
                groq_voice = "autumn" if voice in ("female", "autumn") else "troy"
                response = await client.audio.speech.create(
                    model=TTS_MODEL, voice=groq_voice, input=clean, response_format="wav"
                )
                audio = await response.read()
        except RateLimitError as error:
            retry_after, daily_exhausted = _rate_limit_details(error)
            response = getattr(error, "response", None)
            headers = response.headers if response is not None else {}
            logger.warning(
                "Groq TTS limits: retry_after=%s remaining_requests=%s remaining_tokens=%s reset_requests=%s reset_tokens=%s",
                headers.get("retry-after"), headers.get("x-ratelimit-remaining-requests"),
                headers.get("x-ratelimit-remaining-tokens"), headers.get("x-ratelimit-reset-requests"),
                headers.get("x-ratelimit-reset-tokens"),
            )
            if daily_exhausted:
                response = getattr(error, "response", None)
                headers = response.headers if response is not None else {}
                try:
                    reset_after = float(headers.get("retry-after") or 3600)
                except (TypeError, ValueError):
                    reset_after = 3600.0
                _daily_quota_unavailable_until = asyncio.get_running_loop().time() + reset_after
                raise ServiceError(f"Groq's daily natural-voice quota is used. It should reset in {_quota_wait_text(reset_after)}.") from error
            if attempt + 1 >= MAX_RATE_LIMIT_ATTEMPTS:
                raise ServiceError("Groq's natural voice is still rate-limited after waiting for a new request slot.") from error
            logger.warning("Groq TTS rate limit; retrying attempt %s/%s in %.1f seconds", attempt + 2, MAX_RATE_LIMIT_ATTEMPTS, retry_after)
            await asyncio.sleep(retry_after)
            continue
        except Exception as error:
            raise friendly_error(error, "Speech generation") from error
        if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ServiceError("Cloud speech returned invalid audio.")
        return audio
    raise ServiceError("Natural voice generation did not complete.")
