from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.dependencies import Bot, Snowflake, get_guild
from app.models import RoleAssignment
from app.serialization import member_payload

router = APIRouter(prefix="/api/v1/guilds", tags=["guilds"])


@router.get("/{guild_id}")
async def guild_metadata(guild_id: Snowflake, bot: Bot) -> dict[str, Any]:
    guild = get_guild(bot, guild_id)
    return {
        "id": str(guild.id),
        "name": guild.name,
        "member_count": guild.member_count,
        "members_cached": len(guild.members),
        "cache_complete": guild.chunked,
    }


@router.get("/{guild_id}/members")
async def cached_members(
    guild_id: Snowflake,
    bot: Bot,
    limit: int = Query(default=100, ge=1, le=1000),
    after: int = Query(default=0, ge=0, le=2**64 - 1),
) -> dict[str, Any]:
    guild = get_guild(bot, guild_id)
    members = sorted((m for m in guild.members if m.id > after), key=lambda m: m.id)
    page = members[:limit]
    return {
        "source": "cache",
        "cache_complete": guild.chunked,
        "members": [member_payload(m) for m in page],
        "next_after": str(page[-1].id) if len(members) > limit else None,
    }


@router.post("/{guild_id}/roles/assign")
async def assign_roles(guild_id: Snowflake, body: RoleAssignment, bot: Bot) -> dict[str, Any]:
    guild = get_guild(bot, guild_id)
    me = guild.me
    if me is None:
        raise HTTPException(503, "Bot member is not cached")
    if not me.guild_permissions.manage_roles:
        raise HTTPException(403, "Bot lacks Manage Roles permission")
    member = guild.get_member(body.member_id)
    if member is None:
        member = await guild.fetch_member(body.member_id)
    if member.id == guild.owner_id or member.top_role >= me.top_role:
        raise HTTPException(403, "Target member is above the bot in the role hierarchy")
    roles = []
    for role_id in body.role_ids:
        role = guild.get_role(role_id)
        if role is None:
            raise HTTPException(404, "Role is not in this guild")
        if not role.is_assignable():
            raise HTTPException(403, "Role is managed, default, or above the bot's role")
        roles.append(role)
    await member.add_roles(*roles, reason="Local Discord adapter API", atomic=True)
    return {"member_id": str(member.id), "role_ids": [str(role.id) for role in roles]}
