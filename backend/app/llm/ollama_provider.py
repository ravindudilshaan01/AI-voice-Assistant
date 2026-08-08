import uuid
from typing import Any

import httpx

from app.config import settings
from app.llm.base import LLMProvider, LLMResponse, ToolCall


def _to_ollama_tool_schema(tools: list[dict[str, Any]]) -> list[dict]:
    """Ollama's /api/chat tools param follows the OpenAI function-calling shape."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in tools
    ]


def _to_ollama_messages(messages: list[dict[str, Any]]) -> list[dict]:
    out = []
    for m in messages:
        if m["role"] == "tool":
            out.append(
                {
                    "role": "tool",
                    "content": m["content"],
                    "tool_name": m["name"],
                }
            )
        else:
            message = {"role": m["role"], "content": m["content"]}
            if m.get("tool_calls"):
                message["tool_calls"] = [
                    {
                        "function": {
                            "name": tool_call["name"],
                            "arguments": tool_call["arguments"],
                        }
                    }
                    for tool_call in m["tool_calls"]
                ]
            out.append(message)
    return out


class OllamaProvider(LLMProvider):
    """
    Talks to a local Ollama server (default: the `ollama` service in docker-compose).
    Requires a model pulled with tool-calling support, e.g.:
        docker compose exec ollama ollama pull qwen2.5:3b
    """

    def __init__(self):
        self.base_url = settings.ollama_host
        self.model = settings.ollama_model

    async def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": _to_ollama_messages(messages),
            "stream": False,
        }
        if tools:
            payload["tools"] = _to_ollama_tool_schema(tools)

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        message = data.get("message", {})
        tool_calls = [
            ToolCall(
                id=str(uuid.uuid4()),
                name=tc["function"]["name"],
                arguments=tc["function"].get("arguments", {}),
            )
            for tc in message.get("tool_calls", []) or []
        ]

        return LLMResponse(
            text=message.get("content") or None,
            tool_calls=tool_calls,
        )
