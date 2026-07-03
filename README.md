# Jarvis 2.0

Persistent, stateful AI personal assistant for a single user.

This repository starts with the v0.3 MVP scope:

- FastAPI backend with health and chat endpoints
- React + TypeScript + Vite frontend
- Basic orb state indicator
- Session-style chat UI
- Project structure ready for tools, memory, and LangGraph

## Local Development

Recommended runtime:

- Python 3.13+
- Node 20.12 works with the current Vite 5 pin
- Node 20.19+ is recommended when upgrading to Vite 8+

Backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Dependency Note

The frontend is currently pinned to Vite 5 so it runs on the installed Node 20.12 runtime. `npm audit` reports one moderate Vite dev-server advisory on this line. The clean upgrade path is Node 20.19+ followed by Vite 8+ and `@vitejs/plugin-react` 6+.

## MVP Roadmap

1. Health endpoint and basic chat loop
2. WebSocket streaming
3. Session memory
4. Basic tool registry
5. Web search
6. Export conversations
7. MVP orb state polish
