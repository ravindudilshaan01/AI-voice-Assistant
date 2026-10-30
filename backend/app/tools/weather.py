import httpx

from app.tools.errors import ToolError


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CURRENT_FIELDS = "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m"


def weather_description(code: int | None) -> str:
    if code == 0:
        return "Clear sky"
    if code in (1, 2, 3):
        return "Partly cloudy" if code < 3 else "Overcast"
    if code in (45, 48):
        return "Fog"
    if code in (51, 53, 55, 56, 57):
        return "Drizzle"
    if code in (61, 63, 65, 66, 67):
        return "Rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "Snow"
    if code in (80, 81, 82):
        return "Rain showers"
    if code in (95, 96, 99):
        return "Thunderstorm"
    return "Unknown conditions"


async def get_weather(location: str, unit: str = "celsius") -> dict:
    if not isinstance(location, str) or not 2 <= len(location.strip()) <= 100:
        raise ToolError("Provide a location between 2 and 100 characters.")
    if unit not in ("celsius", "fahrenheit"):
        raise ToolError("Temperature unit must be celsius or fahrenheit.")

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
            geocode = await client.get(GEOCODING_URL, params={"name": location.strip(), "count": 1, "language": "en", "format": "json"})
            geocode.raise_for_status()
            places = geocode.json().get("results") or []
            if not places:
                raise ToolError(f"No location found for {location.strip()}.")
            place = places[0]
            if not isinstance(place, dict):
                raise ToolError("Weather service returned incomplete location data.")
            forecast = await client.get(FORECAST_URL, params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": CURRENT_FIELDS,
                "temperature_unit": unit,
                "wind_speed_unit": "kmh" if unit == "celsius" else "mph",
                "precipitation_unit": "mm" if unit == "celsius" else "inch",
                "timezone": place.get("timezone") or "auto",
                "forecast_days": 1,
            })
            forecast.raise_for_status()
            data = forecast.json()
            if not isinstance(data, dict):
                raise ToolError("Weather service returned incomplete data.")
    except ToolError:
        raise
    except httpx.TimeoutException as error:
        raise ToolError("Weather service timed out. Please try again.") from error
    except httpx.HTTPError as error:
        raise ToolError("Weather service is unavailable. Please try again.") from error
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ToolError("Weather service returned incomplete data.") from error

    current = data.get("current")
    units = data.get("current_units")
    if not isinstance(current, dict) or not isinstance(units, dict) or current.get("temperature_2m") is None or not current.get("time"):
        raise ToolError("Weather service returned incomplete data.")
    return {
        "location": ", ".join(filter(None, [place.get("name"), place.get("admin1"), place.get("country")])),
        "timezone": data.get("timezone") or place.get("timezone"),
        "observed_at": current.get("time"),
        "temperature": current.get("temperature_2m"),
        "temperature_unit": units.get("temperature_2m"),
        "feels_like": current.get("apparent_temperature"),
        "relative_humidity": current.get("relative_humidity_2m"),
        "humidity_unit": units.get("relative_humidity_2m"),
        "precipitation": current.get("precipitation"),
        "precipitation_unit": units.get("precipitation"),
        "wind_speed": current.get("wind_speed_10m"),
        "wind_speed_unit": units.get("wind_speed_10m"),
        "weather_code": current.get("weather_code"),
        "condition": weather_description(current.get("weather_code")),
    }
