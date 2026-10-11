from typing import Annotated

import discord
from discord.ext import commands
from fastapi import Depends, HTTPException, Path, Request

Snowflake = Annotated[int, Path(gt=0, le=2**64 - 1)]


def get_bot(request: Request) -> commands.Bot:
    bot: commands.Bot = request.app.state.bot
    if not bot.is_ready():
        raise HTTPException(503, "Discord gateway is not ready")
    return bot


Bot = Annotated[commands.Bot, Depends(get_bot)]


async def get_message_channel(bot: commands.Bot, channel_id: int) -> discord.abc.Messageable:
    channel = bot.get_channel(channel_id)
    if channel is None:
        channel = await bot.fetch_channel(channel_id)
    if not isinstance(channel, discord.abc.Messageable):
        raise HTTPException(400, "Channel does not support messages")
    return channel


def get_guild(bot: commands.Bot, guild_id: int) -> discord.Guild:
    guild = bot.get_guild(guild_id)
    if guild is None:
        raise HTTPException(404, "Guild is not in the bot cache")
    return guild
