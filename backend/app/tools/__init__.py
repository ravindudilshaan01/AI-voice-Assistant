from app.tools import scheduler, search, weather

TOOL_SCHEMAS = [weather.SCHEMA, search.SCHEMA, scheduler.SCHEMA]

TOOL_FUNCTIONS = {
    "get_weather": weather.run,
    "web_search": search.run,
    "schedule_event": scheduler.run,
}


async def call_tool(name: str, arguments: dict) -> str:
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    try:
        return await fn(**arguments)
    except Exception as exc:  # noqa: BLE001 — tool failures should surface to the LLM, not crash the loop
        return f"Tool '{name}' failed: {exc}"
