from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from discord.ext import commands
from fastapi import FastAPI

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None, discord_bot_token="test-token", api_secret_key="test-secret-key-long"
    )


@pytest.fixture
def bot() -> MagicMock:
    bot = MagicMock(spec=commands.Bot)
    bot.is_ready.return_value = True
    bot.close = AsyncMock()
    bot.cached_messages = []
    return bot


@pytest.fixture
def app(settings: Settings, bot: MagicMock) -> FastAPI:
    return create_app(settings, bot)


@pytest.fixture
async def client(app: FastAPI, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": settings.api_secret_key.get_secret_value()},
    ) as client:
        yield client
