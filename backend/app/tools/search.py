import httpx
from datetime import datetime
from zoneinfo import ZoneInfo

from groq import AsyncGroq

from app.config import APP_TIMEZONE, groq_api_key
from app.tools.errors import ToolError


SEARCH_URL = "https://api.duckduckgo.com/"
SEARCH_MODEL = "openai/gpt-oss-120b"


async def _browser_search(query: str) -> dict:
    key = groq_api_key()
    if not key:
        raise ToolError("Current web search is not configured.")
    today = datetime.now(ZoneInfo(APP_TIMEZONE)).strftime("%B %d, %Y")
    prompt = (
        f"Today is {today}. Search the web for: {query}. "
        "Verify the answer from reliable, preferably primary sources. "
        "Answer in under 80 words and include the best source URL."
    )
    try:
        async with AsyncGroq(api_key=key, timeout=60.0, max_retries=2) as client:
            response = await client.chat.completions.create(
                model=SEARCH_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[{"type": "browser_search"}],
                reasoning_effort="medium",
                max_completion_tokens=1200,
            )
    except Exception as error:
        raise ToolError("Current web search is temporarily unavailable. Please try again.") from error

    message = response.choices[0].message
    answer = (message.content or "").strip()
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for tool in getattr(message, "executed_tools", None) or []:
        results = getattr(getattr(tool, "search_results", None), "results", None) or []
        for item in results:
            url = str(getattr(item, "url", "") or "").strip()
            if not url.startswith(("https://", "http://")) or url in seen:
                continue
            seen.add(url)
            sources.append({"title": str(getattr(item, "title", "") or url), "url": url})
            if len(sources) == 3:
                break
        if len(sources) == 3:
            break
    if not answer:
        raise ToolError("No reliable current answer was found. Try a more specific search.")
    return {"query": query, "answer": answer, "source": sources[0]["url"] if sources else None, "sources": sources}


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
        return await _browser_search(query)
    return {"query": query, "answer": answer, "source": data.get("AbstractURL") or None}
