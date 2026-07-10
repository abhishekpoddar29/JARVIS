from tools.base import BaseTool, ToolResult
import httpx

class CustomWeatherFetcherTool(BaseTool):
    name: str = "custom_weather_fetcher_tool"
    description: str = "Fetches the current temperature for given latitude and longitude using the Open-Meteo API."

    async def run(self, latitude: float, longitude: float, **kwargs) -> ToolResult:
        if not all([latitude, longitude]):
            return ToolResult(ok=False, content="Error: Both latitude and longitude are required.")

        api_url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current_weather=true"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(api_url)
                response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
                data = response.json()

            if "current_weather" in data and "temperature" in data["current_weather"]:
                temperature = data["current_weather"]["temperature"]
                unit = data["current_weather"].get("temperature_unit", "°C") # Open-Meteo uses °C by default
                return ToolResult(ok=True, content=f"Current temperature at {latitude}, {longitude}: {temperature}{unit}", data={"temperature": temperature, "unit": unit})
            else:
                return ToolResult(ok=False, content="Could not find current temperature in the API response.")

        except httpx.RequestError as e:
            return ToolResult(ok=False, content=f"Network error while fetching weather: {e}")
        except httpx.HTTPStatusError as e:
            return ToolResult(ok=False, content=f"API returned an error: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            return ToolResult(ok=False, content=f"An unexpected error occurred: {e}")
