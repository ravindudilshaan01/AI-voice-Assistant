import uuid
from typing import Any

from google import genai
from google.genai import types

from app.config import settings
from app.llm.base import LLMProvider, LLMResponse, ToolCall


def _to_gemini_tool(tools: list[dict[str, Any]]) -> types.Tool:
    declarations = [
        types.FunctionDeclaration(
            name=tool["name"],
            description=tool["description"],
            parameters_json_schema=tool["parameters"],
        )
        for tool in tools
    ]
    return types.Tool(function_declarations=declarations)


def _to_gemini_contents(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[types.Content]]:
    """Convert shared agent messages while preserving Gemini function-call parts."""
    system_instructions: list[str] = []
    contents: list[types.Content] = []
    pending_function_responses: list[types.Part] = []

    def flush_function_responses() -> None:
        if pending_function_responses:
            contents.append(
                types.Content(role="user", parts=list(pending_function_responses))
            )
            pending_function_responses.clear()

    for message in messages:
        role = message["role"]

        if role == "system":
            system_instructions.append(message["content"])
            continue

        # The untouched model content carries Gemini 3 thought signatures. It
        # must be replayed before its matching function response.
        provider_content = message.get("provider_content")
        if provider_content is not None:
            flush_function_responses()
            contents.append(provider_content)
            continue

        if role == "tool":
            function_response = types.Part(
                function_response=types.FunctionResponse(
                    name=message["name"],
                    response={"result": message["content"]},
                    id=message.get("tool_call_id"),
                )
            )
            pending_function_responses.append(function_response)
            continue

        flush_function_responses()
        gemini_role = "model" if role == "assistant" else "user"
        contents.append(
            types.Content(
                role=gemini_role,
                parts=[types.Part.from_text(text=message.get("content") or " ")],
            )
        )

    flush_function_responses()
    system_instruction = "\n\n".join(system_instructions) or None
    return system_instruction, contents


class GeminiProvider(LLMProvider):
    def __init__(self):
        if not settings.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Get a key at https://aistudio.google.com/apikey"
            )
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model_name = settings.gemini_model

    async def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        system_instruction, contents = _to_gemini_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[_to_gemini_tool(tools)] if tools else None,
        )

        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )

        if not response.candidates or response.candidates[0].content is None:
            raise RuntimeError("Gemini returned no response content.")

        model_content = response.candidates[0].content
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for part in model_content.parts or []:
            if part.text:
                text_parts.append(part.text)

            function_call = part.function_call
            if function_call and function_call.name:
                tool_calls.append(
                    ToolCall(
                        id=function_call.id or str(uuid.uuid4()),
                        name=function_call.name,
                        arguments=dict(function_call.args or {}),
                    )
                )

        return LLMResponse(
            text=" ".join(text_parts).strip() or None,
            tool_calls=tool_calls,
            provider_content=model_content,
        )
