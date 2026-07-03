# Jarvis Backend

FastAPI service for Jarvis 2.0.

Current MVP endpoints:

- `GET /api/health`
- `POST /api/chat`
- `WS /ws/chat`

The chat response is currently a stub so the UI loop can be developed before the LLM provider is wired in.
