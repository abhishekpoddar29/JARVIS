from tools.base import BaseTool, ToolResult
import random

class CustomTechJokesTool(BaseTool):
    name: str = "custom_tech_jokes_tool"
    description: str = "Tells a random tech-related joke."

    async def run(self, **kwargs) -> ToolResult:
        jokes = [
            "Why do programmers prefer dark mode? Because light attracts bugs!",
            "There are 10 types of people in the world: those who understand binary, and those who don't.",
            "Debugging: Removing the needles from the haystack, only to realize it was a sewing machine all along.",
            "Why was the JavaScript developer sad? Because he didn't Node how to Express himself.",
            "What's a programmer's favorite place to hang out? Foo Bar.",
            "How many programmers does it take to change a light bulb? None, that's a hardware problem."
        ]
        joke = random.choice(jokes)
        return ToolResult(ok=True, content=joke, data={})