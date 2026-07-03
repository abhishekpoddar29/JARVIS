import psutil

from tools.base import BaseTool, ToolResult


class SystemInfoTool(BaseTool):

    name: str = "system_info_tool"

    description: str = (
        "Get CPU, RAM, disk and battery information."
    )

    async def run(
        self,
        metric: str = "all",
    ) -> ToolResult:

        data = {
            "cpu_percent": psutil.cpu_percent(),
            "ram_percent": psutil.virtual_memory().percent,
            "disk_percent": psutil.disk_usage("/").percent,
        }

        battery = psutil.sensors_battery()

        if battery:
            data["battery_percent"] = battery.percent

        return ToolResult(
            ok=True,
            content=str(data),
            data=data,
        )