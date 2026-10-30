import re

from groq import AsyncGroq

from app.config import groq_api_key
from app.services.groq_errors import ServiceError, friendly_error


TTS_MODEL = "canopylabs/orpheus-v1-english"
TTS_VOICE = "troy"
MAX_TTS_CHARS = 200
SENTENCE_END = re.compile(r"(?<=[.!?])(?:\s+|$)")
UNSPEAKABLE = re.compile(r"[{}]|```|\b(?:tool_calls|tool_call_id|parameters|function_call)\b", re.I)


def spoken_text(text: str) -> str:
    """Keep only prose; never send JSON, code blocks or tool internals to TTS."""
    if UNSPEAKABLE.search(text) or text.lstrip().startswith(("[", "<")):
        return ""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[`*_#>]", "", text)
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
    def __init__(self) -> None:
        self.buffer = ""
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
        while True:
            match = SENTENCE_END.search(self.buffer)
            if match:
                sentence = self.buffer[:match.end()].strip()
                self.buffer = self.buffer[match.end():]
                clean = spoken_text(sentence)
                ready.extend(split_long(clean))
            elif len(self.buffer) > MAX_TTS_CHARS * 2:
                # A long sentence with no punctuation still needs bounded chunks.
                split_at = self.buffer.rfind(" ", 0, MAX_TTS_CHARS + 1)
                if split_at < MAX_TTS_CHARS // 2:
                    split_at = MAX_TTS_CHARS
                clean = spoken_text(self.buffer[:split_at])
                ready.extend(split_long(clean))
                self.buffer = self.buffer[split_at:].lstrip()
            else:
                break
        return ready

    def flush(self) -> list[str]:
        remaining = self.buffer
        self.buffer = ""
        return split_long(spoken_text(remaining))


async def generate_wav(text: str) -> bytes:
    clean = spoken_text(text)
    if not clean or len(clean) > MAX_TTS_CHARS:
        raise ServiceError("Speech segment is invalid or too long.")
    key = groq_api_key()
    if not key:
        raise ServiceError("Cloud speech is not configured.")
    try:
        async with AsyncGroq(api_key=key, timeout=20.0, max_retries=0) as client:
            response = await client.audio.speech.create(
                model=TTS_MODEL, voice=TTS_VOICE, input=clean, response_format="wav"
            )
            audio = await response.read()
    except Exception as error:
        raise friendly_error(error, "Speech generation") from error
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise ServiceError("Cloud speech returned invalid audio.")
    return audio
