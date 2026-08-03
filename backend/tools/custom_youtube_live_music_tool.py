from tools.base import BaseTool, ToolResult
import os
import httpx

class CustomYouTubeLiveMusicTool(BaseTool):
    name: str = "custom_youtube_live_music_tool"
    description: str = "Searches YouTube for live music streams based on a query and provides direct links. Requires a YouTube Data API key set as an environment variable 'YOUTUBE_API_KEY'."

    async def run(self, query: str, **kwargs) -> ToolResult:
        api_key = os.getenv("YOUTUBE_API_KEY")
        if not api_key:
            return ToolResult(
                ok=False,
                content="Error: YouTube API key not found. Please set the YOUTUBE_API_KEY environment variable."
            )

        base_url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "part": "snippet",
            "q": query + " live music",
            "type": "video",
            "eventType": "live",
            "maxResults": 5,
            "key": api_key
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(base_url, params=params)
                response.raise_for_status()
                data = response.json()

            live_streams = []
            if "items" in data:
                for item in data["items"]:
                    if item["id"]["kind"] == "youtube#video":
                        video_id = item["id"]["videoId"]
                        title = item["snippet"]["title"]
                        channel = item["snippet"]["channelTitle"]
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                        live_streams.append({
                            "title": title,
                            "channel": channel,
                            "url": video_url
                        })

            if live_streams:
                content = "Here are some live music streams I found on YouTube:\n"
                for stream in live_streams:
                    content += f"- {stream['title']} by {stream['channel']}: {stream['url']}\n"
                return ToolResult(ok=True, content=content, data={"streams": live_streams})
            else:
                return ToolResult(ok=True, content=f"No live music streams found for '{query}'.", data={"streams": []})

        except httpx.HTTPStatusError as e:
            return ToolResult(ok=False, content=f"YouTube API error: {e.response.status_code} - {e.response.text}")
        except httpx.RequestError as e:
            return ToolResult(ok=False, content=f"Network error while connecting to YouTube API: {e}")
        except Exception as e:
            return ToolResult(ok=False, content=f"An unexpected error occurred: {e}")