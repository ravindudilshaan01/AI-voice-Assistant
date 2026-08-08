import httpx

SCHEMA = {
    "name": "web_search",
    "description": "Search the web for a quick factual answer or summary on a topic.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
        },
        "required": ["query"],
    },
}


async def run(query: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
        )
        data = resp.json()

    abstract = data.get("AbstractText")
    if abstract:
        return abstract

    related = data.get("RelatedTopics", [])
    for item in related:
        if isinstance(item, dict) and item.get("Text"):
            return item["Text"]

    return f"No quick summary found for '{query}'."
