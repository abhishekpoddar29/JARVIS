from __future__ import annotations

import logging
from datetime import datetime, UTC, timedelta
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from auth.token_manager import token_manager
from tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

class CalendarTool(BaseTool):
    name: str = "calendar_tool"
    description: str = (
        "Google Calendar integration. Use mode 'list_events' to see upcoming schedule. "
        "Use mode 'create_event' to draft a new calendar event (requires approval)."
    )

    async def run(
        self, 
        mode: str, 
        query: str = "", 
        title: str = "", 
        start_time: str = "", 
        end_time: str = "", 
        attendees: list[str] = [],
        draft : dict={}
    ) -> ToolResult:
        
        creds = token_manager.get_valid_credentials()
        if not creds:
            return ToolResult(ok=False, content="Google account not connected. Please connect your account first.")

        try:
            service = build("calendar", "v3", credentials=creds)

            if mode == "list_events":
                # Fetch the next 10 events
                now = datetime.now(UTC).isoformat().replace("+00:00","Z")
                events_result = service.events().list(
                    calendarId="primary",
                    timeMin=now,
                    maxResults=10,
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()
                
                events = events_result.get("items", [])
                if not events:
                    return ToolResult(ok=True, content="You have no upcoming events.")

                event_list = []
                for event in events:
                    start = event["start"].get("dateTime", event["start"].get("date"))
                    event_list.append(f"{start} - {event.get('summary', 'No Title')}")
                
                return ToolResult(
                    ok=True,
                    content="Here are your upcoming events:\n" + "\n".join(event_list),
                    data={"events": events}
                )

            elif mode == "create_event":
                # Draft the event for Approval Gate (Step 4)
                event_draft = {
                    "summary": title,
                    "start": {"dateTime": start_time},
                    "end": {"dateTime": end_time},
                    "attendees": [{"email": e} for e in attendees]
                }
                
                return ToolResult(
                    ok=True,
                    content=f"Calendar event drafted: {title} at {start_time}. Awaiting user approval to insert into Google Calendar.",
                    data={"action_type": "calendar_event", "draft": event_draft}
                )
            elif mode == "execute":
                # F13: Actually create the event in Google Calendar
                created_event = service.events().insert(
                    calendarId="primary", body=draft).execute()
                
                return ToolResult(
                    ok=True,
                    content=f"Calendar event successfully created: {created_event.get('htmlLink')}",
                    data={"event_id": created_event.get("id")}
                )

            else:
                return ToolResult(ok=False, content=f"Unknown mode: {mode}")

        except HttpError as e:
            logger.error(f"[CalendarTool] Google API Error: {e}")
            return ToolResult(ok=False, content=f"Google Calendar API error: {e}")
        except Exception as e:
            logger.error(f"[CalendarTool] Failed: {e}")
            return ToolResult(ok=False, content=f"Failed to execute calendar tool: {e}")