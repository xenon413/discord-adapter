# discord-adapter

A local Discord gateway sidecar built with Python 3.11+, discord.py, FastAPI, and
httpx. One process maintains one Discord Gateway connection and exposes an
authenticated REST API, local webhooks, and a WebSocket event stream. Optional
voice reception buffers PCM audio for an external consumer.

## Architecture

```text
Discord Gateway <--> discord.py Bot ----- REST control <----- local application
                         |
                    event listeners
                         |
                   bounded event queue
                         |
                  event dispatcher
                   /            \
       per-webhook queues    WebSocket queues
              |                    |
       local HTTP receivers    local clients

Discord voice --> optional AudioSink --> bounded PCM buffers --> audio REST reads
```

FastAPI and the bot share one native asyncio loop. HTTP sends, retries, and
Discord operations are asynchronous. Voice callbacks run on the receive
extension's audio thread and access a locked buffer; they never run asyncio
operations. Native Opus initialization runs through `asyncio.to_thread`.

`app/main.py` supervises the API and Gateway together. Either service exiting
stops the other. FastAPI lifespan starts and drains the dispatcher and closes
voice sessions. SIGTERM/Ctrl+C initiates bounded shutdown and closes the bot,
HTTP client, and worker tasks. Run `python -m app.main`, using **one process**;
do not run multiple Uvicorn workers or enable reload against a live bot token.

## Setup

Create a Discord application and bot, invite it to your server, and enable the
**Server Members Intent** and **Message Content Intent** in its Developer Portal
settings. These intents are enabled by default in this adapter. Set
`MEMBERS_INTENT=false` or `MESSAGE_CONTENT_INTENT=false` if they are not needed;
member caches or message content will then be limited.

Give the bot the permissions needed for your use: View Channels, Send Messages,
Embed Links, Read Message History, Manage Roles, and Connect. For role assignment,
place its highest role above both the target role and target member. Administrator
permission is not required.

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Set DISCORD_BOT_TOKEN and API_SECRET_KEY in .env, then:
python -m app.main
```

On Linux/macOS, activate using `source .venv/bin/activate` and copy with
`cp .env.example .env`. Python must be installed and available on your PATH.
Keep `.env` local; it is excluded from Git and Docker build contexts.

The service defaults to `127.0.0.1:8000`. Every REST route, including `/health`
and `/openapi.json`, requires `X-API-Key`. WebSockets require the same handshake
header. There is no query-string authentication or public Swagger UI.
The key grants control of the bot; this is a trusted local API.

## Configuration

Environment variables override `.env`. Unknown `.env` keys are ignored.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | required | Bot credential; never printed by adapter logs |
| `API_SECRET_KEY` | required | Shared API key, at least 16 characters |
| `HOST`, `PORT` | `127.0.0.1`, `8000` | HTTP bind address and port |
| `LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR, or CRITICAL |
| `MEMBERS_INTENT` | `true` | Populate guild member caches |
| `MESSAGE_CONTENT_INTENT` | `true` | Receive message content |
| `MESSAGE_CACHE_SIZE` | `1000` | Maximum cached messages across all channels |
| `SHUTDOWN_TIMEOUT` | `15` | Seconds per bounded shutdown operation |
| `EVENT_QUEUE_SIZE` | `1000` | Shared outbound event queue capacity |
| `SUBSCRIBER_QUEUE_SIZE` | `100` | Capacity per webhook or WebSocket |
| `MAX_SUBSCRIBERS` | `32` | Combined webhook and WebSocket limit |
| `WEBHOOK_TIMEOUT` | `5` | HTTP operation timeout in seconds |
| `WEBHOOK_ATTEMPTS` | `3` | Maximum delivery attempts |
| `WEBHOOK_RETRY_DELAY` | `0.5` | Initial exponential retry delay, capped at 10s |
| `VOICE_ENABLED` | `false` | Enable optional voice routes |
| `VOICE_CONNECT_TIMEOUT` | `20` | Voice connect/disconnect timeout in seconds |
| `MAX_VOICE_SESSIONS` | `8` | Maximum concurrent guild voice sessions |
| `VOICE_MAX_USERS` | `16` | Maximum buffered users per guild |
| `VOICE_BUFFER_BYTES` | `384000` | Buffer per user; stereo frame multiple of four |

Logs are JSON with UTC timestamps. The bot token and API key are redacted from
formatted messages and exceptions. HTTP access logging is disabled. Request
bodies are limited to 64 KiB, including chunked requests.

## REST API

Discord IDs are returned as strings to preserve precision in JavaScript. Request
IDs accept positive decimal integers or numeric strings, up to 64 bits.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/health` | `{"status":"ok","discord_connected":true}`; connectivity can be false |
| GET | `/openapi.json` | Authenticated API schema |
| GET | `/api/v1/channels/{channel_id}` | Channel metadata |
| POST | `/api/v1/channels/{channel_id}/messages` | Send text and/or embeds; returns 201 |
| GET | `/api/v1/channels/{channel_id}/history` | Cached messages, newest first; `limit=1..100`, optional `before` |
| GET | `/api/v1/guilds/{guild_id}` | Guild metadata and cache completeness |
| GET | `/api/v1/guilds/{guild_id}/members` | Cached members ordered by ID; `limit=1..1000`, optional `after` |
| POST | `/api/v1/guilds/{guild_id}/roles/assign` | `{"member_id":"123","role_ids":["456"]}` |
| POST | `/api/v1/webhooks/subscribe` | Register URL and optional event selection; returns 201 |
| GET | `/api/v1/webhooks` | List subscriptions |
| DELETE | `/api/v1/webhooks/{subscriber_id}` | Remove subscription; returns 204 |
| GET | `/api/v1/events/status` | Queue sizes, subscriber counts, delivery/drop counters |
| POST | `/api/v1/voice/join` | `{"channel_id":"123","receive":true}` |
| POST | `/api/v1/voice/leave` | `{"guild_id":"456"}`; returns 204 |
| GET | `/api/v1/voice/{guild_id}/status` | Connection and buffer state |
| GET | `/api/v1/voice/{guild_id}/audio/{user_id}` | Drain that user's buffered PCM |

Health is a liveness endpoint: HTTP 200 does not imply Discord readiness. Discord
control routes return 503 until the Gateway is ready. Subscription routes can
be used while it connects. Discord errors map to 404 (not found), 403
(forbidden), or 502 (other HTTP errors). Invalid input returns 422; unsupported
channels return 400; voice conflicts return 409 and voice timeouts return 504.

History is the bot's **in-memory cache**, not a Discord history fetch. It can be
empty after startup and is filtered to the requested channel; current read
permissions are checked. Member responses include `cache_complete` and
`next_after`; no full-server fetch is implied. Guild cache lookups return 404
when the bot has no cached guild. Message sends and channel metadata lookups
fall back to Discord's REST API on channel cache misses.

All outgoing mentions are disabled, including `@everyone`, users, and roles.
Messages support up to 2,000 characters and ten embeds with Discord's field and
combined 6,000-character text limits. Embed inputs support title, description,
URL, color, fields, footer, author, image, and thumbnail; unknown fields are
rejected. Role requests validate all requested roles before writing, reject
default/managed or unassignable roles, and enforce the bot's permissions and
hierarchy. Discord applies multiple role additions individually; an upstream
failure can leave some roles assigned. Retrying the same additions is safe.

PowerShell examples (set the values for your bot):

```powershell
$apiKey = 'your-api-secret'
$headers = @{ 'X-API-Key' = $apiKey }
$base = 'http://127.0.0.1:8000'
Invoke-RestMethod "$base/health" -Headers $headers

$channelId = '123456789012345678'
$body = @{ content = 'Hello from the adapter'; embeds = @(@{ title = 'Status'; description = 'Ready' }) } | ConvertTo-Json -Depth 5
Invoke-RestMethod "$base/api/v1/channels/$channelId/messages" -Method Post -Headers $headers -ContentType 'application/json' -Body $body

$subscription = @{ url = 'http://127.0.0.1:9000/events'; events = @('message', 'member_join') } | ConvertTo-Json
Invoke-RestMethod "$base/api/v1/webhooks/subscribe" -Method Post -Headers $headers -ContentType 'application/json' -Body $subscription
```

## Events and state

The supported event types are `message`, `member_join`, and `voice_state_update`.
All events use this envelope:

```json
{
  "id": "unique-event-uuid",
  "type": "message",
  "timestamp": "2026-01-01T00:00:00+00:00",
  "data": {"id": "123", "channel_id": "456", "content": "hello"}
}
```

Message data also includes author, guild ID (null for DMs), embeds, attachments,
and creation time. Member events contain `guild_id` and `member`; voice events
contain guild/member IDs and `before`/`after` channel and mute/deafen state.
Messages from bots, including this bot, are included; consumers that send replies
should filter the author to avoid feedback loops.

Webhook URLs must use HTTP(S) and a numeric loopback IP or `localhost`, which is
pinned to `127.0.0.1`. Remote/private-network hosts, credentials, and fragments
are rejected. Redirects and environment HTTP proxies are disabled. Receivers
must return a 2xx response. Network failures, 429s, and 5xx responses retry with
bounded exponential delay; other statuses fail immediately. Response bodies
are not buffered. Each delivery includes `X-Event-ID` for consumer deduplication;
the API secret and bot token are never attached to webhook deliveries.

Registering the same normalized URL updates its event filter and reuses its ID.
Subscriptions, cached data, pending events, and voice buffers are process-local
and disappear on restart. Re-register subscriptions after a restart. The queue
is best-effort: a full queue drops new events, and shutdown drops undelivered
work after the deadline. Retries can produce duplicates. Order is maintained
per webhook; there is no durable replay, exactly-once guarantee, or WebSocket
acknowledgement protocol. Removing a webhook cancels pending delivery, but a
receiver may already have accepted an in-flight request.

The status counters count accepted events (`published`), overflow/not-running
drops (`dropped`), and successful/exhausted webhook deliveries (`delivered`,
`failed`). A single event can have multiple deliveries or drops. Shutdown and
unsubscribe discards are not included in the overflow counter.

WebSocket consumers connect to `/api/v1/events/ws` with `X-API-Key` in the
handshake headers. This interface is intended for local application clients;
browser WebSocket APIs cannot set that header. An aiohttp example:

```python
import asyncio
import os
import aiohttp

async def consume():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            'http://127.0.0.1:8000/api/v1/events/ws',
            headers={'X-API-Key': os.environ['API_SECRET_KEY']},
        ) as socket:
            async for message in socket:
                if message.type == aiohttp.WSMsgType.TEXT:
                    print(message.json())

asyncio.run(consume())
```

## Optional voice reception

Install `python -m pip install -e '.[dev,voice]'`, make a system Opus library
available (Debian/Ubuntu: `libopus0`), and set `VOICE_ENABLED=true`. Join requests
default to receiving; use `receive=false` for a deafened connection without a
receiver. A repeated join to the same channel and receive mode is idempotent;
leave before changing channels or mode. Only regular guild voice channels are
accepted. External disconnection clears session buffers.

Audio reads drain the buffer and return raw signed 16-bit little-endian PCM,
48,000 Hz, stereo. Format/rate/channel headers describe the bytes; an empty
buffer returns an empty 200 response. At the defaults, each user's buffer holds
two seconds. Overflow discards the oldest frames. When the user limit is reached,
new users' frames are dropped until a consumer drains an existing user's buffer.
Audio is not written to disk. This is a capture interface, not speech recognition;
run transcription in a separate consumer or offload expensive processing.

Voice reception depends on the experimental
[discord-ext-voice-recv extension](https://github.com/imayhaveborkedit/discord-ext-voice-recv).
Its compatibility with Discord's voice protocol and encryption must be checked
with the library versions used in your deployment. Mocked tests do not certify
live voice reception. The core service does not import the extension until a
receive connection is requested. See also the
[discord.py API reference](https://discordpy.readthedocs.io/en/stable/api.html).

## Docker

With credentials in `.env`:

```sh
docker compose up --build -d
docker compose logs -f adapter
docker compose down
```

The multi-stage build installs wheels, runs as UID/GID 10001, and includes Opus.
Compose publishes only host loopback, drops capabilities, uses a read-only root
filesystem, and allows 75 seconds for shutdown. Its health check requires Discord
readiness. Set `VOICE_ENABLED=true` before building to include the receive extra;
changing voice dependency installation requires rebuilding the image.

Inside Docker, webhook loopback addresses refer to the **container's network
namespace**, not your host. Use the WebSocket stream for host consumers, or run
your webhook receiver in the same namespace (for example, a Compose service
with `network_mode: service:adapter`). `host.docker.internal` and other container
hostnames are deliberately not accepted by the loopback-only registry. Docker
must allow outbound HTTPS/WebSocket traffic, and UDP when using voice.

Compose exposes common settings explicitly. Add any further configuration keys
from the table to its `environment` section when tuning container deployments.

## Tests and CI

Tests mock Discord and use httpx ASGI/MockTransport clients; no credentials or
Discord network access are required. Install development dependencies first.
Run stages in order and stop on any failure:

```sh
python -m pytest tests/test_stage1.py -v
python -m pytest tests/test_stage2.py -v
python -m pytest tests/test_stage3.py -v
python -m pytest tests/test_stage4.py -v
python -m pytest --cov=app --cov-report=term-missing tests/
python -m ruff check app tests
```

Stage 1 covers settings, authentication, shared-loop service supervision, and
health. Stage 2 covers REST validation, cache behavior, error mapping, and role
safety. Stage 3 covers events, retries, destination restrictions, queue bounds,
WebSockets, and drain timeouts. Stage 4 covers voice state and PCM handling;
the real sink contract test is skipped without the optional extension. Stage 5
covers lifecycle cleanup, cancellation, logging/redaction, and request limits.

GitHub Actions runs the stage gates and coverage on Python 3.11–3.13, separately
tests the optional voice extra on Python 3.12, and builds the Docker image.
Live Discord permissions, privileged intents, and voice compatibility still
require a deployment smoke test with your own bot.
