import requests

from config import get_settings
from tools.base import BaseTool, ToolResult

settings = get_settings()


class NewsTool(BaseTool):
    name: str = "news_tool"
    description: str = "Fetch the latest news headlines."

    async def run(self) -> ToolResult:
        try:
            response = requests.get(
                settings.newsdata_url,
                params={
                    "apikey": settings.newsdata_api_key,
                    "language": "en",
                    "category": "top",
                    "size": 5,
                },
                timeout=10,
            )

            response.raise_for_status()

            data = response.json()

            articles = data.get("results", [])

            headlines = []

            for article in articles[:5]:
                title = article.get("title", "").strip()

                if title:
                    headlines.append(title)

            if not headlines:
                return ToolResult(
                    ok=False,
                    content="No news headlines available.",
                )

            return ToolResult(
                ok=True,
                content="\n".join(
                    f"{idx + 1}. {headline}"
                    for idx, headline in enumerate(headlines)
                ),
                data={
                    "headlines": headlines,
                },
            )

        except Exception as exc:
            return ToolResult(
                ok=False,
                content="Unable to fetch latest news.",
                error=str(exc),
            )