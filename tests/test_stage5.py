import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.logging_config import JSONFormatter
from app.main import create_app, create_bot, run_services


def test_json_logging_redacts_secrets_and_exceptions():
    formatter = JSONFormatter(("bot-token", "api-secret"))
    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1, "Failed with %s", ("bot-token",), None
    )
    try:
        raise ValueError("api-secret")
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()
    formatted = formatter.format(record)
    payload = json.loads(formatted)
    assert payload["level"] == "ERROR"
    assert "bot-token" not in formatted and "api-secret" not in formatted
    assert "[REDACTED]" in payload["exception"]


async def test_lifespan_releases_resources(app, bot):
    async with app.router.lifespan_context(app):
        assert app.state.dispatcher.accepting
        bot.add_cog.assert_awaited_once()
        app.state.dispatcher.publish("message", {})
    assert app.state.dispatcher.client.is_closed
    assert app.state.dispatcher.task is None
    assert app.state.voice_manager.closing
    bot.remove_cog.assert_awaited_once_with("EventListener")


async def test_lifespan_startup_failure_cleans_dispatcher(app, bot):
    bot.add_cog.side_effect = RuntimeError("cannot register listener")
    with pytest.raises(RuntimeError, match="cannot register"):
        async with app.router.lifespan_context(app):
            pass
    assert app.state.dispatcher.client.is_closed


async def test_listener_cleanup_failure_still_closes_resources(app, bot):
    bot.remove_cog.side_effect = RuntimeError("listener cleanup failed")
    with pytest.raises(RuntimeError, match="listener cleanup failed"):
        async with app.router.lifespan_context(app):
            pass
    assert app.state.voice_manager.closing
    assert app.state.dispatcher.client.is_closed


async def test_cancellation_stops_both_services():
    started = asyncio.Event()

    async def start(token):
        started.set()
        await asyncio.Event().wait()

    server = MagicMock(should_exit=False)

    async def serve():
        while not server.should_exit:
            await asyncio.sleep(0)

    bot = MagicMock(start=AsyncMock(side_effect=start), close=AsyncMock())
    server.serve = AsyncMock(side_effect=serve)
    task = asyncio.create_task(run_services(bot, server, "token", 1))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    bot.close.assert_awaited_once()
    assert server.should_exit


async def test_shutdown_timeout_leaves_no_service_tasks():
    server = MagicMock(should_exit=False, serve=AsyncMock(side_effect=lambda: None))

    async def stuck_server():
        await asyncio.Event().wait()

    server.serve = AsyncMock(side_effect=stuck_server)
    bot = MagicMock(start=AsyncMock(), close=AsyncMock())
    await run_services(bot, server, "token", 0.01)
    assert not [
        task
        for task in asyncio.all_tasks()
        if task.get_name() in {"discord-gateway", "http-server"}
    ]


async def test_oversized_body_is_rejected(client):
    result = await client.post("/api/v1/channels/1/messages", content=b"x" * 65537)
    assert result.status_code == 413


async def test_oversized_chunked_body_is_rejected(client):
    async def chunks():
        yield b"x" * 40000
        yield b"x" * 40000

    result = await client.post("/api/v1/channels/1/messages", content=chunks())
    assert result.status_code == 413


async def test_schema_is_authenticated(client):
    schema = await client.get("/openapi.json")
    assert schema.status_code == 200
    assert "/api/v1/voice/join" in schema.json()["paths"]
    client.headers.clear()
    assert (await client.get("/openapi.json")).status_code == 401


async def test_real_bot_listener_registration_without_login(settings):
    bot = create_bot(settings)
    app = create_app(settings, bot)
    try:
        async with app.router.lifespan_context(app):
            assert bot.get_cog("EventListener") is not None
        assert bot.get_cog("EventListener") is None
    finally:
        await bot.close()
