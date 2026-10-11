import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import discord
import uvicorn
from discord.ext import commands
from fastapi import APIRouter, Depends, FastAPI

from app.audio.manager import VoiceManager
from app.auth import require_api_key
from app.cogs.event_listener import EventListener
from app.config import Settings
from app.errors import register_error_handlers
from app.event_dispatcher import EventDispatcher
from app.logging_config import configure_logging
from app.middleware import BodyLimitMiddleware
from app.routers import events, guilds, messages, voice

logger = logging.getLogger(__name__)


def create_bot(settings: Settings) -> commands.Bot:
    intents = discord.Intents.default()
    intents.members = settings.members_intent
    intents.message_content = settings.message_content_intent
    return commands.Bot(
        command_prefix=commands.when_mentioned,
        intents=intents,
        allowed_mentions=discord.AllowedMentions.none(),
        max_messages=settings.message_cache_size,
        help_command=None,
    )


def create_app(settings: Settings, bot: commands.Bot) -> FastAPI:
    dispatcher = EventDispatcher(settings)
    voice_manager = VoiceManager(bot, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await dispatcher.start()
        try:
            await bot.add_cog(EventListener(dispatcher, voice_manager))
            yield
        finally:
            try:
                await bot.remove_cog("EventListener")
            finally:
                try:
                    await voice_manager.close()
                finally:
                    await dispatcher.close()

    app = FastAPI(
        title="Discord Adapter",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.bot = bot
    app.state.dispatcher = dispatcher
    app.state.voice_manager = voice_manager
    app.add_middleware(BodyLimitMiddleware)
    register_error_handlers(app)
    rest = APIRouter(dependencies=[Depends(require_api_key)])
    rest.include_router(messages.router)
    rest.include_router(guilds.router)
    rest.include_router(events.router)
    rest.include_router(voice.router)
    app.include_router(events.socket_router)

    @rest.get("/openapi.json", include_in_schema=False)
    async def openapi() -> dict[str, object]:
        return app.openapi()

    @rest.get("/health")
    async def health() -> dict[str, str | bool]:
        return {"status": "ok", "discord_connected": bot.is_ready()}

    app.include_router(rest)
    return app


async def run_services(
    bot: commands.Bot, server: uvicorn.Server, token: str, shutdown_timeout: float
) -> None:
    """Supervise both services on the caller's loop; either exit stops the other."""
    gateway = asyncio.create_task(bot.start(token), name="discord-gateway")
    api = asyncio.create_task(server.serve(), name="http-server")
    try:
        done, _ = await asyncio.wait({gateway, api}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(bot.close(), timeout=shutdown_timeout)
        finally:
            try:
                await asyncio.wait_for(asyncio.shield(api), timeout=shutdown_timeout)
            except TimeoutError:
                logger.warning("HTTP shutdown deadline exceeded")
            finally:
                for task in (gateway, api):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(gateway, api, return_exceptions=True)


async def serve(settings: Settings) -> None:
    bot = create_bot(settings)
    app = create_app(settings, bot)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
            timeout_graceful_shutdown=int(settings.shutdown_timeout),
            access_log=False,
            log_config=None,
            ws_max_size=65536,
        )
    )
    await run_services(
        bot, server, settings.discord_bot_token.get_secret_value(), settings.shutdown_timeout
    )


def main() -> None:
    settings = Settings()
    configure_logging(
        settings.log_level,
        (
            settings.discord_bot_token.get_secret_value(),
            settings.api_secret_key.get_secret_value(),
        ),
    )
    try:
        asyncio.run(serve(settings))
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("Discord adapter stopped after a service failure")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
