# TASK: Build a Production-Ready Discord-to-REST/WebSocket Gateway Sidecar

## System Architecture Overview
Build a local async adapter service using Python 3.11+, `discord.py`, `FastAPI`, `uvicorn`, and `httpx`.
1. **Discord Gateway (Inbound)**: Maintain 1 persistent `discord.py` WebSocket connection.
2. **REST API (Outbound Control)**: Expose local FastAPI endpoints (`APIRouter`) allowing external apps to control the bot via HTTP POST/GET.
3. **Event Dispatcher (Outbound Push)**: Forward live Gateway events (like `on_message` or voice status) to registered local subscribers via HTTP Webhooks or local WebSockets.
4. **Voice Audio Recv (Optional Sink)**: Modular hook using `discord-ext-voice-recv` or PCM AudioSink for receiving/transcribing voice streams.

---

## Technical & Production Requirements
- **Frameworks**: FastAPI, discord.py, uvicorn, pydantic (v2), httpx, pytest, pytest-asyncio, pytest-mock.
- **Async Safety**: Run FastAPI and discord.py on the **same native `asyncio` event loop**. Never block the event loop (`asyncio.to_thread` for heavy work).
- **Security**: API key authentication header (`X-API-Key`) on all REST routes; bind by default to `127.0.0.1`.
- **Error Handling**: Graceful error mapping for `discord.NotFound`, `discord.Forbidden`, and `discord.HTTPException` returning valid HTTP status codes (`404`, `403`, `502`).
- **Code Quality**: Strict type hints, modular structure, logging via standard Python `logging`, and clean shutdown handlers (`lifespan` / `on_shutdown`).

---

## EXECUTION PLAN & TESTING STAGES

Execute this project sequentially. **DO NOT proceed to the next stage until all pytest unit/integration tests for the current stage pass 100%.**

---

### STAGE 1: Project Skeleton, Shared Event Loop, & Core Health Checks
**Goal**: Set up project structure and run FastAPI + `discord.py` concurrently on a single `asyncio` event loop.

#### Deliverables:
1. `config.py`: Pydantic `BaseSettings` for environment variables (`DISCORD_BOT_TOKEN`, `API_SECRET_KEY`, `HOST`, `PORT`, `LOG_LEVEL`).
2. `main.py`: Entrypoint assembling FastAPI app and `discord.py` Client/Bot using `uvicorn.Server` and `asyncio.gather`.
3. `/health` route returning `{"status": "ok", "discord_connected": bool}`.
4. `tests/test_stage1.py`: Tests for settings loading, health endpoint, and event loop co-existence.

#### Pytest Verification Stage 1:
```bash
pytest tests/test_stage1.py -v
```

---

### STAGE 2: Control Endpoints (Channels, Messaging & Guilds)
**Goal**: Allow external apps to send messages, embeds, and fetch server/channel metadata via REST.

#### Deliverables:
1. `routers/messages.py`:
   - `POST /api/v1/channels/{channel_id}/messages`: Send text or JSON embeds.
   - `GET /api/v1/channels/{channel_id}/history`: Fetch recent message cache.
2. `routers/guilds.py`:
   - `GET /api/v1/guilds/{guild_id}/members`: List cached members.
   - `POST /api/v1/guilds/{guild_id}/roles/assign`: Assign roles to members.
3. Middleware / Dependency: API key check (`X-API-Key`).
4. Exception handlers: Map `discord.NotFound` -> `404`, `discord.Forbidden` -> `403`.
5. `tests/test_stage2.py`: Use `pytest-mock` to mock `discord.py` bot methods (`get_channel`, `fetch_channel`, `send`) and verify HTTP responses, error handling, and auth checks.

#### Pytest Verification Stage 2:
```bash
pytest tests/test_stage2.py -v
```

---

### STAGE 3: Outbound Event Forwarding (Webhooks & Event Bus)
**Goal**: Capture live Discord events (`on_message`, `on_member_join`, `on_voice_state_update`) and forward them to external local webhooks.

#### Deliverables:
1. `event_dispatcher.py`:
   - Webhook registry managing subscriber URLs (`POST /api/v1/webhooks/subscribe`).
   - Async HTTP client (`httpx.AsyncClient`) with retry logic and non-blocking background queue (`asyncio.Queue`).
2. `cogs/event_listener.py`:
   - `discord.ext.commands.Cog` listening for `on_message`, formatting JSON payloads, and enqueueing them to the event dispatcher.
3. `tests/test_stage3.py`: Mock Gateway events, trigger `on_message`, and test that subscriber endpoints receive the correct JSON HTTP POST payload.

#### Pytest Verification Stage 3:
```bash
pytest tests/test_stage3.py -v
```

---

### STAGE 4: Voice Receiving & Audio Stream Processing (Optional Module)
**Goal**: Enable connecting to voice channels and processing incoming audio.

#### Deliverables:
1. `routers/voice.py`:
   - `POST /api/v1/voice/join`: Connect bot to a voice channel.
   - `POST /api/v1/voice/leave`: Disconnect bot from voice channel.
2. `audio/sink.py`:
   - Audio sink integration (e.g., using `discord-ext-voice-recv` or custom PCM sink).
   - Convert incoming voice frames into a stream buffer or dispatch events when speech is detected.
3. `tests/test_stage4.py`: Mock voice client connections, joining/leaving channels, and test PCM buffer handling.

#### Pytest Verification Stage 4:
```bash
pytest tests/test_stage4.py -v
```

---

### STAGE 5: Production Readiness, Logging, Docker & CI Setup
**Goal**: Prepare the sidecar for production deployment.

#### Deliverables:
1. `Dockerfile` & `docker-compose.yml`: Non-root execution, optimized Python dependencies.
2. Structured JSON logging (`structlog` or standard `logging` with JSON formatter).
3. Graceful shutdown handler: Close Discord WebSocket session, drain `httpx` connection pool, and exit cleanly on SIGTERM.
4. Comprehensive test execution suite with coverage report setup (`pytest-cov`).

#### Pytest Verification Stage 5:
```bash
pytest --cov=app --cov-report=term-missing tests/
```

---

## Instructions for Codex:
Start with **STAGE 1**. Generate all project files, configuration, main entrypoint, and corresponding `pytest` files. Stop and wait for confirmation before moving to Stage 2.