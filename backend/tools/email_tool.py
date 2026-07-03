from __future__ import annotations

import logging
import base64
from email.mime.text import MIMEText
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from auth.token_manager import token_manager
from tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

class EmailTool(BaseTool):
    name: str = "email_tool"
    description: str = (
        "Gmail integration. Use mode 'search' to find emails by query. "
        "Use mode 'send' to draft an email to a recipient (requires approval)."
    )

    async def run(
        self, 
        mode: str, 
        query: str = "", 
        to: str = "", 
        subject: str = "", 
        body: str = "",
        draft: dict={},
    ) -> ToolResult:
        
        creds = token_manager.get_valid_credentials()
        if not creds:
            return ToolResult(ok=False, content="Google account not connected. Please connect your account first.")

        try:
            service = build("gmail", "v1", credentials=creds)

            if mode == "search":
                # Search inbox (max 5 results)
                results = service.users().messages().list(
                    userId="me", q=query, maxResults=5
                ).execute()
                
                messages = results.get("messages", [])
                if not messages:
                    return ToolResult(ok=True, content=f"No emails found matching: {query}")

                email_summaries = []
                for msg in messages:
                    msg_data = service.users().messages().get(
                        userId="me", id=msg["id"], format="metadata", 
                        metadataHeaders=["Subject", "From"]
                    ).execute()
                    
                    subject = next((h["value"] for h in msg_data["payload"]["headers"] if h["name"] == "Subject"), "No Subject")
                    sender = next((h["value"] for h in msg_data["payload"]["headers"] if h["name"] == "From"), "Unknown")
                    email_summaries.append(f"From: {sender} | Subject: {subject}")

                return ToolResult(
                    ok=True,
                    content="Here are the recent emails found:\n" + "\n".join(email_summaries),
                    data={"emails": email_summaries}
                )

            elif mode == "send":
                # Draft the email for Approval Gate (Step 4)
                message = MIMEText(body)
                message["to"] = to
                message["subject"] = subject
                
                # Base64 encode for Gmail API
                raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
                
                email_draft = {
                    "raw": raw_message,
                    "to": to,
                    "subject": subject,
                    "body": body
                }
                
                return ToolResult(
                    ok=True,
                    content=f"Email drafted to {to} with subject '{subject}'. Awaiting user approval to send.",
                    data={"action_type": "send_email", "draft": email_draft}
                )
            
            elif mode == "execute":
                # F13: Actually send the email via Gmail API
                sent_message = service.users().messages().send(
                    userId="me", body={"raw": draft.get("raw")}
                ).execute()
                
                return ToolResult(
                    ok=True,
                    content=f"Email successfully sent to {draft.get('to')}. Message ID: {sent_message.get('id')}",
                    data={"message_id": sent_message.get("id")}
                )

            else:
                return ToolResult(ok=False, content=f"Unknown mode: {mode}")

        except HttpError as e:
            logger.error(f"[EmailTool] Google API Error: {e}")
            return ToolResult(ok=False, content=f"Gmail API error: {e}")
        except Exception as e:
            logger.error(f"[EmailTool] Failed: {e}")
            return ToolResult(ok=False, content=f"Failed to execute email tool: {e}")