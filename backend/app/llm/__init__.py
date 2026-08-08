from app.config import settings
from app.llm.base import LLMProvider


def get_llm_provider() -> LLMProvider:
    if settings.llm_provider == "gemini":
        from app.llm.gemini_provider import GeminiProvider

        return GeminiProvider()
    elif settings.llm_provider == "ollama":
        from app.llm.ollama_provider import OllamaProvider

        return OllamaProvider()
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
