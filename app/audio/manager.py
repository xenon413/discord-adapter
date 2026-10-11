import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from weakref import WeakValueDictionary

import discord
from discord.ext import commands
from fastapi import HTTPException

from app.audio.buffer import PCMBuffer
from app.audio.sink import prepare_opus, receive_backend
from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class VoiceSession:
    client: Any
    buffer: PCMBuffer | None
    sink: Any = None


class VoiceManager:
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings
        self.sessions: dict[int, VoiceSession] = {}
        self._locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
        self._pending: set[int] = set()
        self.closing = False

    def _lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[guild_id] = lock
        return lock

    async def join(self, channel: discord.VoiceChannel, receive: bool) -> dict[str, Any]:
        async with self._lock(channel.guild.id):
            if self.closing:
                raise HTTPException(503, "Voice service is shutting down")
            existing = channel.guild.voice_client
            if existing is not None and existing.is_connected():
                session = self.sessions.get(channel.guild.id)
                if (
                    existing.channel.id == channel.id
                    and session is not None
                    and (session.buffer is not None) == receive
                ):
                    return self.status(channel.guild.id)
                raise HTTPException(
                    409, "Already connected; leave before changing channel or receive mode"
                )
            if existing is not None:
                await existing.disconnect(force=True)
            self._forget(channel.guild.id)
            if len(self.sessions) + len(self._pending) >= self.settings.max_voice_sessions:
                raise HTTPException(409, "Voice session capacity reached")
            self._pending.add(channel.guild.id)
            buffer: PCMBuffer | None = None
            sink: Any = None
            client: Any = None
            try:
                backend: type[Any] = discord.VoiceClient
                if receive:
                    buffer = PCMBuffer(
                        self.settings.voice_buffer_bytes, self.settings.voice_max_users
                    )
                    try:
                        backend, sink = receive_backend(buffer)
                    except (ImportError, RuntimeError) as exc:
                        raise HTTPException(
                            503, "Voice receive dependencies are unavailable; install .[voice]"
                        ) from exc
                    if not discord.opus.is_loaded():
                        try:
                            await asyncio.to_thread(prepare_opus)
                        except (discord.opus.OpusNotLoaded, discord.opus.OpusError, OSError) as exc:
                            raise HTTPException(
                                503, "Opus decoder unavailable; install a system libopus library"
                            ) from exc
                client = await channel.connect(
                    cls=backend,
                    timeout=self.settings.voice_connect_timeout,
                    reconnect=True,
                    self_deaf=not receive,
                )
                if receive:
                    client.listen(sink)
                self.sessions[channel.guild.id] = VoiceSession(client, buffer, sink)
                return self.status(channel.guild.id)
            except BaseException:
                if buffer is not None:
                    buffer.close()
                # A failed/cancelled connect may already have registered a voice client.
                failed_client = client or channel.guild.voice_client
                if failed_client is not None:
                    try:
                        await asyncio.wait_for(
                            failed_client.disconnect(force=True),
                            timeout=self.settings.voice_connect_timeout,
                        )
                    except (Exception, asyncio.CancelledError):
                        logger.warning("Failed voice connection cleanup")
                raise
            finally:
                self._pending.discard(channel.guild.id)

    def _forget(self, guild_id: int) -> None:
        session = self.sessions.pop(guild_id, None)
        if session and session.buffer:
            session.buffer.close()

    async def reconcile(self, guild: discord.Guild) -> None:
        async with self._lock(guild.id):
            if guild.voice_client is None or not guild.voice_client.is_connected():
                self._forget(guild.id)

    async def leave(self, guild: discord.Guild) -> None:
        async with self._lock(guild.id):
            session = self.sessions.get(guild.id)
            client = guild.voice_client or (session.client if session else None)
            try:
                if client is not None:
                    await asyncio.wait_for(
                        client.disconnect(force=True), timeout=self.settings.voice_connect_timeout
                    )
            finally:
                self._forget(guild.id)

    def status(self, guild_id: int) -> dict[str, Any]:
        session = self.sessions.get(guild_id)
        if session is None or not session.client.is_connected():
            self._forget(guild_id)
            return {
                "guild_id": str(guild_id),
                "connected": False,
                "receiving": False,
                "channel_id": None,
                "buffered_bytes": {},
                "dropped_bytes": 0,
            }
        buffer = session.buffer
        return {
            "guild_id": str(guild_id),
            "connected": True,
            "channel_id": str(session.client.channel.id),
            "receiving": buffer is not None and not buffer.closed,
            "buffered_bytes": buffer.sizes() if buffer else {},
            "dropped_bytes": buffer.dropped_bytes if buffer else 0,
        }

    async def close(self) -> None:
        self.closing = True

        async def disconnect(session: VoiceSession) -> None:
            try:
                await session.client.disconnect(force=True)
            finally:
                if session.buffer:
                    session.buffer.close()

        tasks = [asyncio.create_task(disconnect(session)) for session in self.sessions.values()]
        try:
            if tasks:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self.settings.shutdown_timeout,
                )
        except TimeoutError:
            logger.warning("Voice shutdown deadline exceeded")
        finally:
            for guild_id in list(self.sessions):
                self._forget(guild_id)
