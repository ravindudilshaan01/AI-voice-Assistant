import json
import logging
import re
from decimal import Decimal, InvalidOperation
from time import monotonic
from collections.abc import AsyncIterator
from typing import Any

from app.config import LLM_MODEL, TOOL_LLM_MODEL
from app.services.groq_errors import ServiceError, friendly_error
from app.services.llm import MAX_TURNS, SYSTEM_PROMPT, make_client
from app.tools.errors import ToolError
from app.tools.registry import TOOL_SCHEMAS, TOOLS, execute_tool
from app.services.metrics import metrics


MAX_TOOL_ROUNDS = 3
MAX_CALLS_PER_ROUND = 4
logger = logging.getLogger(__name__)
DETAIL_REQUEST = re.compile(r"\b(?:explain|elaborate|detail|detailed|in-depth|step[- ]by[- ]step|why|how does|how do|compare|break down)\b", re.I)
REMINDER_REQUEST = re.compile(r"\b(?:remind|reminder|set\s+(?:an?\s+)?alarm)\b", re.I)
WEATHER_REQUEST = re.compile(r"\b(?:weather|temperature|forecast|rain(?:ing)?|humidity)\b", re.I)
CALCULATION_REQUEST = re.compile(r"\b(?:calculate|compute|multiply|multiplied|times|divide|divided|plus|minus|sum|product)\b|\d\s*[+*/%-]\s*\d", re.I)
SEARCH_REQUEST = re.compile(r"\b(?:search(?:\s+the\s+web)?|look\s+up|find\s+online|latest|current\s+(?:version|release|news))\b", re.I)
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def preferred_tool_for(user_text: str, round_number: int) -> str | None:
    if round_number != 0:
        return None
    intents = [
        (REMINDER_REQUEST.search(user_text), "schedule_event"),
        (WEATHER_REQUEST.search(user_text), "get_weather"),
        (CALCULATION_REQUEST.search(user_text), "calculate"),
        (SEARCH_REQUEST.search(user_text), "web_search"),
    ]
    selected = [name for match, name in intents if match]
    if len(selected) != 1:
        return None
    return selected[0]


def needs_tool_model(user_text: str) -> bool:
    return any(pattern.search(user_text) for pattern in (REMINDER_REQUEST, WEATHER_REQUEST, CALCULATION_REQUEST, SEARCH_REQUEST))


def tool_choice_for(user_text: str, round_number: int) -> str:
    if preferred_tool_for(user_text, round_number):
        return "required"
    return "auto"


def tool_schemas_for(user_text: str, round_number: int) -> list[dict[str, Any]]:
    preferred = preferred_tool_for(user_text, round_number)
    return [TOOLS[preferred].groq_schema] if preferred else TOOL_SCHEMAS


def deterministic_multi_request(user_text: str) -> list[tuple[str, str]]:
    """Extract only unambiguous compound requests before asking the model to orchestrate them."""
    requests: list[tuple[str, str]] = []
    weather = re.search(r"\bweather\s+(?:in|for)\s+([A-Za-z][A-Za-z .'-]*?)(?=\s*,?\s*(?:check|and|calculate|what|should|remind|after)\b|[.?]|$)", user_text, re.I)
    if not weather and WEATHER_REQUEST.search(user_text):
        weather = re.search(r"\bin\s+([A-Za-z][A-Za-z .'-]*?)(?=\s*,\s*(?:check|and|calculate|what|should|remind)\b)", user_text, re.I)
    if weather:
        location = weather.group(1).strip(" ,")
        if location:
            requests.append(("get_weather", json.dumps({"location": location, "unit": "celsius"})))

    discount = re.search(r"(?:final\s+price|price).*?of\s+([\d,]+(?:\.\d+)?)\s+(?:purchase|buying).*?(\d+(?:\.\d+)?)\s*%\s*discount", user_text, re.I)
    if not discount:
        discount = re.search(r"([\d,]+(?:\.\d+)?)\s+(?:purchase|buying).*?(\d+(?:\.\d+)?)\s*%\s*discount", user_text, re.I)
    if discount:
        try:
            amount = Decimal(discount.group(1).replace(",", ""))
            percent = Decimal(discount.group(2))
            expression = f"({amount})*(1-({percent})/100)"
            requests.append(("calculate", json.dumps({"expression": expression})))
        except InvalidOperation:
            pass

    reminder = re.search(r"\bremind\s+me\s+in\s+(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(minute|minutes|hour|hours)\s+to\s+(.+?)(?:[.?]|$)", user_text, re.I)
    if reminder:
        raw_amount = reminder.group(1).casefold()
        amount = NUMBER_WORDS.get(raw_amount, int(raw_amount) if raw_amount.isdigit() else 0)
        unit = reminder.group(2).casefold()
        title = reminder.group(3).strip()
        if amount > 0 and title:
            requests.append(("schedule_event", json.dumps({"title": title, "when": f"in {amount} {unit}"})))
    return requests if len(requests) >= 2 else []


async def stream_agent(history: list[dict[str, str]], user_text: str) -> AsyncIterator[dict[str, Any]]:
    """Stream text and tool activity. Tool messages live only within this request."""
    deterministic_requests = deterministic_multi_request(user_text)
    if deterministic_requests:
        for name, arguments in deterministic_requests:
            label = TOOLS[name].label
            yield {"type": "tool_activity", "name": name, "label": label, "state": "started"}
            try:
                result = await execute_tool(name, arguments)
                yield {"type": "tool_result", "name": name, "label": label, "ok": True, "result": result}
            except ToolError as error:
                yield {"type": "tool_result", "name": name, "label": label, "ok": False, "error": str(error)}
            yield {"type": "tool_activity", "name": name, "label": label, "state": "done"}
        return
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history[-MAX_TURNS * 2 :],
        {"role": "user", "content": user_text},
    ]
    completed_calls: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        async with make_client() as client:
            for round_number in range(MAX_TOOL_ROUNDS + 1):
                # One streaming request answers ordinary questions; tool use adds rounds only when requested.
                stream = await client.chat.completions.create(
                    model=TOOL_LLM_MODEL if needs_tool_model(user_text) else LLM_MODEL,
                    messages=messages,
                    tools=tool_schemas_for(user_text, round_number),
                    tool_choice=tool_choice_for(user_text, round_number) if round_number < MAX_TOOL_ROUNDS else "none",
                    stream=True,
                    max_tokens=600 if DETAIL_REQUEST.search(user_text) else 100,
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
                    raise ServiceError("The assistant could not complete the tool request. Please try a simpler question.")
                if len(pending_calls) > MAX_CALLS_PER_ROUND:
                    raise ServiceError("The assistant requested too many tools at once. Please try a simpler question.")

                calls = [pending_calls[index] for index in sorted(pending_calls)]
                if any(not call["id"] or not call["name"] for call in calls):
                    raise ServiceError("The assistant received an incomplete tool request. Please try again.")
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
                    yield {"type": "tool_activity", "name": name, "label": label, "state": "started"}
                    tool_started = monotonic()
                    try:
                        signature = (name, call["arguments"])
                        if signature in completed_calls:
                            payload = completed_calls[signature]
                            await metrics.increment(f"tool.{name}.deduplicated")
                        else:
                            result = await execute_tool(name, call["arguments"])
                            payload = {"ok": True, "result": result}
                            completed_calls[signature] = payload
                            await metrics.increment(f"tool.{name}.success")
                    except ToolError as error:
                        payload = {"ok": False, "error": str(error)}
                        await metrics.increment(f"tool.{name}.failure")
                    except Exception:
                        payload = {"ok": False, "error": "Tool failed. Please try again."}
                        await metrics.increment(f"tool.{name}.failure")
                    await metrics.observe(f"tool.{name}.latency", (monotonic() - tool_started) * 1000)
                    messages.append({
                        "role": "tool", "tool_call_id": call["id"], "name": name,
                        "content": json.dumps(payload),
                    })
                    yield {
                        "type": "tool_result", "name": name, "label": label,
                        "ok": payload["ok"],
                        **({"result": payload["result"]} if payload["ok"] else {"error": payload["error"]}),
                    }
                    yield {"type": "tool_activity", "name": name, "label": label, "state": "done"}
    except ServiceError:
        raise
    except Exception as error:
        logger.exception("Groq chat stream failed")
        raise friendly_error(error, "Chat") from error
