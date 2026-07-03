from tools.base import BaseTool, ToolResult
import random

class CustomJokeTool(BaseTool):
    name: str = "custom_joke_tool"
    description: str = "Tells a random programming joke."

    async def run(self, **kwargs) -> ToolResult:
        jokes = [
            "Why do programmers prefer dark mode? Because light attracts bugs.",
            "Why was the JavaScript developer sad? Because he didn't Node how to Express himself.",
            "There are 10 types of people in the world: those who understand binary, and those who don't.",
            "Debugging: Removing the needles from the haystack, only to realize it was a sewing kit."
        ]
        joke = random.choice(jokes)
        return ToolResult(ok=True, content=joke, data={})