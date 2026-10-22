# Jarvis 2.0

Jarvis is a personal AI assistant that connects conversation to tools, specialist agents, and memory. Gemini and LangGraph interpret requests, retrieve relevant memories, plan tool calls, review results, and respond through a React interface. Text, images, files, and turn-based voice feed the same assistant workflow.

The project was built around a single owner, Abhi. Some services still use the internal user ID `abhi`; cloning the repository does not create a multi-user product. This is an actively developed local application with implemented Phase 1.5 security and DevOps controls. The expanded Phase 2 conversation-memory system is planned work.

## Current Capabilities

| Area | Implementation |
| --- | --- |
| Conversation | WebSocket chat, animated orb states, workflow progress, and tool-result rendering |
| Everyday tools | Date/time, approximate IP-based location, weather, news, web search, and system information |
| Research | Source gathering, claim/evidence tracking, coverage review, and reports through a research subgraph |
| Engineering | Requirements, planning, file generation, testing, repair, and packaging; the workflow can use research and sandbox execution |
| Financial analysis | Company research, financial facts, reconciliation, calculated metrics, and reports |
| Code and files | E2B-backed execution, image/file input, generated artifacts, and optional filesystem MCP access restricted to `backend/sandbox/` |
| Google integrations | Gmail reading/sending and Calendar access through OAuth; email sending and event creation use approval flows |
| Music | YouTube search, browser playback controls, queues, and favorites |
| Location sharing | Temporary consent links delivered through an Android SMS gateway; recipients must accept and grant browser location access |
| Voice | Recorded audio transcription through Gemini, the shared chat/tool pipeline, and browser speech output |
| Memory | Redis session/task state, local ChromaDB semantic memory, memory management, and conversation export |

The older web-developer implementation remains as backup code. The active engineering workflow is exposed through `engineering_team_tool`. Not every file in `agents/` or `tools/` is an active capability.

## Architecture and Data

```text
React UI -> FastAPI REST / WebSocket -> LangGraph orchestration
                                      |-> Tools and specialist subgraphs
                                      |-> Gemini responses and embeddings
                                      |-> Redis session/task/runtime state
                                      |-> Local ChromaDB semantic memory
                                      `-> SQLite encrypted Google OAuth tokens
```

| Data | Native development location | Docker storage |
| --- | --- | --- |
| Session, tasks, music runtime state | Redis database 0 | `redis-data` volume |
| Tracking sessions | Redis database 1 | Same Redis service and volume |
| Semantic memory | `backend/data/chroma/` | `backend-data` mounted at `/app/data` |
| Encrypted OAuth tokens | `backend/data/jarvis_auth.db` | `backend-data` |
| Music favorites backup | Under `backend/data/` | `backend-data` |
| Uploads | `backend/uploads/` | `backend-uploads` |
| Generated files and conversation JSON exports | `backend/exports/` | `backend-exports` |
| Filesystem MCP workspace | `backend/sandbox/` | `backend-sandbox` |
| Engineering output | `backend/engineering_team_work/` | `backend-engineering-work` |

Session keys use the UTC date and expire according to `SESSION_TTL_SECONDS` (default 86,400 seconds). The UI can restore the current Redis session after refresh, but a durable, browsable archive of all chats and reliable cross-chat recall are not implemented. The graph checkpointer is in memory; pending approvals do not survive a backend restart. Session storage has a process-memory fallback, which is not durable; other Redis-dependent features still need Redis.

**Export currently downloads Markdown, writes a JSON archive, and clears the current Redis session.** ChromaDB stores selected semantic memories, not complete transcripts. Docker volumes retain files across ordinary container recreation, but a Redis volume alone does not guarantee every recent write survives a crash; this Compose file does not enable append-only persistence.

## Prerequisites

Commands below use PowerShell. Use a separate terminal for each long-running process. Start each setup section from the repository root unless stated otherwise.

- Git and internet access for dependencies and external services.
- A Gemini API key with access to the configured response/STT model and `gemini-embedding-001` embeddings.
- Docker path: Docker Desktop running Linux containers, with Docker Compose.
- Native development path: Python 3.13, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Node.js 20.19+ with npm. Redis can still run in Docker.
- Remote location consent: [ngrok](https://ngrok.com/download/windows), an ngrok account, and an Android SMS Gateway reachable from the backend.

Local ports: `5173` for frontend, `8000` for backend, and `6379` for Redis. Provider services may have quotas or charges. Docker does not make external integrations work offline.

## Clone and Configure

```powershell
git clone https://github.com/abhishekpoddar29/JARVIS.git
cd JARVIS
Copy-Item .env.example backend/.env
```

For the existing `JARVIS-publish` checkout, use that folder as the root and skip cloning. Do not overwrite an already-configured `.env` when updating a checkout.

Edit `backend/.env` with your own values. **No real credentials belong in a Git commit.** Commit only blank placeholders and safe defaults in `.env.example`. Keep API keys, OAuth tokens, cookies, SMS credentials, ngrok authtokens, and encryption keys private. `.env` files and `backend/auth/credentials.json` are ignored. The OAuth code reads environment variables, so that JSON file is not required for startup or Google login.

### Required Startup Settings

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Required for the assistant and semantic-memory embedding client |
| `TOKEN_ENCRYPTION_KEY` | Valid Fernet key; required at module import even before connecting Google |
| `REDIS_URL` | Session, task, and music state; address depends on run mode |
| `TRACKING_REDIS_URL` | Tracking state in Redis database 1 |

Back up `TOKEN_ENCRYPTION_KEY` with your private configuration. Changing it makes existing encrypted OAuth tokens unreadable; reconnect Google after replacing it.

### Optional Integration Credentials

| Feature | Configuration |
| --- | --- |
| Search and research source discovery | `SERPER_API_KEY` |
| Weather | `OPENWEATHER_API_KEY` |
| News | `NEWSDATA_API_KEY` |
| Sandbox execution and engineering tests | `E2B_API_KEY` |
| YouTube music search | `YOUTUBE_API_KEY`; enable YouTube Data API v3 |
| Gmail and Calendar | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, and OAuth consent |
| Location consent | `TRACKING_PUBLIC_BASE_URL`, `TRACKING_ADMIN_TOKEN`, `SMS_GATEWAY_URL`, `SMS_GATEWAY_USERNAME`, `SMS_GATEWAY_PASSWORD` |
| SMS tuning | `SMS_GATEWAY_TIMEOUT_SECONDS`; optionally `SMS_GATEWAY_SIM_NUMBER` |
| Tracing | `LANGCHAIN_TRACING_V2`, `LANGCHAIN_ENDPOINT`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` |
| App access | `JARVIS_APP_TOKEN`, `JARVIS_AUTH_REQUIRED`; matching frontend `VITE_JARVIS_APP_TOKEN` |

Unused provider keys can remain blank; related capabilities will be unavailable or return configuration errors. `PERPLEXITY_API_KEY` and `GOOGLE_ACCOUNT_COOKIE` remain in the example for compatibility, but the current Python implementation does not use them for an active integration. A Google browser cookie is not needed for OAuth.

Model names, YouTube tuning, upload limits, and feature flags are listed in `.env.example`. Model availability depends on your account; defaults do not imply that a model is the provider's latest release.

## Option A: Run Everything with Docker

This starts Redis, backend, and the Vite frontend. Host Python and Node installations are not required for this path.

### 1. Build and Generate an Encryption Key

After creating `backend/.env`, run from the root:

```powershell
docker version
docker compose version
docker compose build
docker compose run --rm --no-deps backend python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the printed key in `backend/.env` as `TOKEN_ENCRYPTION_KEY`, and fill in `GEMINI_API_KEY`. Generate the key once per new installation, not on every restart. This command runs Python without importing the application.

Compose automatically supplies these container addresses:

```dotenv
REDIS_URL=redis://redis:6379/0
TRACKING_REDIS_URL=redis://redis:6379/1
```

### 2. Configure App Access

The checked-in Compose defaults permit a private, local-only smoke test without an app token. Before using a public tunnel, configure a strong app token.

Create a root `.env` with the following, replacing the placeholder with a long random secret:

```dotenv
JARVIS_APP_TOKEN=replace-with-a-long-random-secret
JARVIS_AUTH_REQUIRED=true
ENABLE_SELF_MODIFY=false
ENABLE_MCP_FILESYSTEM=true
ENABLE_CODE_EXECUTION=true
ENABLE_WEB_SCRAPER=true
```

Set matching auth values in `backend/.env` if also using native development. All `VITE_*` values are visible to the browser; this app token is a shared access token for the single-user application. Provider keys must remain in backend configuration.

Compose reads root `.env` for `${...}` interpolation and injects `backend/.env` through `env_file`. Explicit Compose entries for app auth and tool flags override `env_file` values: setting those flags only in `backend/.env` does not change Compose defaults. Compose forwards the root app token to both backend and frontend.

### 3. Start and Check

```powershell
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 backend
docker compose exec redis redis-cli ping
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Expect Redis `PONG`, health `status: ok`, and running services with Redis/backend becoming healthy. Open [Jarvis](http://localhost:5173) and [API documentation](http://127.0.0.1:8000/docs). Health does not validate model credentials, quotas, or tool accuracy.

Logs and shutdown:

```powershell
docker compose logs -f backend frontend
docker compose down
```

`down` preserves named volumes. `docker compose down -v` deletes them; use it only when intending to erase stored Docker data. Native directories and Docker volumes are separate stores; switching run modes does not migrate data.

After changing environment files, recreate services:

```powershell
docker compose up -d --force-recreate backend frontend
```

After changing source or dependencies, rebuild with `docker compose up --build -d`. Compose does not bind-mount the source tree.

## Option B: Native Backend and Frontend

Stop the full Docker stack first if it occupies ports `8000` or `5173`. Keep Docker Desktop running for Redis.

### 1. Prepare the Backend

If uv is missing, install it using the [official guide](https://docs.astral.sh/uv/getting-started/installation/). Windows command:

```powershell
winget install --id=astral-sh.uv -e
```

Open a new terminal after installation. From the repository root:

```powershell
docker compose up -d redis
cd backend
uv python install 3.13
uv sync --python 3.13 --group dev
uv run python -m playwright install chromium
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

On Linux, install browser OS dependencies with `uv run python -m playwright install --with-deps chromium`. Node/npm must be on the backend's PATH because filesystem MCP starts through `npx`; the first start may download its package.

Fill in `GEMINI_API_KEY` and the generated `TOKEN_ENCRYPTION_KEY` in `backend/.env`. Preserve an already-configured encryption key. Replace the example's Docker Redis addresses with:

```dotenv
REDIS_URL=redis://127.0.0.1:6379/0
TRACKING_REDIS_URL=redis://127.0.0.1:6379/1
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

The native backend loads `backend/.env` with `override=True`, so its values take precedence over shell values for the same settings. Still inside `backend/`, start:

```powershell
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Run from `backend/` so imports and relative workspaces resolve correctly. The port argument is `--port 8000`.

If using the requirements file instead of uv, run these inside `backend/` with Python 3.13:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Choose one environment strategy. The requirements path installs runtime dependencies; uv's development group also supplies pytest/Ruff. Python dependencies use version ranges without a checked-in `uv.lock`, so fresh installs can resolve newer versions.

### 2. Start the Frontend

In a second terminal, start from the repository root:

```powershell
cd frontend
npm ci
```

Create `frontend/.env` containing:

```dotenv
VITE_API_URL=http://127.0.0.1:8000
VITE_WS_URL=ws://127.0.0.1:8000/ws/chat
VITE_JARVIS_APP_TOKEN=
```

If backend auth is enabled, set `VITE_JARVIS_APP_TOKEN` to the backend's `JARVIS_APP_TOKEN`. Otherwise leave it empty. Then run:

```powershell
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). Restart Vite after editing its environment. The browser calls the backend directly; Vite is not configured as an API proxy.

### 3. Check and Stop

From a third terminal at the root:

```powershell
docker compose exec redis redis-cli ping
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Stop frontend/backend with `Ctrl+C` in their terminals. Stop Redis with `docker compose stop redis` when finished.

## Connect Google Calendar and Gmail

1. Enable Gmail API and Google Calendar API in your Google Cloud project.
2. Configure the OAuth consent screen, add your account as a test user if needed, and create an OAuth **Web application** client.
3. Register the exact redirect URI `http://localhost:8000/api/auth/google/callback`.
4. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI` in `backend/.env`. Keep the encryption key stable.
5. Restart the native backend or recreate the Docker backend to load these values.
6. Open [Connect Google](http://localhost:8000/api/auth/google) and grant permissions. Tokens are encrypted into the local SQLite store under the current owner ID.

The default localhost Google flow does not need ngrok. If deliberately using a different callback, register it in Google Cloud and match `GOOGLE_REDIRECT_URI` exactly. Tool access depends on the scopes granted; write actions still use their approval workflow.

## ngrok for Location Consent

The recipient needs a public HTTPS URL to open the consent page and permit browser geolocation. Run ngrok on the host, forwarding to backend port `8000`, whether the backend is native or in Docker. The Jarvis UI can remain local on `5173`.

Install ngrok using its [Windows setup guide](https://ngrok.com/download/windows), then run in another terminal:

```powershell
ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN
ngrok http 8000
```

Use your own authtoken and keep this process running. Take the HTTPS origin from ngrok's forwarding output and set these in `backend/.env`:

```dotenv
TRACKING_PUBLIC_BASE_URL=https://your-assigned-host.ngrok-free.app
TRACKING_ADMIN_TOKEN=replace-with-a-separate-random-admin-token
SMS_GATEWAY_URL=http://YOUR_ANDROID_PHONE_LAN_IP:8080
SMS_GATEWAY_USERNAME=your-gateway-username
SMS_GATEWAY_PASSWORD=your-gateway-password
SMS_GATEWAY_TIMEOUT_SECONDS=10
SMS_GATEWAY_SIM_NUMBER=
```

Use the real gateway URL/port and credentials reported by your Android app; `8080` is an example. The backend must reach that address. Container `localhost` refers to the container, not the phone or host. Use a reachable phone LAN address, or `host.docker.internal` for a gateway forwarded onto the Docker Desktop host.

Restart the native backend, or run from the root for Docker:

```powershell
docker compose up -d --force-recreate backend
```

The public origin is read at startup. Update it and restart again if the tunnel address changes. Do not include `/track` in `TRACKING_PUBLIC_BASE_URL`; Jarvis appends `/track/<public-token>` itself. The tracking admin token is separate from the app token.

Only request location from someone who agrees to test. The current tool sends the consent link by SMS; it is not a link-only implementation. Sharing remains pending until the recipient accepts and grants location access. Missing gateway configuration causes the tool to end the session and report failure.

The tunnel exposes the backend, including routes outside the app-token gate such as OAuth entry/callback and public consent. Configure app auth for protected routes and treat this as a development tunnel, not a hardened public deployment. Tunneling `5173` alone does not make the complete UI remotely usable: its default API/WebSocket URLs point to the viewer's localhost, and public frontend access also needs appropriate URLs, origins, host configuration, and access controls.

## Validate the Build and Prompting

From `backend/`, after installing the uv development group:

```powershell
uv run pytest tests/test_app_auth.py tests/test_safe_url.py tests/test_uploads.py -q
```

These tests cover token checks, URL validation, and uploads, not the complete agent workflow. The default-auth test assumes auth settings are unset; a configured `backend/.env` or inherited auth settings can affect it. Use an isolated test environment with those settings unset when checking default behavior.

From `frontend/`:

```powershell
npm ci
npx tsc --noEmit
npm run build
```

Watch Docker tool execution from the root with `docker compose logs -f backend`, or inspect the native backend terminal.

| Prompt / action | Expected behavior and acceptance criterion |
| --- | --- |
| `Hello, what can you help me with?` | Chat connects, responds, and returns to an idle UI state |
| `What is the current date and time?` | Calls `datetime_tool`; compare its result to the backend clock and intended timezone |
| `What is the weather in Bengaluru?` | Uses weather with a configured key; final values must match the tool output |
| `Summarize https://example.com` | Uses the scraper; compare the answer with extracted text; the known context issue may currently cause failure |
| `What is the latest news?` | Uses fetched news; inspect source dates and reject unsupported claims of freshness |
| `What is 17 multiplied by 23?` | Returns `391`; a basic response check, not evidence that external tools work |
| Explicitly draft/send a test email | Pauses for write approval; rejecting must not send the message |
| Upload a small `.txt`, then a disallowed `.exe` | Accepts allowed text and rejects the disallowed extension; stored uploads use generated names |
| `Fetch http://127.0.0.1:8000/api/health with the web scraper` | Scraper blocks loopback/private destinations when private-network blocking is enabled |
| Refresh during a current-day session | Restores messages with working Redis; this does not prove cross-chat memory |

For compound requests, verify each requested task against the logs. A successful call is insufficient if the answer drops its result, mixes tasks, or adds unsupported facts. Test code execution, research, financial reports, music, and location separately with the necessary accounts and explicit test data.

## Observed Results and Limitations

This records earlier reported development observations and inspection of the checked-in implementation. It is not a fresh benchmark or a completed clean-machine test run.

| Area | Observation / limitation |
| --- | --- |
| Docker startup | After adding the missing Jinja2 dependency, reported logs reached health responses and accepted WebSocket chat connections |
| Weather | Returned a provider-backed result in the reported conversation; this does not validate other tools |
| Scraper answers | Extracted text is in `ToolResult.data.text`, but the observation builder forwards only `content` and selected metadata. The model can receive a success message without the article body, causing summary/verification failures even when extraction worked |
| Date/time | `datetime_tool` uses timezone-naive `datetime.now()`. A UTC container clock can disagree with Indian local time; a configurable tool timezone is not implemented |
| Search/news | Reported answers included stale or low-relevance information; freshness and source dates need checking |
| Research | A report can return as successful/terminal with incomplete verification or no approved evidence. Inspect `verification_status`, `decision_safe`, source counts, and evidence before trusting conclusions |
| Music favorites | Earlier Docker logs showed an intermittent temporary-file replacement error; persistence under concurrent requests needs validation |
| Frontend validation | TypeScript checking previously passed. A prior production-build attempt hit environment permissions; rerun a complete clean build |
| Security tests | Focused tests exist; earlier execution was blocked by missing pytest/environment access. No completed automated pass is claimed here |
| Security scope | App tokens, file controls, URL checks, and feature gates are implemented. This is not a completed security audit; URL validation does not guarantee protection from every SSRF bypass |
| Phase 1.5 | Implemented controls are present; full acceptance remains pending tool-result fidelity, timezone/freshness, and end-to-end regression validation |
| Phase 2 | Durable conversation history, working summaries, cross-chat recall, and linked memory nodes remain planned |

A generated report is not proof of factual accuracy. Distinguish provider errors, extraction failures, incomplete evidence, and results omitted from model context when diagnosing answers.

## Troubleshooting

| Symptom | Check / action |
| --- | --- |
| Fernet-key startup error | Generate a valid `TOKEN_ENCRYPTION_KEY` and configure it before starting |
| Model/embedding error | Check Gemini credentials, configured model access, network, quota, and provider errors in logs |
| Native Redis hostname error | Use `127.0.0.1`, not Docker's `redis` hostname |
| Container prints `/app/.env (exists: False)` | Expected with `env_file`; values are injected into the process and the file is excluded from the image |
| HTTP 401 / rejected WebSocket | Match backend and frontend app tokens; check root Compose overrides and restart/recreate |
| Connection refused | Confirm ports, API/WS URLs, and whether another run mode occupies `8000` or `5173` |
| Playwright browser missing | Install Chromium in the environment launching the backend, or rebuild the image |
| MCP startup failure | Check Node/npm, `npx`, network, and sandbox permissions; disable the feature in the appropriate configuration if unused |
| Google redirect mismatch | Match the registered callback and `GOOGLE_REDIRECT_URI`, including hostname, port, and path |
| Broken consent link | Check the live HTTPS tunnel, public origin, backend recreation, and gateway connectivity |
| Docker self-modification unavailable | Disabled by default; the image does not contain the parent Git repository or a mounted working checkout |
| Package-download timeout | Check network/proxy access to registries; stop and retry the failed step before continuing |

## Project Map and Further Reading

```text
backend/
  main.py                    API routes, startup, WebSocket coordination
  config.py                  Environment settings and feature gates
  agents/                    Main graph, prompts, specialist agents/subgraphs
  tools/                     Registered capabilities and internal helpers
  memory/                    Redis sessions, ChromaDB, distillation, export
  auth/                      Google OAuth and encrypted token storage
  security/                  App token, upload, and URL controls
  tracking/                  Consent pages, sessions, Android SMS gateway
  music/                     YouTube service, player state, favorites
  voice/                     Speech/transcription workflow helpers
  tests/                     Focused security tests
frontend/src/                React UI, sockets, voice, widgets
docker-compose.yml           Redis/backend/frontend services and volumes
.env.example                 Safe environment-variable template
```

- [Project documentation](Jarvis__2.0_Updated_17-08-26.md)
- [Connected tools, agents, and memory diagram](Jarvis_Low_Level_Diagram.md)
- [Phase 1.5 security and DevOps plan](JARVIS_PHASE_1_5_SECURITY_DEVOPS.md)
- [Phase 2 memory plan](JARVIS_PHASE_2.md)
- [ngrok localhost-sharing reference](https://ngrok.com/use-cases/share-localhost)

Planning documents contain proposals and descriptions of earlier builds. Use the actual implementation and the limitations above to assess what is available in this checkout.
