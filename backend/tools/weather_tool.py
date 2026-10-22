from __future__ import annotations

import re
from collections import Counter
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from config import get_settings
from tools.base import BaseTool, ToolResult


settings = get_settings()

_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
_IPINFO_URL = "https://ipinfo.io/json"

_DATE_ALIASES = {
    "today": 0,
    "tomorrow": 1,
    "day after tomorrow": 2,
}


class WeatherTool(BaseTool):
    name: str = "weather_tool"
    description: str = (
        "Get current weather or a forecast within OpenWeather's 5-day forecast window. "
        "Use date='today', date='tomorrow', or an ISO date such as '2026-08-12'. "
        "Natural dates such as '12th August' are also accepted. "
        "If no city is provided, the user's city is auto-detected before querying OpenWeather."
    )

    async def run(
        self,
        city: str | None = None,
        date: str | None = None,
    ) -> ToolResult:
        try:
            if not settings.openweather_api_key:
                return ToolResult(
                    ok=False,
                    content="OpenWeather API key is not configured.",
                )

            resolved_city, detected_region, detected_country = self._resolve_city(city)
            if not resolved_city:
                return ToolResult(
                    ok=False,
                    content="Unable to determine the location for the weather request.",
                )

            requested = (date or "").strip()
            if not requested or requested.lower() in {"now", "current", "right now"}:
                return self._get_current_weather(
                    resolved_city,
                    detected_region,
                    detected_country,
                )

            return self._get_forecast(
                resolved_city,
                requested,
                detected_region,
                detected_country,
            )

        except requests.HTTPError as exc:
            return ToolResult(
                ok=False,
                content=f"Unable to fetch weather for '{city or 'your location'}'.",
                data={"error": str(exc)},
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                content="Unable to fetch weather information.",
                data={"error": str(exc)},
            )

    # ------------------------------------------------------------------
    # Location
    # ------------------------------------------------------------------
    def _resolve_city(self, city: str | None) -> tuple[str | None, str, str]:
        if city and city.strip():
            return city.strip(), "", ""

        # Preserve the existing Jarvis behavior: IP lookup is used only to
        # determine the city. All weather and forecast data comes from OpenWeather.
        response = requests.get(
            _IPINFO_URL,
            headers={"User-Agent": "Jarvis/2.0"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        return (
            data.get("city"),
            data.get("region", ""),
            data.get("country", ""),
        )

    # ------------------------------------------------------------------
    # Current weather
    # ------------------------------------------------------------------
    def _get_current_weather(
        self,
        city: str,
        detected_region: str,
        detected_country: str,
    ) -> ToolResult:
        response = requests.get(
            _CURRENT_URL,
            params={
                "q": city,
                "appid": settings.openweather_api_key,
                "units": "metric",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        result = {
            "forecast_type": "current",
            "city": data.get("name", city),
            "region": detected_region,
            "country": data.get("sys", {}).get("country") or detected_country,
            "temperature": round(data["main"]["temp"]),
            "feels_like": round(data["main"]["feels_like"]),
            "humidity": data["main"]["humidity"],
            "condition": data["weather"][0]["description"].capitalize(),
            "wind_speed": data.get("wind", {}).get("speed", 0),
        }

        return ToolResult(
            ok=True,
            content=(
                f"Current weather in {result['city']} is "
                f"{result['temperature']}°C with {result['condition']}. "
                f"It feels like {result['feels_like']}°C. "
                f"Humidity is {result['humidity']}% and "
                f"wind speed is {result['wind_speed']} m/s."
            ),
            data=result,
        )

    # ------------------------------------------------------------------
    # 5-day / 3-hour forecast
    # ------------------------------------------------------------------
    def _get_forecast(
        self,
        city: str,
        requested_date: str,
        detected_region: str,
        detected_country: str,
    ) -> ToolResult:
        response = requests.get(
            _FORECAST_URL,
            params={
                "q": city,
                "appid": settings.openweather_api_key,
                "units": "metric",
            },
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()

        city_data = payload.get("city", {})
        timezone_offset = int(city_data.get("timezone", 0) or 0)
        local_now = datetime.now(timezone.utc) + timedelta(seconds=timezone_offset)

        target_date = self._parse_requested_date(requested_date, local_now.date())
        if target_date is None:
            return ToolResult(
                ok=False,
                content=(
                    "I couldn't understand the requested weather date. "
                    "Use 'today', 'tomorrow', or a date such as '2026-08-12'."
                ),
                data={"requested_date": requested_date},
            )

        points = payload.get("list") or []
        if not points:
            return ToolResult(
                ok=False,
                content="OpenWeather returned no forecast data for this location.",
            )

        local_points: list[tuple[dict[str, Any], datetime]] = []
        available_dates: list[Date] = []

        for point in points:
            timestamp = point.get("dt")
            if timestamp is None:
                continue

            local_dt = datetime.fromtimestamp(
                int(timestamp) + timezone_offset,
                tz=timezone.utc,
            )
            local_points.append((point, local_dt))
            available_dates.append(local_dt.date())

        if not available_dates:
            return ToolResult(
                ok=False,
                content="OpenWeather returned forecast data without valid timestamps.",
            )

        first_date = min(available_dates)
        last_date = max(available_dates)

        if target_date < local_now.date():
            return ToolResult(
                ok=False,
                content=(
                    f"{target_date.isoformat()} is in the past. "
                    "This tool provides current weather and upcoming forecasts only."
                ),
                data={
                    "requested_date": target_date.isoformat(),
                    "forecast_start": first_date.isoformat(),
                    "forecast_end": last_date.isoformat(),
                },
            )

        day_points = [
            (point, local_dt)
            for point, local_dt in local_points
            if local_dt.date() == target_date
        ]

        if not day_points:
            return ToolResult(
                ok=False,
                content=(
                    f"OpenWeather's 5-day forecast does not currently include "
                    f"{target_date.strftime('%d %B %Y')}. "
                    f"Available forecast dates are {first_date.strftime('%d %B')} "
                    f"through {last_date.strftime('%d %B %Y')}."
                ),
                data={
                    "requested_date": target_date.isoformat(),
                    "forecast_start": first_date.isoformat(),
                    "forecast_end": last_date.isoformat(),
                },
            )

        summary = self._summarize_forecast_day(day_points)
        result = {
            "forecast_type": "daily_forecast",
            "city": city_data.get("name", city),
            "region": detected_region,
            "country": city_data.get("country") or detected_country,
            "date": target_date.isoformat(),
            **summary,
        }

        rain_text = ""
        if result["precipitation_probability"] > 0:
            rain_text = (
                f" Maximum precipitation probability is "
                f"{result['precipitation_probability']}%."
            )

        return ToolResult(
            ok=True,
            content=(
                f"Forecast for {result['city']} on "
                f"{target_date.strftime('%d %B %Y')}: "
                f"{result['condition']}, with temperatures around "
                f"{result['temp_min']}–{result['temp_max']}°C. "
                f"Average humidity is {result['humidity']}% and "
                f"wind speed is around {result['wind_speed']} m/s."
                f"{rain_text}"
            ),
            data=result,
        )

    @staticmethod
    def _summarize_forecast_day(
        day_points: list[tuple[dict[str, Any], datetime]],
    ) -> dict[str, Any]:
        temperatures: list[float] = []
        feels_like: list[float] = []
        humidity: list[int] = []
        wind_speeds: list[float] = []
        precipitation_probabilities: list[float] = []
        conditions: list[str] = []
        rain_total = 0.0
        snow_total = 0.0
        hourly: list[dict[str, Any]] = []

        for point, local_dt in day_points:
            main = point.get("main", {})
            weather = point.get("weather") or [{}]
            wind = point.get("wind", {})

            temp = float(main.get("temp", 0))
            temperatures.append(temp)
            feels_like.append(float(main.get("feels_like", temp)))
            humidity.append(int(main.get("humidity", 0)))
            wind_speeds.append(float(wind.get("speed", 0)))

            pop = max(0.0, min(1.0, float(point.get("pop", 0) or 0)))
            precipitation_probabilities.append(pop)

            condition = str(weather[0].get("description", "Unknown")).capitalize()
            conditions.append(condition)

            rain_total += float((point.get("rain") or {}).get("3h", 0) or 0)
            snow_total += float((point.get("snow") or {}).get("3h", 0) or 0)

            hourly.append({
                "time": local_dt.strftime("%H:%M"),
                "temperature": round(temp),
                "feels_like": round(float(main.get("feels_like", temp))),
                "humidity": int(main.get("humidity", 0)),
                "condition": condition,
                "precipitation_probability": round(pop * 100),
                "wind_speed": round(float(wind.get("speed", 0)), 1),
            })

        # The most frequent condition is a better daily description than whichever
        # 3-hour point happened to appear first in the API response.
        dominant_condition = Counter(conditions).most_common(1)[0][0]

        return {
            "temperature": round(sum(temperatures) / len(temperatures)),
            "temp_min": round(min(temperatures)),
            "temp_max": round(max(temperatures)),
            "feels_like": round(sum(feels_like) / len(feels_like)),
            "humidity": round(sum(humidity) / len(humidity)),
            "condition": dominant_condition,
            "wind_speed": round(sum(wind_speeds) / len(wind_speeds), 1),
            "precipitation_probability": round(max(precipitation_probabilities) * 100),
            "rain_total_mm": round(rain_total, 1),
            "snow_total_mm": round(snow_total, 1),
            "hourly": hourly,
        }

    # ------------------------------------------------------------------
    # Date parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_requested_date(value: str, local_today: Date) -> Date | None:
        cleaned = value.strip().lower()
        if not cleaned:
            return None

        if cleaned in _DATE_ALIASES:
            return local_today + timedelta(days=_DATE_ALIASES[cleaned])

        try:
            return Date.fromisoformat(cleaned)
        except ValueError:
            pass

        # Turn "9th August" into "9 August" before normal parsing.
        cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)\b", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        formats_with_year = (
            "%d %B %Y",
            "%d %b %Y",
            "%B %d %Y",
            "%b %d %Y",
        )
        for fmt in formats_with_year:
            try:
                return datetime.strptime(cleaned, fmt).date()
            except ValueError:
                continue

        formats_without_year = (
            "%d %B",
            "%d %b",
            "%B %d",
            "%b %d",
        )
        for fmt in formats_without_year:
            try:
                parsed = datetime.strptime(cleaned, fmt)
                return Date(local_today.year, parsed.month, parsed.day)
            except ValueError:
                continue

        return None