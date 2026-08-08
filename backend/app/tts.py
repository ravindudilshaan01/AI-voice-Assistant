"""
edge-tts: free, no API key, good voice quality. Note this is NOT offline —
it calls Microsoft's Edge read-aloud service over the network. If you need
a genuinely offline TTS fallback, swap this for Piper (see git history /
the previous version of this file for a subprocess-based Piper wrapper).
"""

import edge_tts

from app.config import settings


async def synthesize(text: str) -> bytes:
    if not text.strip():
        text = "I don't have a response for that."

    communicate = edge_tts.Communicate(text, voice=settings.edge_tts_voice)
    audio_chunks = bytearray()

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.extend(chunk["data"])

    if not audio_chunks:
        raise RuntimeError("edge-tts returned no audio data.")

    return bytes(audio_chunks)
