import httpx

from app.tools.errors import ToolError


SEARCH_URL = "https://api.duckduckgo.com/"


async def web_search(query: str) -> dict:
    query = query.strip()
    if not 2 <= len(query) <= 200:
        raise ToolError("Provide a search query between 2 and 200 characters.")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
            response = await client.get(
                SEARCH_URL,
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            )
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException as error:
        raise ToolError("Search timed out. Please try again.") from error
    except (httpx.HTTPError, ValueError) as error:
        raise ToolError("Search is unavailable. Please try again.") from error

    answer = data.get("AbstractText") or data.get("Answer")
    if not answer:
        for item in data.get("RelatedTopics") or []:
            if isinstance(item, dict) and item.get("Text"):
                answer = item["Text"]
                break
    if not answer:
        raise ToolError(f"No quick answer was found for {query}.")
    return {"query": query, "answer": answer, "source": data.get("AbstractURL") or None}
