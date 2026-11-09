import asyncio
from difflib import SequenceMatcher

import httpx

from app.config import WEATHER_PREFERRED_COUNTRY
from app.tools.errors import ToolError


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CURRENT_FIELDS = "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,cloud_cover,surface_pressure,wind_speed_10m,wind_direction_10m,is_day"
DAILY_FIELDS = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max"
COUNTRY_CODES = {"sri lanka": "LK"}


async def _get_json(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    """Retry brief upstream failures so one network hiccup does not fail a voice turn."""
    for attempt in range(3):
        try:
            response = await client.get(url, params=params)
            if response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            return data
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError):
            if attempt == 2 or ('response' in locals() and response.status_code not in (429,) and response.status_code < 500):
                raise
            await asyncio.sleep(0.15 * (2 ** attempt))
    raise RuntimeError("unreachable")


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
            requested_parts = [part.strip() for part in location.split(",") if part.strip()]
            requested_name = requested_parts[0].casefold()
            requested_country = requested_parts[-1].casefold() if len(requested_parts) > 1 else WEATHER_PREFERRED_COUNTRY
            geocode_params = {"name": requested_parts[0], "count": 10, "language": "en", "format": "json"}
            country_code = COUNTRY_CODES.get(requested_country)
            if country_code:
                geocode_params["countryCode"] = country_code
            geocode_data = await _get_json(client, GEOCODING_URL, geocode_params)
            places = geocode_data.get("results") or []
            if not places:
                raise ToolError(f"No location found for {location.strip()}.")
            candidates = [candidate for candidate in places if isinstance(candidate, dict)]
            country_matches = [candidate for candidate in candidates if str(candidate.get("country", "")).casefold() == requested_country]
            if requested_country and not country_matches:
                raise ToolError(f"No clear match for {requested_parts[0]} in {requested_parts[-1] if len(requested_parts) > 1 else requested_country.title()}. Please specify the city and country.")
            exact = [candidate for candidate in candidates if str(candidate.get("name", "")).casefold() == requested_name]
            preferred_exact = [candidate for candidate in exact if str(candidate.get("country", "")).casefold() == requested_country]
            if preferred_exact:
                place = max(preferred_exact, key=lambda item: int(item.get("population") or 0))
            elif len({candidate.get("country_code") for candidate in exact}) > 1 and "," not in location and not WEATHER_PREFERRED_COUNTRY:
                raise ToolError(f"Multiple places match {location.strip()}. Please specify the city and country.")
            elif exact:
                place = max(exact, key=lambda item: int(item.get("population") or 0))
            else:
                ranked = sorted(candidates, key=lambda item: (
                    str(item.get("country", "")).casefold() == requested_country,
                    SequenceMatcher(None, requested_name, str(item.get("name", "")).casefold()).ratio(),
                    int(item.get("population") or 0),
                ), reverse=True)
                place = ranked[0]
            if not isinstance(place, dict):
                raise ToolError("Weather service returned incomplete location data.")
            matched_name = str(place.get("name", "")).casefold()
            if SequenceMatcher(None, requested_name, matched_name).ratio() < 0.82:
                raise ToolError(f"The location {location.strip()} was not clear. Please provide the city and country.")
            data = await _get_json(client, FORECAST_URL, {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": CURRENT_FIELDS,
                "daily": DAILY_FIELDS,
                "temperature_unit": unit,
                "wind_speed_unit": "kmh" if unit == "celsius" else "mph",
                "precipitation_unit": "mm" if unit == "celsius" else "inch",
                "timezone": place.get("timezone") or "auto",
                "forecast_days": 1,
                "cell_selection": "land",
            })
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
    daily = data.get("daily")
    daily_units = data.get("daily_units")
    if not isinstance(current, dict) or not isinstance(units, dict) or current.get("temperature_2m") is None or not current.get("time"):
        raise ToolError("Weather service returned incomplete data.")
    if not isinstance(daily, dict):
        daily = {}
    if not isinstance(daily_units, dict):
        daily_units = {}

    def first(name: str):
        values = daily.get(name)
        return values[0] if isinstance(values, list) and values else None

    today_code = first("weather_code")
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
        "wind_direction": current.get("wind_direction_10m"),
        "wind_direction_unit": units.get("wind_direction_10m"),
        "cloud_cover": current.get("cloud_cover"),
        "cloud_cover_unit": units.get("cloud_cover"),
        "surface_pressure": current.get("surface_pressure"),
        "surface_pressure_unit": units.get("surface_pressure"),
        "is_day": current.get("is_day"),
        "weather_code": current.get("weather_code"),
        "condition": weather_description(current.get("weather_code")),
        "today_date": first("time"),
        "today_condition": weather_description(today_code) if today_code is not None else None,
        "today_high": first("temperature_2m_max"),
        "today_low": first("temperature_2m_min"),
        "today_temperature_unit": daily_units.get("temperature_2m_max") or units.get("temperature_2m"),
        "today_precipitation": first("precipitation_sum"),
        "today_precipitation_unit": daily_units.get("precipitation_sum") or units.get("precipitation"),
        "today_rain_chance": first("precipitation_probability_max"),
        "today_rain_chance_unit": daily_units.get("precipitation_probability_max") or "%",
        "today_max_wind": first("wind_speed_10m_max"),
        "today_wind_unit": daily_units.get("wind_speed_10m_max") or units.get("wind_speed_10m"),
        "source": "Open-Meteo",
    }
