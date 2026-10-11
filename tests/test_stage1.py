import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import create_bot, run_services


def test_settings_from_environment(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "environment-token")
    monkeypatch.setenv("API_SECRET_KEY", "environment-secret-long")
    settings = Settings(_env_file=None)
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.discord_bot_token.get_secret_value() == "environment-token"
    assert "environment-token" not in repr(settings)


@pytest.mark.parametrize(
    "changes", [{"port": 0}, {"api_secret_key": "short"}, {"discord_bot_token": ""}]
)
def test_invalid_settings(settings, changes):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **(settings.model_dump() | changes))


async def test_health_tracks_gateway(client, bot):
    assert (await client.get("/health")).json() == {"status": "ok", "discord_connected": True}
    bot.is_ready.return_value = False
    assert (await client.get("/health")).json()["discord_connected"] is False


async def test_health_authentication(client):
    client.headers.clear()
    assert (await client.get("/health")).status_code == 401
    assert (await client.get("/health", headers={"X-API-Key": "wrong"})).status_code == 401
    assert (await client.get("/docs")).status_code == 404


async def test_bot_configuration(settings):
    bot = create_bot(settings)
    assert bot.intents.members and bot.intents.message_content
    assert not bot.allowed_mentions.everyone
    await bot.close()


async def test_services_share_loop_and_shutdown():
    loops = []
    started = asyncio.Event()
    closed = asyncio.Event()
    bot = MagicMock()
    server = MagicMock(should_exit=False)

    async def start(token):
        assert token == "token"
        loops.append(asyncio.get_running_loop())
        started.set()
        await closed.wait()

    async def close():
        closed.set()

    async def serve():
        loops.append(asyncio.get_running_loop())
        await started.wait()

    bot.start = AsyncMock(side_effect=start)
    bot.close = AsyncMock(side_effect=close)
    server.serve = AsyncMock(side_effect=serve)
    await run_services(bot, server, "token", 1)
    assert loops == [asyncio.get_running_loop()] * 2
    bot.close.assert_awaited_once()
    assert server.should_exit


async def test_gateway_failure_stops_server():
    bot = MagicMock(start=AsyncMock(side_effect=RuntimeError("login failed")), close=AsyncMock())
    server = MagicMock(should_exit=False)

    async def serve():
        while not server.should_exit:
            await asyncio.sleep(0)

    server.serve = AsyncMock(side_effect=serve)
    with pytest.raises(RuntimeError, match="login failed"):
        await run_services(bot, server, "token", 1)
    bot.close.assert_awaited_once()
