# JARVIS PHASE 1.5 - Security Hardening and DevOps Stabilization

> Status: Planning and discussion document only  
> Created: August 18, 2026  
> Project root: `C:\Users\abhir\Desktop\projects\Jarvis-0.2`  
> Scope: Current build review, security risks, DevOps structure, Docker/UV plan, hardening plan, and smoke-test strategy  
> Rule: This document does not change source code

---

## 1. Why Phase 1.5 Exists

Phase 2 will make Jarvis remember more, persist more, and connect more information across conversations. That is powerful, but it also means any weak assumption in the current codebase becomes more important.

Before building durable memory, Jarvis needs a hardening phase.

Phase 1.5 exists to make sure:

- Jarvis can run predictably from a clean environment.
- Docker Compose can start the full project, not only part of it.
- Python dependencies are reproducible with `uv`.
- File uploads and downloads are safer.
- URL-fetching tools do not become SSRF paths.
- Tool capabilities are gated based on risk.
- Local secrets and credentials are handled carefully.
- Smoke tests catch broken runtime assumptions early.

The goal is not to turn Jarvis into a public multi-user SaaS app immediately. The goal is to make the local single-user assistant safe enough and stable enough that Phase 2 memory work does not preserve fragile foundations.

---

## 2. Where Jarvis Is Now

Jarvis is currently a local-first personal AI assistant.

### 2.1 Current backend

Backend runtime:

- FastAPI.
- LangGraph.
- Gemini 2.5 Flash.
- Redis session storage.
- ChromaDB semantic memory.
- SQLite auth token store.
- Playwright-based web scraper.
- E2B cloud code sandbox.
- Google OAuth integrations.
- YouTube music service.
- Consent-based location tracking.
- Tool registry and specialist agents.

Primary backend entry point:

- `backend/main.py`

Current backend startup registers:

- `DateTimeTool`
- `LocationTool`
- `WeatherTool`
- `NewsTool`
- `WebSearchTool`
- `SystemInfoTool`
- `TaskMemoryTool`
- `ResearchTool`
- `CodeExecutionTool`
- `CalendarTool`
- `EmailTool`
- `WebScraperTool`
- `SelfModifyTool`
- `EngineeringTeamTool`
- `FinancialAnalystTool`
- `LocationShareTool`
- `MusicPlayerTool`
- MCP filesystem tools

### 2.2 Current frontend

Frontend runtime:

- React.
- TypeScript.
- Vite.
- WebSocket chat.
- Browser voice capture.
- Browser speech output.
- Session restore from `/api/session`.
- Memory viewer.
- Music widget.
- Tracking widget.
- Jarvis brain visualization.
- History drawer for current session.

Current limitation:

- Frontend is not part of Docker Compose.
- Frontend restore is tied to today's Redis session, not durable conversation threads.

### 2.3 Current data layer

Jarvis currently uses several local data stores.

Redis:

- Session chat memory.
- Temporary task memories.
- Research context key-value storage.
- Music/tracking state when Redis is available.

ChromaDB:

- Long-term semantic memory.
- Gemini embeddings.
- Stored under backend data path.

SQLite:

- OAuth token store in `backend/data/jarvis_auth.db`.

Filesystem:

- `backend/uploads`
- `backend/exports`
- `backend/data/chroma`
- `backend/engineering_team_work`
- `backend/sandbox`
- generated code artifacts

Current concern:

These storage areas exist, but they are not yet clearly modeled as Docker volumes, runtime-only data, or version-controlled assets.

### 2.4 Current Docker and runtime state

Current root Compose file:

- `docker-compose.yml`

Current behavior:

- Starts backend.
- Starts Redis.
- Does not start frontend.
- Does not define health checks.
- Does not mount all runtime data directories.
- Uses `env_file: .env`, but the real `.env` currently lives under `backend/.env`.
- Backend image uses `pip install -r requirements.txt`.

Current backend Dockerfile:

- `backend/Dockerfile`

Current behavior:

- Uses `python:3.13-slim`.
- Installs Python requirements with pip.
- Does not install Node.js.
- Does not install Playwright browser binaries.
- Does not install Playwright system dependencies.
- Does not use `uv`.

Current local scripts:

- `scripts/dev.ps1`

Current behavior:

- Prints manual backend/frontend startup commands.
- Does not actually orchestrate services.
- Uses `pip` and `requirements.txt`, not `uv`.

---

## 3. Research Basis

This hardening plan is based on the current codebase plus standard security and deployment guidance.

### 3.1 File upload guidance

OWASP recommends:

- Allowlisted extensions.
- File size limits.
- Generated server-side filenames.
- File type validation beyond user-provided `Content-Type`.
- Authorized upload access.
- Storage outside public webroot when possible.
- Safe file retrieval through application-controlled IDs.

References:

- [OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)
- [OWASP Input Validation Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html)
- [OWASP Unrestricted File Upload](https://owasp.org/www-community/vulnerabilities/Unrestricted_File_Upload)

### 3.2 SSRF guidance

OWASP describes SSRF as a risk when an application fetches a URL supplied by a user or model-controlled path.

Relevant risks:

- Access to localhost services.
- Access to Docker internal network services.
- Access to cloud metadata endpoints.
- Access to private IP ranges.
- Redirect chains that land on internal addresses.

References:

- [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
- [OWASP Server Side Request Forgery](https://owasp.org/www-community/attacks/Server_Side_Request_Forgery)

### 3.3 API security guidance

OWASP API Security Top 10 highlights risks that matter for Jarvis:

- Broken authentication.
- Broken function-level authorization.
- Unrestricted resource consumption.
- SSRF.
- Security misconfiguration.
- Unsafe third-party API consumption.

Reference:

- [OWASP API Security Top 10 2023](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)

### 3.4 Docker and FastAPI guidance

FastAPI documents containerized deployment as a normal packaging path and recommends explicit dependency installation and command configuration.

Reference:

- [FastAPI in Containers](https://fastapi.tiangolo.com/deployment/docker/)

Docker Compose recommends health checks and dependency conditions when service readiness matters.

Reference:

- [Docker Compose startup order and health checks](https://docs.docker.com/compose/how-tos/startup-order/)

### 3.5 Playwright Docker guidance

Playwright requires browser binaries and system dependencies. The docs also warn that browser automation against untrusted websites should run with a safer user and container configuration.

References:

- [Playwright Python browsers](https://playwright.dev/python/docs/browsers)
- [Playwright Python Docker](https://playwright.dev/python/docs/docker)

### 3.6 UV dependency guidance

The project should move toward `pyproject.toml` plus `uv.lock` for reproducible Python environments. FastAPI's container guide notes that `uv` projects keep direct dependencies in `pyproject.toml` and resolved versions in `uv.lock`.

Reference:

- [FastAPI in Containers - uv package requirements](https://fastapi.tiangolo.com/deployment/docker/)

---

## 4. Current Security Risks

This section lists the biggest risks found from the current codebase.

### 4.1 No unified app authentication

Current situation:

- Tracking admin routes require `TRACKING_ADMIN_TOKEN`.
- Most other backend routes do not have an app-level auth guard.
- `/ws/chat` accepts WebSocket messages without an app token.
- `/api/upload`, `/api/files`, `/api/memory`, `/api/export`, `/api/voice/transcribe`, `/api/auth/google`, and music APIs are accessible if the backend is reachable.

Why this matters:

Jarvis has powerful tools:

- Email.
- Calendar.
- File upload.
- File download.
- Voice transcription.
- Memory edit/delete.
- Code execution through E2B.
- Web scraping.
- Self-modification.

In a strictly local environment, this is acceptable during early development. But once Docker Compose exposes ports, the app should assume anything listening on `0.0.0.0` can be reached by more than the intended browser in some network setups.

What to improve:

- Add a simple local app auth layer.
- Use one `JARVIS_APP_TOKEN` or session cookie for local admin access.
- Require it for powerful APIs.
- Require it for WebSocket chat.
- Keep public tracking consent routes tokenized but separate.

Recommended first fix:

```text
Add optional app-token middleware:
- disabled only in explicit local dev mode
- enabled by default in Docker
- protects chat, memory, upload, files, export, music, voice, OAuth, and admin APIs
```

### 4.2 Upload endpoint is too trusting

Current file:

- `backend/main.py`

Current behavior:

```text
safe_filename = file.filename.replace(" ", "_")
file_path = UPLOADS_DIR / safe_filename
```

Problem:

This is not enough filename protection.

Risks:

- Path traversal attempts.
- Overwriting existing files.
- Long filenames.
- Weird Unicode/control characters.
- Double extensions.
- Uploading unexpected active content.
- Uploading very large files.
- Filling disk.
- Confusing later tools that read uploaded files.

Simple explanation:

Right now Jarvis mostly trusts the name supplied by the browser. A safer system should ignore that name for storage and generate its own filename.

How to solve:

- Generate a UUID filename.
- Preserve original filename only as metadata.
- Allow only specific extensions:
  - `.csv`
  - `.json`
  - `.txt`
  - `.xlsx`
  - selected image types if used
- Enforce max upload size.
- Reject empty files.
- Reject hidden files and strange names.
- Store uploads outside directly served paths.
- Return an upload ID, not the raw filename.
- Add MIME sniffing where practical.

Recommended new behavior:

```text
User uploads my data.csv
-> backend validates extension and size
-> backend stores uploads/2026/08/<uuid>.csv
-> backend records original name as metadata
-> frontend receives upload_id
-> tools access file by upload_id, not raw filename
```

### 4.3 File download can serve active HTML

Current file:

- `backend/main.py`

Current behavior:

- `/api/files/{filename}` serves artifacts from exports.
- HTML files are served as `text/html`.

Problem:

If generated HTML contains script, and it is served from the backend origin, it can run in a privileged app context.

Simple explanation:

Generated files should be treated as untrusted output. Even if Jarvis generated them, the code may come from model output or user instructions.

How to solve:

- Serve generated artifacts as attachments by default.
- Avoid inline rendering for `.html`.
- Add security headers:
  - `Content-Disposition: attachment`
  - `X-Content-Type-Options: nosniff`
  - `Content-Security-Policy: sandbox`
- Consider a separate preview origin later.
- Keep the current path traversal check, but also normalize artifact IDs.

Recommended first fix:

```text
All /api/files downloads become attachments.
HTML preview becomes a separate explicitly sandboxed feature later.
```

### 4.4 Web scraper has SSRF risk

Current file:

- `backend/tools/web_scraper_tool.py`

Current behavior:

- Accepts any `http://` or `https://` URL with a netloc.
- Launches Playwright and visits it.

Problem:

This blocks non-http schemes, which is good, but it does not block internal network addresses.

SSRF examples to block:

- `http://localhost:8000`
- `http://127.0.0.1:6379`
- `http://0.0.0.0`
- `http://169.254.169.254`
- private IP ranges
- Docker service hostnames
- IPv6 loopback
- redirects from public URL to private URL

Simple explanation:

The scraper is a server-side browser. If Jarvis can be tricked into browsing internal URLs, it might read services that were never meant to be public.

How to solve:

- Parse URL.
- Resolve hostname to IP.
- Reject private, loopback, link-local, multicast, and reserved IPs.
- Reject localhost-like hostnames.
- Reject Docker internal hostnames unless explicitly allowed.
- Enforce allowed schemes.
- Re-check final URL after redirects.
- Add timeout and response-size limits.
- Optionally add domain allowlist for high-risk modes.

Recommended first fix:

```text
Add a shared safe_url.py helper.
Use it in web_scraper_tool, research fetching, tracking gateway URLs if needed, and any future URL fetcher.
```

### 4.5 Self-modification is powerful

Current files:

- `backend/tools/self_modify_tool.py`
- `backend/tools/git_manager.py`

Current behavior:

- Model can propose writing a new Python tool.
- Tool requires user approval in orchestration.
- Filename must end in `.py`.
- Filename must start with `custom_`.
- Path is constrained to `tools/`.
- Git branch is created.
- File is written and committed.

Good existing controls:

- Requires approval path.
- Limits filenames.
- Limits directory.
- Uses git branch.

Remaining risks:

- Tool code may import dangerous libraries.
- Tool code may run on future startup if registered.
- Branch creation changes user workspace state.
- Current branch handling assumes `main`.
- File validation is syntactic only, not security-focused.
- Approval may not show enough risk context.

Simple explanation:

Self-modification is useful, but it is one of Jarvis's highest-risk capabilities because it turns a model output into project code.

How to solve:

- Add `ENABLE_SELF_MODIFY=false` by default in Docker.
- Require explicit config enablement.
- Require HIL approval every time.
- Show a security review summary before approval.
- Run static checks before writing.
- Validate AST for risky imports/functions.
- Save generated code to a draft area first.
- Do not auto-load new tools until restart or explicit approval.
- Ensure git operations never assume branch names.

Recommended first fix:

```text
Keep self_modify_tool registered only when ENABLE_SELF_MODIFY=true.
In Docker default, disable it.
```

### 4.6 MCP filesystem tools need explicit limits

Current file:

- `backend/tools/mcp_bridge.py`

Current behavior:

- Starts MCP filesystem server with:
  - `npx -y @modelcontextprotocol/server-filesystem`
  - root path `backend/sandbox`

Good existing control:

- Filesystem access is scoped to `backend/sandbox`.

Remaining risks:

- Backend Docker image currently does not install Node/npm, so this may fail.
- `npx -y` downloads/runs package at startup if not cached.
- Startup depends on network/package registry.
- Filesystem write tools are powerful if prompt injection reaches them.
- Sandbox contents may persist unexpectedly.

Simple explanation:

The MCP filesystem is a local file tool. It is scoped, but it should be predictable and not depend on dynamic package downloads at runtime.

How to solve:

- Add Node/npm to backend Docker image or move MCP to a separate service.
- Pin MCP package version.
- Avoid `npx -y` runtime installs in production.
- Add `ENABLE_MCP_FILESYSTEM` config.
- Keep sandbox mounted as a dedicated volume or temp directory.
- Clean or rotate sandbox files if needed.

Recommended first fix:

```text
Make MCP filesystem optional and disabled if Node/npm is unavailable.
Pin the MCP server package version.
```

### 4.7 CORS is broad for local dev

Current file:

- `backend/main.py`

Current behavior:

- CORS origins come from config.
- Credentials allowed.
- All methods allowed.
- All headers allowed.

Problem:

This is fine for dev if origins are local-only. It becomes dangerous if origins are widened.

How to solve:

- Keep local-only defaults.
- In Docker, set frontend origin explicitly.
- For production-like mode, fail startup if CORS includes `*` with credentials.
- Restrict methods/headers later if needed.

### 4.8 OAuth token storage depends on key hygiene

Current file:

- `backend/auth/token_store.py`

Current behavior:

- SQLite token DB.
- Fernet encryption using `TOKEN_ENCRYPTION_KEY`.

Good existing control:

- Tokens are encrypted at rest.

Remaining risks:

- Encryption key must be valid and protected.
- `backend/auth/credentials.json` exists in the project tree.
- Docker needs secret mounting or env injection.
- Token DB should be mounted as runtime data, not baked into image.

How to solve:

- Keep `TOKEN_ENCRYPTION_KEY` only in `.env` or Docker secret.
- Move Google credentials to env variables or a mounted secret file.
- Add `.env.example`.
- Ensure actual credentials are ignored.
- Mount `backend/data` as a volume.

### 4.9 Resource consumption risks

Current surfaces:

- Voice transcription uploads.
- File uploads.
- Web scraping with Playwright.
- Research agent loops.
- Financial analyst scraping.
- Code execution through E2B.
- Music search.
- Tracking WebSocket.

Good existing controls:

- Voice has max audio bytes.
- Tracking WebSocket has payload size and rate limit.
- Tool orchestration has max rounds and max calls.
- Code execution has sandbox timeout.

Needed improvements:

- Upload size limits.
- Request rate limits.
- WebSocket message size limits.
- Scraper content size limits.
- Per-tool timeout standardization.
- Per-tool enable/disable flags.
- Cost-control guardrails for paid APIs.

---

## 5. DevOps Problems And Fixes

### 5.1 Docker Compose is incomplete

Current problem:

Compose starts only:

- backend
- Redis

Missing:

- frontend service
- volumes
- health checks
- startup ordering
- backend dependency readiness
- `.env.example`
- clear local URL output

How to solve:

Add a complete Compose stack:

```text
services:
  redis
  backend
  frontend

volumes:
  redis-data
  backend-data
  backend-uploads
  backend-exports
  backend-sandbox
  playwright-cache if needed
```

Backend should depend on Redis health.

Frontend should depend on backend health or at least start after backend.

### 5.2 Backend Docker image does not match runtime needs

Current problem:

Backend Dockerfile installs Python requirements only.

But runtime needs:

- Python dependencies.
- Node/npm for MCP.
- Playwright browsers.
- Playwright Linux system dependencies.
- possibly git for self-modification.
- non-root runtime user.

How to solve:

Update backend Docker strategy:

- Use `uv` for dependency installation.
- Add `pyproject.toml`.
- Add `uv.lock`.
- Install Node/npm if MCP is enabled.
- Install Playwright Chromium with dependencies.
- Run as a non-root user where possible.
- Set explicit working directory.
- Add health check.

Important Playwright note:

Playwright docs recommend specific Docker handling for browser automation. For scraping untrusted websites, use a separate user and safer container settings.

### 5.3 No `pyproject.toml` for backend

Current problem:

Backend has `requirements.txt`, but no project metadata file for `uv`.

How to solve:

Create:

- `backend/pyproject.toml`
- `backend/uv.lock`

Recommended `pyproject.toml` content:

- project name
- Python version requirement
- runtime dependencies copied from `requirements.txt`
- dev dependencies:
  - pytest
  - pytest-asyncio
  - ruff
  - mypy optional
  - httpx test support if needed

Keep `requirements.txt` initially:

- It can be generated from `uv`.
- It preserves compatibility with existing scripts during migration.

### 5.4 Frontend Dockerfile is missing

Current problem:

Frontend runs only locally through `npm run dev`.

How to solve:

Create:

- `frontend/Dockerfile`

For development:

- run `npm install`
- run `npm run dev -- --host 0.0.0.0`
- expose `5173`

For production later:

- build static files
- serve with nginx or a simple static server

Phase 1.5 only needs development Compose to run the full project.

### 5.5 Runtime data needs explicit volumes

Current data directories:

- `backend/data`
- `backend/uploads`
- `backend/exports`
- `backend/sandbox`
- `backend/engineering_team_work`

Problem:

If these are copied into an image or left unmanaged, behavior becomes confusing.

How to solve:

Map volumes:

- `backend-data:/app/data`
- `backend-uploads:/app/uploads`
- `backend-exports:/app/exports`
- `backend-sandbox:/app/sandbox`
- optional `backend-engineering-work:/app/engineering_team_work`

Also clarify:

- Chroma lives under `data`.
- OAuth token DB lives under `data`.
- generated files live under `exports`.
- uploaded user files live under `uploads`.
- MCP filesystem writes live under `sandbox`.

### 5.6 Environment needs an example file

Current problem:

Secrets and required variables are known by the running app, but there is no clean `.env.example`.

How to solve:

Create:

- `.env.example`
- optionally `backend/.env.example`

Include placeholders:

- `GEMINI_API_KEY`
- `SERPER_API_KEY`
- `REDIS_URL`
- `TOKEN_ENCRYPTION_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI`
- `YOUTUBE_API_KEY`
- `TRACKING_PUBLIC_BASE_URL`
- `TRACKING_ADMIN_TOKEN`
- Android SMS gateway settings
- `JARVIS_APP_TOKEN`
- feature flags:
  - `ENABLE_SELF_MODIFY`
  - `ENABLE_MCP_FILESYSTEM`
  - `ENABLE_WEB_SCRAPER`
  - `ENABLE_CODE_EXECUTION`

---

## 6. File And URL Hardening In Detail

### 6.1 File upload problem

Where:

- `backend/main.py`
- `/api/upload`

What the problem is:

The backend accepts a file and saves it using a lightly modified user-provided filename.

Why this is risky:

- User-controlled filenames are unsafe.
- Filenames can include path tricks.
- Files can overwrite each other.
- Very large uploads can fill disk.
- Bad extensions can later be served or processed.
- Active content can become dangerous if previewed.

Simple fix explanation:

Jarvis should treat uploaded files like untrusted packages. It should inspect the label, generate its own storage name, and only pass around an internal ID.

Detailed fix:

1. Add upload config:
   - `UPLOAD_MAX_BYTES`
   - `UPLOAD_ALLOWED_EXTENSIONS`
   - `UPLOAD_STORAGE_DIR`

2. Add upload helper:
   - `backend/security/uploads.py`

3. Validate:
   - filename exists
   - extension allowlisted
   - size under limit
   - file not empty
   - no unsupported extension

4. Store:
   - generated UUID filename
   - original name as metadata
   - date-based subfolder optional

5. Return:
   - `upload_id`
   - original filename
   - size
   - extension

6. Make tools use:
   - `upload_id`
   - not raw filename

### 6.2 File download problem

Where:

- `backend/main.py`
- `/api/files/{filename}`

What the problem is:

Generated files may be served inline. HTML is specifically served as `text/html`.

Why this is risky:

Generated HTML could include script. If served from the same backend origin, the browser treats it as app-origin content.

Simple fix explanation:

Generated files should download by default, not run inside Jarvis's origin.

Detailed fix:

- Add `Content-Disposition: attachment`.
- Add `X-Content-Type-Options: nosniff`.
- Do not special-case `.html` as inline.
- Use generated artifact IDs.
- Keep path traversal checks.
- Add optional sandboxed preview later.

### 6.3 URL fetching problem

Where:

- `backend/tools/web_scraper_tool.py`
- research source fetching
- financial analyst source fetching
- any future URL fetcher

What the problem is:

The application fetches URLs selected by user/model flow.

Why this is risky:

The backend has network access that the browser may not have. If Jarvis fetches internal URLs, it could expose internal services.

Simple fix explanation:

Jarvis should check not only "is this a URL?" but also "is this safe for the backend to visit?"

Detailed fix:

Create:

- `backend/security/safe_url.py`

Checks:

- scheme must be `http` or `https`
- hostname must exist
- reject localhost names
- resolve DNS
- reject private IPs
- reject loopback IPs
- reject link-local IPs
- reject multicast/reserved IPs
- reject cloud metadata IP
- reject Docker internal service names unless explicitly allowed
- follow redirects carefully and re-check final URL

Add standard limits:

- connect timeout
- read timeout
- max response bytes
- max redirects
- user-agent policy

### 6.4 Artifact hardening problem

Where:

- `backend/agents/code_agent.py`
- `backend/exports`

What the problem is:

Files generated by code execution are collected and made downloadable.

Why this is risky:

Even sandbox-generated files can be untrusted. A model-generated script may produce HTML, JS, or other active files.

Simple fix explanation:

The sandbox protects execution, but downloaded artifacts still need browser-side safety.

Detailed fix:

- Treat artifacts as untrusted.
- Download as attachment.
- Add allowed artifact extensions.
- Consider artifact metadata table.
- Cap artifact count and size.
- Do not recursively download huge trees.
- Keep E2B execution isolated.

---

## 7. Tool Safety Gates In Detail

Jarvis has many tools. The goal is not to disable Jarvis. The goal is to classify tool risk and enforce the right guardrail.

### 7.1 Tool risk levels

Recommended categories:

- `low`: read-only or harmless local context
- `medium`: external requests or larger cost
- `high`: writes data, sends messages, changes files, or affects another person
- `critical`: modifies Jarvis code, runs shell commands, or accesses sensitive tokens

### 7.2 Low-risk tools

Examples:

- datetime
- system info
- weather
- simple news

Controls:

- normal logging
- timeouts
- input validation

### 7.3 Medium-risk tools

Examples:

- web search
- web scraper
- research tool
- financial analyst
- music search

Controls:

- rate limits
- URL safety
- cost limits
- timeout limits
- result-size limits

### 7.4 High-risk tools

Examples:

- email send
- calendar create event
- location share
- task memory clear
- file write through MCP

Controls:

- HIL approval
- explicit user intent
- structured preview
- audit log
- one side-effect action per round

Jarvis already has part of this through serial/HIL orchestration. Phase 1.5 should make it more explicit and configurable.

### 7.5 Critical-risk tools

Examples:

- self-modification
- code execution if allowed to install packages or fetch network
- engineering team if it writes project packages
- MCP filesystem write tools

Controls:

- disabled by default in Docker unless enabled
- app-token required
- HIL approval required
- audit trail
- config feature flag
- strict path boundaries
- static checks where possible

### 7.6 Self-modification gate

Problem:

Self-modification can write new Python files into Jarvis.

Fix:

- Add `ENABLE_SELF_MODIFY`.
- If disabled, do not register tool.
- If enabled, require approval and static review.
- Write drafts to a staging folder first.
- Only load after explicit approval.

### 7.7 MCP filesystem gate

Problem:

MCP exposes filesystem operations under `backend/sandbox`.

Fix:

- Add `ENABLE_MCP_FILESYSTEM`.
- Pin MCP server package version.
- Avoid runtime `npx -y` install when possible.
- Keep sandbox in a dedicated directory.
- Disallow symlink escapes.
- Treat file writes as high risk.

### 7.8 Code execution gate

Problem:

Code execution uses E2B, which is a good isolation boundary, but it can still create cost and fetch external data from generated code.

Fix:

- Keep E2B required.
- Add `ENABLE_CODE_EXECUTION`.
- Add package allowlist or deny high-risk packages.
- Keep timeout.
- Cap artifacts.
- Audit generated code and package installs.

### 7.9 Location sharing gate

Problem:

Location sharing affects another person and sends SMS.

Good current controls:

- Consent link.
- HTTPS public URL validation.
- recipient must allow location.
- duration limit.
- admin token for admin routes.

Fix:

- Keep HIL for SMS request.
- Log consent request without exposing full phone.
- Confirm public URL is not localhost.
- Keep duration caps.

---

## 8. Smoke Tests In Detail

Smoke tests are small tests that answer:

```text
Can the project start?
Can the critical endpoints respond?
Can the core dependencies connect?
Can the frontend talk to the backend?
```

Smoke tests are not full test coverage. They are the alarm system for broken setup.

### 8.1 Current problem

Today, startup can fail because:

- Docker CLI may not exist or Compose may not run full stack.
- Backend venv can have pywin32/Windows dependency issues.
- `uvicorn` may not be on PATH.
- Frontend Vite can fail due to local runtime/path quirks.
- Docker backend lacks Node/npm for MCP.
- Docker backend lacks Playwright browser install.
- Redis may not be ready when backend starts.
- Required API keys may be missing.

Simple explanation:

Right now, "run Jarvis" depends on many hidden assumptions. Smoke tests make those assumptions visible.

### 8.2 Backend smoke tests

Create:

- `backend/tests/test_health.py`
- `backend/tests/test_imports.py`

Test:

- `main.py` imports without crashing when feature flags are safe.
- `/api/health` returns `status=ok`.
- settings load.
- Redis can connect or fallback behavior is explicit.
- Chroma path can initialize.
- app routers mount.

Important:

Import tests should not require paid API keys unless the feature is explicitly enabled.

### 8.3 API smoke tests

Test:

- `/api/health`
- `/api/session` compatibility route
- `/api/memory` with empty memory store
- `/api/voice/transcribe` rejects non-audio cleanly
- `/api/upload` rejects disallowed files
- `/api/files` rejects missing files

### 8.4 WebSocket smoke test

Test:

- connect to `/ws/chat`
- receive initial status
- close cleanly

Do not require Gemini for this first smoke test.

### 8.5 Frontend smoke tests

Create:

- frontend build smoke
- optionally Playwright UI smoke later

Test:

- `npm run build`
- app compiles
- TypeScript passes
- no missing env variables for local defaults

### 8.6 Docker smoke tests

Test:

- `docker compose config`
- `docker compose build`
- `docker compose up`
- Redis healthy
- backend healthy
- frontend reachable
- backend can reach Redis
- backend logs registered tools or clearly disabled optional tools

### 8.7 Tool smoke tests

Only test safe behavior:

- disabled tools return clear unavailable messages
- web scraper rejects localhost/private URL
- upload rejects invalid extension
- file download returns 404 for unknown file
- tracking admin route rejects missing token
- memory route requires app token when security is enabled

### 8.8 Why smoke tests matter before Phase 2

Phase 2 adds durable memory. If startup is unreliable before memory is added, debugging becomes harder because failures can look like memory corruption.

Smoke tests create a stable baseline before adding persistence.

---

## 9. Files To Update

### 9.1 Backend files

Likely updates:

- `backend/main.py`
- `backend/config.py`
- `backend/Dockerfile`
- `backend/requirements.txt`
- `backend/tools/web_scraper_tool.py`
- `backend/tools/self_modify_tool.py`
- `backend/tools/mcp_bridge.py`
- `backend/tools/code_execution_tool.py`
- `backend/agents/code_agent.py`
- `backend/tools/research_tool.py`
- `backend/tools/web_search_tool.py`
- `backend/auth/token_store.py`
- `backend/tracking/routes.py`

### 9.2 Frontend files

Likely updates:

- `frontend/package.json`
- `frontend/vite.config.ts`
- `frontend/src/hooks/useJarvisSocket.ts`
- `frontend/src/services/exportService.ts`
- `frontend/src/services/memoryService.ts`
- `frontend/src/App.tsx`

Why frontend changes are needed:

- Add app-token support if enabled.
- Update API services to send token/header.
- Handle backend auth failures.
- Display safer upload errors.
- Possibly update export wording when export no longer wipes memory later.

### 9.3 Root files

Likely updates:

- `docker-compose.yml`
- `.gitignore`
- `README.md`
- `scripts/dev.ps1`

Likely creates:

- `.env.example`
- `backend/pyproject.toml`
- `backend/uv.lock`
- `frontend/Dockerfile`
- optionally `backend/.dockerignore`
- optionally `frontend/.dockerignore`

### 9.4 New backend security files

Likely creates:

- `backend/security/__init__.py`
- `backend/security/app_auth.py`
- `backend/security/uploads.py`
- `backend/security/safe_url.py`
- `backend/security/rate_limit.py`
- `backend/security/tool_policy.py`

### 9.5 New test files

Likely creates:

- `backend/tests/test_health.py`
- `backend/tests/test_upload_security.py`
- `backend/tests/test_safe_url.py`
- `backend/tests/test_tool_policy.py`
- `backend/tests/test_websocket_smoke.py`
- `frontend` smoke or build validation through package scripts

---

## 10. Implementation Plan

### Step 1 - DevOps baseline

Add:

- `backend/pyproject.toml`
- `uv.lock`
- `.env.example`
- backend Dockerfile updates
- frontend Dockerfile
- improved docker-compose
- volumes
- health checks

Success:

```text
docker compose up --build
```

starts Redis, backend, and frontend.

### Step 2 - App auth baseline

Add:

- `JARVIS_APP_TOKEN`
- auth helper
- protected endpoint groups
- WebSocket token check
- frontend token header support

Success:

Sensitive APIs are not open just because the port is reachable.

### Step 3 - File hardening

Add:

- safe upload helper
- upload size limit
- extension allowlist
- UUID storage names
- safer file downloads

Success:

Uploads are controlled and generated files cannot execute inline by accident.

### Step 4 - URL hardening

Add:

- safe URL helper
- SSRF checks
- redirect checks
- shared usage in scraper/research fetchers

Success:

Jarvis cannot scrape localhost, private IPs, metadata endpoints, or Docker internal services.

### Step 5 - Tool safety policy

Add:

- feature flags
- risk classification
- registration gating
- safer disabled-tool responses

Success:

High-risk tools are enabled intentionally, not accidentally.

### Step 6 - Smoke tests

Add:

- backend smoke tests
- safe URL tests
- upload security tests
- Docker smoke commands
- frontend build validation

Success:

There is a repeatable way to know whether Jarvis can start and whether the biggest guardrails are working.

---

## 11. Prioritized Fix List

### P0 - Must fix before Phase 2

- Complete Docker Compose for backend, frontend, Redis.
- Add backend `pyproject.toml` and `uv.lock`.
- Fix backend Dockerfile for `uv`, Playwright, and optional Node/MCP.
- Add `.env.example`.
- Add upload filename/size/extension validation.
- Add SSRF protection for URL-fetching tools.
- Add safer file download headers.
- Add app-token protection for sensitive local APIs and WebSocket.
- Add smoke tests for startup and security basics.

### P1 - Should fix soon

- Feature flags for high-risk tools.
- Disable self-modification by default in Docker.
- Pin MCP filesystem server package.
- Add rate limits.
- Add artifact size/count caps.
- Add Docker health checks.
- Add frontend Dockerfile.
- Add backend non-root user.

### P2 - Later hardening

- Separate artifact preview origin.
- Full audit logging.
- Optional malware scanning for uploads.
- More complete API authorization model.
- CI pipeline.
- Dependency vulnerability scanning.
- Container image scanning.

---

## 12. What Not To Do Yet

Do not start Phase 2 memory persistence before:

- the app can run cleanly from Compose
- upload/file download risks are reduced
- URL fetchers are protected
- high-risk tools are gated
- smoke tests exist

Do not rewrite the whole assistant architecture during Phase 1.5.

Keep this phase focused:

```text
Make Jarvis runnable.
Make Jarvis safer.
Make Jarvis testable.
Then build Phase 2 memory.
```

---

## 13. Final Target Of Phase 1.5

After Phase 1.5, Jarvis should have:

- A reliable Docker Compose development stack.
- A `uv`-based backend environment.
- A frontend service in Compose.
- Redis readiness and backend health checks.
- Clear runtime volumes.
- Clear environment template.
- Safer uploads.
- Safer downloads.
- Safer URL fetching.
- Feature-gated high-risk tools.
- Basic app authentication for powerful endpoints.
- Smoke tests that catch broken setup quickly.

The expected result:

```text
Jarvis remains the same assistant behaviorally,
but the foundation becomes safer, clearer, and easier to run.
```

That is the right base before adding durable memory, cross-chat recall, and the Phase 2 Obsidian-style memory graph.

