from typing import Any

import discord
from fastapi import APIRouter, HTTPException, Query

from app.dependencies import Bot, Snowflake, get_message_channel
from app.models import MessageCreate
from app.serialization import message_payload

router = APIRouter(prefix="/api/v1/channels", tags=["messages"])


@router.post("/{channel_id}/messages", status_code=201)
async def send_message(channel_id: Snowflake, body: MessageCreate, bot: Bot) -> dict[str, Any]:
    channel = await get_message_channel(bot, channel_id)
    message = await channel.send(
        content=body.content,
        embeds=[
            discord.Embed.from_dict(embed.model_dump(mode="json", exclude_none=True))
            for embed in body.embeds
        ],
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return message_payload(message)


@router.get("/{channel_id}/history")
async def cached_history(
    channel_id: Snowflake,
    bot: Bot,
    limit: int = Query(default=50, ge=1, le=100),
    before: int | None = Query(default=None, gt=0, le=2**64 - 1),
) -> dict[str, Any]:
    channel = await get_message_channel(bot, channel_id)
    if isinstance(
        channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel)
    ):
        me = channel.guild.me
        if me is None:
            raise HTTPException(503, "Bot member is not cached")
        permissions = channel.permissions_for(me)
        if not permissions.view_channel or not permissions.read_message_history:
            raise HTTPException(403, "Bot cannot read channel history")
    messages = sorted(
        (
            message
            for message in bot.cached_messages
            if message.channel.id == channel_id and (before is None or message.id < before)
        ),
        key=lambda message: message.id,
        reverse=True,
    )
    return {"source": "cache", "messages": [message_payload(m) for m in messages[:limit]]}


@router.get("/{channel_id}")
async def channel_metadata(channel_id: Snowflake, bot: Bot) -> dict[str, Any]:
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    guild = getattr(channel, "guild", None)
    return {
        "id": str(channel.id),
        "name": getattr(channel, "name", None),
        "type": str(channel.type),
        "guild_id": str(guild.id) if guild else None,
    }
