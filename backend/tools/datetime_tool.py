from datetime import datetime

from tools.base import BaseTool, ToolResult


class DateTimeTool(BaseTool):
    name: str = "datetime_tool"
    description: str = "Get current date and time information."

    async def run(self) -> ToolResult:

        now = datetime.now()

        if now.hour < 12:
            greeting_time = "morning"
        elif now.hour < 17:
            greeting_time = "afternoon"
        else:
            greeting_time = "evening"

        data = {
            "day_name": now.strftime("%A"),
            "date_formatted": now.strftime("%B %d, %Y"),
            "time_formatted": now.strftime("%I:%M %p").lstrip("0"),
            "greeting_time": greeting_time,
            "timestamp": now.isoformat(),
            "is_monday": now.weekday() == 0,
        }

        return ToolResult(
            ok=True,
            content=(
                f"Today is "
                f"{data['day_name']}, "
                f"{data['date_formatted']}. "
                f"Current time is "
                f"{data['time_formatted']}."
            ),
            data=data,
        )