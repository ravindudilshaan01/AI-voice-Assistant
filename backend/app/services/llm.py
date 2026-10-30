from groq import AsyncGroq

from app.config import LLM_MODEL, groq_api_key
from app.services.groq_errors import ServiceError


SYSTEM_PROMPT = (
    "You are NOVA, a helpful conversational voice assistant. "
    "Give clear, concise, natural answers suitable for speaking aloud. "
    "Be honest when you do not know something. "
    "Use tools when current weather, web information, exact arithmetic, or an in-session reminder is needed. "
    "Do not invent tool results."
)
MAX_TURNS = 10
MAX_TEXT_CHARS = 4000


def make_client() -> AsyncGroq:
    key = groq_api_key()
    if not key:
        raise ServiceError("Groq is not configured. Add GROQ_API_KEY to backend/.env.")
    return AsyncGroq(api_key=key, timeout=30.0, max_retries=0)
