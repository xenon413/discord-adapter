from typing import Annotated, Any

import discord
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.audio.manager import VoiceManager
from app.dependencies import Bot, Snowflake, get_guild
from app.models import Id, InputModel

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])


def get_voice_manager(request: Request) -> VoiceManager:
    if not request.app.state.settings.voice_enabled:
        raise HTTPException(503, "Voice is disabled; set VOICE_ENABLED=true and install .[voice]")
    return request.app.state.voice_manager


Manager = Annotated[VoiceManager, Depends(get_voice_manager)]


class JoinVoice(InputModel):
    channel_id: Id
    receive: bool = True


class LeaveVoice(InputModel):
    guild_id: Id


@router.post("/join")
async def join(body: JoinVoice, bot: Bot, manager: Manager) -> dict[str, Any]:
    channel = bot.get_channel(body.channel_id) or await bot.fetch_channel(body.channel_id)
    if not isinstance(channel, discord.VoiceChannel):
        raise HTTPException(400, "A guild voice channel is required")
    me = channel.guild.me
    if me is None:
        raise HTTPException(503, "Bot member is not cached")
    permissions = channel.permissions_for(me)
    if not permissions.view_channel or not permissions.connect:
        raise HTTPException(403, "Bot cannot view or connect to this voice channel")
    try:
        return await manager.join(channel, body.receive)
    except TimeoutError as exc:
        raise HTTPException(504, "Voice connection timed out") from exc
    except RuntimeError as exc:
        raise HTTPException(
            503, "Voice backend unavailable; check voice dependencies and Opus"
        ) from exc


@router.post("/leave", status_code=204)
async def leave(body: LeaveVoice, bot: Bot, manager: Manager) -> None:
    try:
        await manager.leave(get_guild(bot, body.guild_id))
    except TimeoutError as exc:
        raise HTTPException(504, "Voice disconnect timed out") from exc


@router.get("/{guild_id}/status")
async def status(guild_id: Snowflake, bot: Bot, manager: Manager) -> dict[str, Any]:
    guild = get_guild(bot, guild_id)
    await manager.reconcile(guild)
    return manager.status(guild_id)


@router.get("/{guild_id}/audio/{user_id}")
async def take_audio(
    guild_id: Snowflake, user_id: Snowflake, bot: Bot, manager: Manager
) -> Response:
    await manager.reconcile(get_guild(bot, guild_id))
    session = manager.sessions.get(guild_id)
    if session is None or session.buffer is None or session.buffer.closed:
        raise HTTPException(409, "No active voice receiver for this guild")
    return Response(
        session.buffer.take(user_id),
        media_type="application/octet-stream",
        headers={
            "X-Audio-Format": "s16le",
            "X-Audio-Rate": "48000",
            "X-Audio-Channels": "2",
            "Cache-Control": "no-store",
        },
    )
