from typing import Any

import discord


def message_payload(message: discord.Message) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "channel_id": str(message.channel.id),
        "guild_id": str(message.guild.id) if message.guild else None,
        "author": {
            "id": str(message.author.id),
            "name": message.author.name,
            "bot": message.author.bot,
        },
        "content": message.content,
        "embeds": [embed.to_dict() for embed in message.embeds],
        "attachments": [
            {"id": str(item.id), "filename": item.filename, "url": item.url}
            for item in message.attachments
        ],
        "created_at": message.created_at.isoformat(),
    }


def member_payload(member: discord.Member) -> dict[str, Any]:
    return {
        "id": str(member.id),
        "name": member.name,
        "display_name": member.display_name,
        "bot": member.bot,
        "role_ids": [str(role.id) for role in member.roles],
    }
