import json
from collections.abc import AsyncIterator
from typing import Any

from app.config import LLM_MODEL
from app.services.groq_errors import ServiceError, friendly_error
from app.services.llm import MAX_TURNS, SYSTEM_PROMPT, make_client
from app.tools.errors import ToolError
from app.tools.registry import TOOL_SCHEMAS, TOOLS, execute_tool


MAX_TOOL_ROUNDS = 3
MAX_CALLS_PER_ROUND = 4


async def stream_agent(history: list[dict[str, str]], user_text: str) -> AsyncIterator[dict[str, str]]:
    """Stream text and tool activity. Tool messages live only within this request."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history[-MAX_TURNS * 2 :],
        {"role": "user", "content": user_text},
    ]
    try:
        async with make_client() as client:
            for round_number in range(MAX_TOOL_ROUNDS + 1):
                # One streaming request answers ordinary questions; tool use adds rounds only when requested.
                stream = await client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto" if round_number < MAX_TOOL_ROUNDS else "none",
                    stream=True,
                    max_tokens=600,
                )
                content_parts: list[str] = []
                pending_calls: dict[int, dict[str, str]] = {}
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        content_parts.append(delta.content)
                        yield {"type": "chat_delta", "text": delta.content}
                    for call in delta.tool_calls or []:
                        current = pending_calls.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
                        if call.id:
                            current["id"] = call.id
                        if call.function:
                            current["name"] += call.function.name or ""
                            current["arguments"] += call.function.arguments or ""

                if not pending_calls:
                    return
                if round_number == MAX_TOOL_ROUNDS:
                    raise ServiceError("NOVA could not complete the tool request. Please try a simpler question.")
                if len(pending_calls) > MAX_CALLS_PER_ROUND:
                    raise ServiceError("NOVA requested too many tools at once. Please try a simpler question.")

                calls = [pending_calls[index] for index in sorted(pending_calls)]
                if any(not call["id"] or not call["name"] for call in calls):
                    raise ServiceError("NOVA received an incomplete tool request. Please try again.")
                messages.append({
                    "role": "assistant",
                    "content": "".join(content_parts) or None,
                    "tool_calls": [
                        {"id": call["id"], "type": "function", "function": {
                            "name": call["name"], "arguments": call["arguments"],
                        }} for call in calls
                    ],
                })
                for call in calls:
                    name = call["name"]
                    label = TOOLS[name].label if name in TOOLS else "Using tool"
                    yield {"type": "tool_activity", "label": label, "state": "started"}
                    try:
                        result = await execute_tool(name, call["arguments"])
                        payload = {"ok": True, "result": result}
                    except ToolError as error:
                        payload = {"ok": False, "error": str(error)}
                    except Exception:
                        payload = {"ok": False, "error": "Tool failed. Please try again."}
                    messages.append({
                        "role": "tool", "tool_call_id": call["id"], "name": name,
                        "content": json.dumps(payload),
                    })
                    yield {"type": "tool_activity", "label": label, "state": "done"}
    except ServiceError:
        raise
    except Exception as error:
        raise friendly_error(error, "Chat") from error
