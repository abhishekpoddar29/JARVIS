import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from langgraph.types import Command  # ← F13: Required for resuming paused graphs

from agents.graph import jarvis_graph
from config import get_settings
from memory.session_storage import session_store
from memory.export_manager import export_session, archive_to_disk, wipe_session
from memory.semantic import semantic_memory       
from memory.distiller import distiller            
from memory.chromadb_schemas import MemoryType, SensitivityLevel 
from auth.google_oauth import google_oauth       
from auth.token_store import token_store         
from tools.registry import registry
from tools.weather_tool import WeatherTool
from tools.news_tool import NewsTool
from tools.datetime_tool import DateTimeTool
from tools.location_tool import LocationTool
from tools.web_search_tool import WebSearchTool
from tools.system_info_tool import SystemInfoTool
from tools.task_memory_tool import TaskMemoryTool
from tools.research_tool import ResearchTool
from tools.code_execution_tool import CodeExecutionTool
from tools.calendar_tool import CalendarTool
from tools.email_tool import EmailTool
from agents.graph_nodes import orb_emitter, progress_emitter
from tools.web_scraper_tool import WebScraperTool
from api.vapi_routes import router as vapi_router
from tools.mcp_bridge import MCPManager
from tools.self_modify_tool import SelfModifyTool


settings = get_settings()

# Ensure exports directory exists
EXPORTS_DIR = Path(__file__).resolve().parent / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)




@asynccontextmanager
async def lifespan(app: FastAPI):
    await session_store.connect()

    registry.register(DateTimeTool())
    registry.register(LocationTool())
    registry.register(WeatherTool())
    registry.register(NewsTool())
    registry.register(WebSearchTool())
    registry.register(SystemInfoTool())
    registry.register(TaskMemoryTool())
    registry.register(ResearchTool())
    registry.register(CodeExecutionTool())
    registry.register(CalendarTool())
    registry.register(EmailTool())
    registry.register(WebScraperTool())
    registry.register(SelfModifyTool())

    mcp_manager = MCPManager()
    mcp_tools = await mcp_manager.initialize()
    for tool in mcp_tools:
        registry.register(tool)

    print("Registered tools:", [tool.name for tool in registry.list_tools()])

    yield

    await mcp_manager.close()
    await session_store.disconnect()


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
# Global reference to the active frontend WebSocket (for Vapi UI bridging)
active_frontend_ws: WebSocket | None = None

app.include_router(vapi_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request Models ──────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(min_length=1)

class ChatResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    state: str
    provider: str = "stub"

class MemoryUpdateRequest(BaseModel):
    content: Optional[str] = None
    pinned: Optional[bool] = None
    type: Optional[str] = None
    sensitivity: Optional[str] = None


# ── Core API Endpoints ──────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
        "time": datetime.now(UTC).isoformat(),
    }

@app.get("/api/session")
async def get_session() -> dict:
    messages = await session_store.load_session()
    return {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "message_count": len(messages),
        "messages": messages,
    }

@app.post("/api/export")
async def export_conversation() -> PlainTextResponse:
    markdown, payload, timestamp_str = await export_session()
    await archive_to_disk(payload, timestamp_str)
    await wipe_session()

    filename = f"jarvis-{timestamp_str}.md"
    return PlainTextResponse(
        content=markdown,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Filename": filename,
        },
    )

@app.get("/api/files/{filename}")
async def download_file(filename: str):
    safe_path = (EXPORTS_DIR / filename).resolve()
    if not safe_path.is_file() or EXPORTS_DIR not in safe_path.parents:
        raise HTTPException(status_code=404, detail="File not found")
        
    if filename.endswith(".html"):
        return FileResponse(safe_path, media_type="text/html")
        
    return FileResponse(safe_path)


# ── F9: Long-Term Semantic Memory Endpoints ─────────────────────────────────────

@app.get("/api/memory")
async def get_all_memories():
    memories = await asyncio.to_thread(semantic_memory.get_all_memories)
    return {
        "count": len(memories),
        "memories": [m.model_dump(mode="json") for m in memories]
    }

@app.post("/api/memory/distill")
async def trigger_distillation():
    result = await distiller.run()
    return result

@app.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: str):
    existing = await asyncio.to_thread(semantic_memory.get_memory_by_id, memory_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Memory not found")
        
    await asyncio.to_thread(semantic_memory.delete_memory, memory_id)
    return {"status": "deleted", "id": memory_id}

@app.patch("/api/memory/{memory_id}")
async def update_memory(memory_id: str, payload: MemoryUpdateRequest):
    existing = await asyncio.to_thread(semantic_memory.get_memory_by_id, memory_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Memory not found")
        
    if payload.content is not None:
        existing.content = payload.content
    if payload.pinned is not None:
        existing.pinned = payload.pinned
    if payload.type is not None:
        existing.type = MemoryType(payload.type)
    if payload.sensitivity is not None:
        existing.sensitivity = SensitivityLevel(payload.sensitivity)
        
    existing.updated_at = datetime.now(UTC)
    
    await asyncio.to_thread(semantic_memory.update_memory, existing)
    return existing.model_dump(mode="json")


# ── F13: Google OAuth Endpoints ─────────────────────────────────────────────────

@app.get("/api/auth/google")
async def auth_google():
    auth_url = google_oauth.get_auth_url()
    return RedirectResponse(url=auth_url)

@app.get("/api/auth/google/callback")
async def auth_google_callback(code: str):
    try:
        token_data = google_oauth.exchange_code_for_token(code)
        token_store.save_token("abhi", token_data)
        return {"status": "success", "message": "Google account connected successfully! You can close this tab."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to authenticate with Google: {str(e)}")


# ── WebSocket Endpoint ──────────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def chat_socket(websocket: WebSocket) -> None:
    global active_frontend_ws
    active_frontend_ws=websocket 

    await websocket.accept()
    
    # F13: Track the active thread ID for pause/resume
    active_thread_id: str | None = None

    try:
        await websocket.send_json({
            "type": "status",
            "state": "idle",
            "message": "Jarvis socket connected.",
        })

        while True:
            payload = await websocket.receive_json()
            
            # F13: Handle Approval Response from Frontend
            if payload.get("type") == "approval_response":
                if not active_thread_id:
                    await websocket.send_json({"type": "error", "message": "No active approval request."})
                    continue
                    
                thread_config = {"configurable": {"thread_id": active_thread_id}}
                resume_payload = {"approved": payload.get("approved", False)}
                
                await websocket.send_json({"type": "status", "state": "processing"})
                
                try:
                    # Resume the paused graph
                    result = await jarvis_graph.ainvoke(
                        Command(resume=resume_payload), 
                        config=thread_config
                    )
                    
                    # Clear the thread ID because the graph is finished
                    active_thread_id = None
                    
                    assistant_message = result.get("final_reply") or "Action completed."
                    
                    tool_result = result.get("tool_result")
                    if tool_result:
                        await session_store.append_message(
                            role="tool",
                            content=tool_result.content,
                            tool_results=[{
                                "tool": tool_result.tool,
                                "success": tool_result.success,
                                "data": tool_result.data
                            }],
                        )

                    await websocket.send_json({
                        "type": "message",
                        "id": str(uuid4()),
                        "role": "assistant",
                        "content": assistant_message,
                        "created_at": datetime.now(UTC).isoformat(),
                        "provider": "gemini",
                    })
                    await session_store.append_message(role="assistant", content=assistant_message)
                except Exception as exc:
                    error_msg = f"Jarvis encountered an error resuming: {exc}"
                    print(f"ERROR: {error_msg}")
                    await websocket.send_json({
                        "type": "message", "id": str(uuid4()), "role": "assistant",
                        "content": error_msg, "created_at": datetime.now(UTC).isoformat(),
                        "provider": "fallback",
                    })
                    
                await websocket.send_json({"type": "status", "state": "idle"})
                continue

            # Normal Chat Flow
            message = str(payload.get("message", "")).strip()
            image_data = payload.get("image")
            if image_data == "": image_data = None

            if not message and not image_data:
                await websocket.send_json({"type": "error", "message": "Message cannot be empty."})
                continue

            attachment_metadata = {"image": image_data} if image_data else None
            await session_store.append_message(role="user", content=message, attachment_metadata=attachment_metadata)

            if not settings.gemini_api_key:
                fallback = (
                    "Gemini API key is not configured. "
                    "Please set GEMINI_API_KEY in backend/.env and restart."
                )
                await websocket.send_json({"type": "status", "state": "error"})
                await websocket.send_json({
                    "type": "message", "id": str(uuid4()), "role": "assistant",
                    "content": fallback, "created_at": datetime.now(UTC).isoformat(),
                    "provider": "fallback",
                })
                await session_store.append_message(role="assistant", content=fallback)
                await websocket.send_json({"type": "status", "state": "idle"})
                continue

            session_messages = await session_store.load_session()
            history: list[dict[str, str]] = []
            for m in session_messages:
                role = m["role"]
                content = m.get("content", "").strip()
                if not content: continue
                if role == "assistant": role = "model"
                if role in ("user", "model"):
                    history.append({"role": role, "content": content})

            await websocket.send_json({"type": "status", "state": "processing"})

            try:
                async def _heartbeat():
                    while True:
                        await asyncio.sleep(8)
                        try: await websocket.send_json({"type": "heartbeat"})
                        except Exception: break

                heartbeat = asyncio.create_task(_heartbeat())

                async def _emit_orb(state: str) -> None:
                    await websocket.send_json({"type": "orb_state", "orb_state": state})
                
                async def _emit_progress(msg: str) -> None:
                    await websocket.send_json({"type": "research_progress", "message": msg})

                token = orb_emitter.set(_emit_orb)
                progress_token = progress_emitter.set(_emit_progress)
                
                # F13: Generate a NEW thread_id for every new message
                active_thread_id = str(uuid4())
                thread_config = {"configurable": {"thread_id": active_thread_id}}
                
                try:
                    result = await jarvis_graph.ainvoke({
                        "user_message": message,
                        "history": history,
                        "memory_facts": [],
                        "image_data": image_data,
                    }, config=thread_config)
                finally:
                    orb_emitter.reset(token)
                    progress_emitter.reset(progress_token)
                    heartbeat.cancel()

                # F13: Check if graph paused for approval
                if "__interrupt__" in result:
                    interrupt_data = result["__interrupt__"][0].value
                    await websocket.send_json({
                        "type": "approval_request",
                        "action_type": interrupt_data.get("action_type"),
                        "draft": interrupt_data.get("draft"),
                        "tool_name": interrupt_data.get("tool_name")
                    })
                    continue # Graph is paused, wait for user to click approve

                # If graph finishes normally, clear the thread ID
                active_thread_id = None
                
                assistant_message = result.get("final_reply") or ""

                print("=" * 80)
                print("GRAPH RESULT:")
                print(f"  tool_call   : {result.get('tool_call')}")
                print(f"  final_reply : {assistant_message[:120]}")
                print("=" * 80)

                tool_result = result.get("tool_result")
                if tool_result:
                    await session_store.append_message(
                        role="tool", content=tool_result.content,
                        tool_results=[{
                            "tool": tool_result.tool, "success": tool_result.success,
                            "data": tool_result.data
                        }],
                    )

                ws_payload = {
                    "type": "message", "id": str(uuid4()), "role": "assistant",
                    "content": assistant_message, "created_at": datetime.now(UTC).isoformat(),
                    "provider": "gemini",
                }

                tool_data = result.get("tool_data") or {}
                if tool_data.get("code_result"):
                    ws_payload["code_result"] = tool_data["code_result"]

                await websocket.send_json(ws_payload)
                await session_store.append_message(role="assistant", content=assistant_message)

            except Exception as exc:
                error_msg = f"Jarvis encountered an error: {exc}"
                print(f"ERROR: {error_msg}")
                await websocket.send_json({
                    "type": "message", "id": str(uuid4()), "role": "assistant",
                    "content": error_msg, "created_at": datetime.now(UTC).isoformat(),
                    "provider": "fallback",
                })
                await session_store.append_message(role="assistant", content=error_msg)

            await websocket.send_json({"type": "status", "state": "idle"})

    except WebSocketDisconnect:
        # FIX: Gracefully handle disconnects anywhere in the function
        print("[WebSocket] Client disconnected abruptly.")
        return
    except Exception as e:
        print(f"[WebSocket] Unexpected error: {e}")
        return


