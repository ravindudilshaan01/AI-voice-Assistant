import httpx

SCHEMA = {
    "name": "get_weather",
    "description": "Get the current weather for a city.",
    "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name, e.g. Colombo"},
        },
        "required": ["city"],
    },
}


async def run(city: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        geo = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1},
        )
        geo_data = geo.json()
        results = geo_data.get("results")
        if not results:
            return f"Could not find a location called '{city}'."

        lat, lon = results[0]["latitude"], results[0]["longitude"]
        resolved_name = results[0]["name"]

        weather = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "current_weather": True},
        )
        current = weather.json().get("current_weather", {})

    if not current:
        return f"Weather data unavailable for {resolved_name} right now."

    return (
        f"Current weather in {resolved_name}: {current.get('temperature')}°C, "
        f"wind {current.get('windspeed')} km/h."
    )
