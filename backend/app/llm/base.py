from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Provider-native assistant content is retained during a tool round. Gemini
    # 3 requires the original function-call part (including its thought
    # signature) to be sent back alongside the function result.
    provider_content: Any | None = None

    @property
    def wants_tool_call(self) -> bool:
        return len(self.tool_calls) > 0


class LLMProvider(ABC):
    """
    Common interface so the agent loop doesn't care whether it's talking
    to a cloud API (Gemini) or a local model server (Ollama). Swap providers
    with the LLM_PROVIDER env var — no changes to agent.py needed.
    """

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        """Send the conversation + available tool schemas, get back text or tool calls."""
        raise NotImplementedError
