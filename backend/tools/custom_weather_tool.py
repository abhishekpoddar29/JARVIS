from tools.base import BaseTool, ToolResult

class CustomWeatherTool(BaseTool):
    name: str = "custom_weather_tool"
    description: str = "Fetches a mock weather report for a given city."

    async def run(self, city: str, **kwargs) -> ToolResult:
        if not city:
            return ToolResult(ok=False, content="Please provide a city to fetch weather for.")
        
        mock_weather = f"The weather in {city} is currently sunny with a temperature of 25°C. There is a light breeze."
        return ToolResult(ok=True, content=mock_weather)