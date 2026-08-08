from typing import AsyncIterator

from app.llm import get_llm_provider
from app.llm.base import LLMResponse
from app.tools import TOOL_SCHEMAS, call_tool

SYSTEM_PROMPT = (
    "You are a helpful voice assistant. Keep responses short and conversational — "
    "they will be spoken aloud, not read. Use the available tools when the user asks "
    "for something that needs current information (weather, web search) or an action "
    "(scheduling). Otherwise just answer directly."
)

MAX_TOOL_ROUNDS = 4  # safety cap so a misbehaving model can't loop forever


class Agent:
    def __init__(self):
        self.llm = get_llm_provider()

    async def respond_stream(self, conversation: list[dict]) -> AsyncIterator[dict]:
        """
        Runs the agent loop, yielding events as they happen so the caller can
        forward them to the UI in real time:
          {"type": "tool_call", "name": ..., "arguments": ...}
          {"type": "tool_result", "name": ..., "result": ...}
          {"type": "final", "text": ...}
        """
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *conversation]

        for _ in range(MAX_TOOL_ROUNDS):
            response: LLMResponse = await self.llm.generate(messages, TOOL_SCHEMAS)

            if not response.wants_tool_call:
                yield {"type": "final", "text": response.text or "I'm not sure how to respond to that."}
                return

            assistant_message = {
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    }
                    for tool_call in response.tool_calls
                ],
            }
            if response.provider_content is not None:
                assistant_message["provider_content"] = response.provider_content
            messages.append(assistant_message)

            for tool_call in response.tool_calls:
                yield {"type": "tool_call", "name": tool_call.name, "arguments": tool_call.arguments}

                result = await call_tool(tool_call.name, tool_call.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "name": tool_call.name,
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )

                yield {"type": "tool_result", "name": tool_call.name, "result": result}

        yield {"type": "final", "text": "That request needed too many steps — can you rephrase it?"}
