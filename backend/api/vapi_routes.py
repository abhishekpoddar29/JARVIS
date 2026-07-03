from fastapi import APIRouter, Request
from typing import Any
import uuid
import json
from langgraph.types import Command
from datetime import datetime, UTC
from fastapi.responses import StreamingResponse

from agents.graph import jarvis_graph
from memory.session_storage import session_store

router = APIRouter()

# Track which calls are currently awaiting an approval (F13 HITL)
active_approvals: dict[str, bool] = {}

@router.post("/api/vapi/webhook/chat/completions")
async def vapi_webhook(request: Request):
    raw_body = await request.body()
    try:
        request_data = json.loads(raw_body)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON"}
        
    print(f"\n[VAPI WEBHOOK] Received request: {json.dumps(request_data, indent=2)}\n")

    try:
        call_id = request_data.get("call", {}).get("id", "unknown")
        messages = request_data.get("messages", [])
        is_stream = request_data.get("stream", False)
        
        # FIX: Only get the LATEST user message from Vapi. Ignore Vapi's messy TTS transcriptions.
        user_messages = [m for m in messages if m.get("role") == "user"]
        
        if not user_messages:
            # If no user message (e.g., call just started), return a greeting
            assistant_message = "Hello Abhi. How can I assist you today?"
            return _format_response(assistant_message, is_stream)
            
        latest_message = user_messages[-1].get("content", "")
        
        # F13: Check if Jarvis is currently awaiting an approval for this call
        if active_approvals.get(call_id):
            lower_transcript = latest_message.lower()
            
            # Check for explicit approval or rejection
            approved_words = ["yes", "yeah", "send", "approve", "do it", "confirm", "go ahead", "proceed", "yep", "sure"]
            rejected_words = ["no", "cancel", "stop", "don't", "abort", "forget it", "nope"]
            
            is_approved = any(word in lower_transcript for word in approved_words)
            is_rejected = any(word in lower_transcript for word in rejected_words)
            
            if is_approved and not is_rejected:
                active_approvals[call_id] = False
                thread_config = {"configurable": {"thread_id": call_id}}
                
                # Resume the graph!
                result = await jarvis_graph.ainvoke(
                    Command(resume={"approved": True}), 
                    config=thread_config
                )
                assistant_message = result.get("final_reply") or "Action completed."
                await session_store.append_message(role="user", content=latest_message)
                await session_store.append_message(role="assistant", content=assistant_message)
                return _format_response(assistant_message, is_stream)
                
            elif is_rejected and not is_approved:
                active_approvals[call_id] = False
                await session_store.append_message(role="user", content=latest_message)
                await session_store.append_message(role="assistant", content="Okay, I will not execute that action.")
                return _format_response("Okay, action cancelled.", is_stream)
                
            else:
                # FIX: If the user said something unclear (like "Where is the approval card?"), ask again instead of cancelling.
                return _format_response("I didn't catch a clear yes or no. Do you want me to proceed with this action?", is_stream)


                
        # Normal Chat Flow: Save user message to Redis
        await session_store.append_message(role="user", content=latest_message)

        # Load CLEAN History from Redis
        session_messages = await session_store.load_session()
        history: list[dict[str, str]] = []
        for m in session_messages:
            role = m["role"]
            content = m.get("content", "").strip()
            if not content: continue
            if role == "assistant": role = "model"
            if role in ("user", "model"):
                history.append({"role": role, "content": content})

        # Invoke LangGraph Brain
        thread_config = {"configurable": {"thread_id": call_id}}
        result = await jarvis_graph.ainvoke({
            "user_message": latest_message,
            "history": history,
            "memory_facts": [],
            "image_data": None,
        }, config=thread_config)

        # Check if Jarvis paused to ask for permission (F13)
        if "__interrupt__" in result:
            active_approvals[call_id] = True
            interrupt_data = result["__interrupt__"][0].value
            assistant_message = "I have prepared an action that requires your approval. Do you want me to proceed?"
            
            # F16 BRIDGE: Send Approval Card to Frontend UI via WebSocket!
            from main import active_frontend_ws
            if active_frontend_ws:
                await active_frontend_ws.send_json({
                    "type": "approval_request",
                    "action_type": interrupt_data.get("action_type"),
                    "draft": interrupt_data.get("draft"),
                    "tool_name": interrupt_data.get("tool_name")
                })
                
            return _format_response(assistant_message, is_stream)

        # Return normal text response to Vapi
        assistant_message = result.get("final_reply") or "I'm sorry, I didn't understand that."
        tool_data = result.get("tool_data") or {}
        
        # F16 BRIDGE: Send Code Blocks to Frontend UI via WebSocket!
        if tool_data.get("code_result"):
            from main import active_frontend_ws
            if active_frontend_ws:
                await active_frontend_ws.send_json({
                    "type": "message",
                    "id": str(uuid.uuid4()),
                    "role": "assistant",
                    "content": assistant_message,
                    "created_at": datetime.now(UTC).isoformat(),
                    "provider": "gemini",
                    "code_result": tool_data["code_result"]
                })
                
            await session_store.append_message(role="assistant", content=assistant_message, tool_results=[{"tool": "code_execution_tool", "success": True, "data": tool_data}])
            return _format_response(assistant_message, is_stream)

        # F16 BRIDGE: Send standard text message to Frontend UI via WebSocket!
        from main import active_frontend_ws
        if active_frontend_ws:
            await active_frontend_ws.send_json({
                "type": "message",
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": assistant_message,
                "created_at": datetime.now(UTC).isoformat(),
                "provider": "gemini",
            })
            
        await session_store.append_message(role="assistant", content=assistant_message)
        return _format_response(assistant_message, is_stream)

    except Exception as e:
        print(f"[VAPI WEBHOOK ERROR] {e}")
        return _format_response("I encountered an error processing that.", is_stream)

def _format_response(text: str, is_stream: bool):
    """Formats the response as standard JSON or SSE stream based on what Vapi requested."""
    if is_stream:
        # Vapi expects strict OpenAI-style SSE streaming
        async def event_generator():
            # 1. Send the role chunk
            chunk1 = {
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None
                }]
            }
            yield f"data: {json.dumps(chunk1)}\n\n"
            
            # 2. Send the content chunk
            chunk2 = {
                "choices": [{
                    "index": 0,
                    "delta": {"content": text},
                    "finish_reason": None
                }]
            }
            yield f"data: {json.dumps(chunk2)}\n\n"
            
            # 3. Send the stop chunk
            chunk3 = {
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop"
                }]
            }
            yield f"data: {json.dumps(chunk3)}\n\n"
            
            # 4. Send the [DONE] signal
            yield "data: [DONE]\n\n"
            
        return StreamingResponse(event_generator(), media_type="text/event-stream")
    else:
        # Standard JSON response
        return {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": int(datetime.now(UTC).timestamp()),
            "model": "jarvis-2.0",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
        }