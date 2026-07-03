import requests

from tools.base import BaseTool, ToolResult


class LocationTool(BaseTool):
    name: str = "location_tool"
    description: str = (
        "Detect the current user location "
        "using IP geolocation."
    )

    async def run(self) -> ToolResult:

        try:

            response = requests.get(
                "https://ipinfo.io/json",
                timeout=10,
            )

            response.raise_for_status()

            data = response.json()

            loc = data.get(
                "loc",
                "0,0",
            ).split(",")

            result = {
                "city": data.get(
                    "city",
                    "Unknown City",
                ),
                "region": data.get(
                    "region",
                    "",
                ),
                "country": data.get(
                    "country",
                    "",
                ),
                "lat": float(loc[0]),
                "lon": float(loc[1]),
            }

            return ToolResult(
                ok=True,
                content=(
                    f"Current location: "
                    f"{result['city']}, "
                    f"{result['region']}, "
                    f"{result['country']}"
                ),
                data=result,
            )

        except Exception as exc:

            return ToolResult(
                ok=False,
                content="Unable to determine location.",
                error=str(exc),
            )