import requests

from config import get_settings
from tools.base import BaseTool, ToolResult

settings = get_settings()


class WeatherTool(BaseTool):
    name: str = "weather_tool"
    description: str = (
        "Get current weather information for a city. "
        "If no city is provided, automatically detects the user's location."
    )

    async def run(
        self,
        city: str | None = None,
    ) -> ToolResult:

        try:

            if not settings.openweather_api_key:
                return ToolResult(
                    ok=False,
                    content="OpenWeather API key is not configured.",
                )

            detected_region = ""
            detected_country = ""

            # --------------------------------------------------
            # Auto-detect location
            # --------------------------------------------------
            if not city:

                location_response = requests.get(
                    "https://ipinfo.io/json",
                    headers={
                        "User-Agent": "Jarvis/2.0"
                    },
                    timeout=10,
                )

                location_response.raise_for_status()

                location_data = location_response.json()

                city = location_data.get("city")

                detected_region = location_data.get(
                    "region",
                    "",
                )

                detected_country = location_data.get(
                    "country",
                    "",
                )

                if not city:
                    return ToolResult(
                        ok=False,
                        content="Unable to determine your current location.",
                    )

            # --------------------------------------------------
            # Fetch weather
            # --------------------------------------------------
            weather_response = requests.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={
                    "q": city,
                    "appid": settings.openweather_api_key,
                    "units": "metric",
                },
                timeout=10,
            )

            weather_response.raise_for_status()

            data = weather_response.json()

            result = {
                "city": data.get("name", city),
                "region": detected_region,
                "country": detected_country,
                "temperature": round(data["main"]["temp"]),
                "feels_like": round(data["main"]["feels_like"]),
                "humidity": data["main"]["humidity"],
                "condition": data["weather"][0]["description"].capitalize(),
                "wind_speed": data["wind"]["speed"],
            }

            return ToolResult(
                ok=True,
                content=(
                    f"Today's weather in {result['city']} is "
                    f"{result['temperature']}°C with "
                    f"{result['condition']}. "
                    f"It feels like {result['feels_like']}°C. "
                    f"Humidity is {result['humidity']}% and "
                    f"wind speed is {result['wind_speed']} m/s."
                ),
                data=result,
            )

        except requests.HTTPError as exc:
            return ToolResult(
                ok=False,
                content=f"Unable to fetch weather for '{city}'.",
                data={
                    "error": str(exc),
                },
            )

        except Exception as exc:
            return ToolResult(
                ok=False,
                content="Unable to fetch weather information.",
                data={
                    "error": str(exc),
                },
            )