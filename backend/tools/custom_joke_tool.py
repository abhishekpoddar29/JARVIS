from tools.base import BaseTool, ToolResult
import random

class CustomJokeTool(BaseTool):
    name: str = "custom_joke_tool"
    description: str = "Tells a random programming joke."

    async def run(self) -> ToolResult:
        jokes = [
            "Why do programmers prefer dark mode? Because light attracts bugs.",
            "Why was the JavaScript developer sad? Because he didn't Node how to Express himself.",
            "There are 10 types of people in this world: those who understand binary, and those who don't.",
            "Debugging: Removing the needles from the haystack, only to realize the hay is also a needle.",
            "A SQL query walks into a bar, walks up to two tables and asks, 'Can I join you?'"
        ]
        joke = random.choice(jokes)
        return ToolResult(ok=True, content=joke, data={})
