from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


@pytest.fixture
def message():
    return SimpleNamespace(
        id=123,
        channel=SimpleNamespace(id=10),
        guild=SimpleNamespace(id=20),
        author=SimpleNamespace(id=30, name="person", bot=False),
        content="hello",
        embeds=[],
        attachments=[],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def channel(bot, message):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 10
    channel.name = "general"
    channel.type = discord.ChannelType.text
    channel.guild = MagicMock(id=20)
    channel.send = AsyncMock(return_value=message)
    channel.permissions_for.return_value = discord.Permissions.all()
    bot.get_channel.return_value = channel
    return channel


async def test_send_message_and_mentions(client, channel, mocker):
    spy = mocker.spy(channel, "send")
    response = await client.post(
        "/api/v1/channels/10/messages", json={"content": "hello @everyone"}
    )
    assert response.status_code == 201
    assert response.json()["id"] == "123"
    assert spy.call_args.kwargs["allowed_mentions"].to_dict() == {"parse": []}


async def test_fetch_channel_and_embed(client, channel, bot):
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(return_value=channel)
    response = await client.post(
        "/api/v1/channels/10/messages", json={"embeds": [{"title": "News"}]}
    )
    assert response.status_code == 201
    bot.fetch_channel.assert_awaited_once_with(10)
    assert channel.send.call_args.kwargs["embeds"][0].title == "News"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"content": " "},
        {"content": "x" * 2001},
        {"embeds": [{}]},
        {"embeds": [{"description": "x" * 4000}] * 2},
        {"embeds": [{"color": -1, "title": "bad"}]},
        {"content": "ok", "tts": True},
    ],
)
async def test_invalid_message(client, payload, channel):
    assert (await client.post("/api/v1/channels/10/messages", json=payload)).status_code == 422
    channel.send.assert_not_awaited()


@pytest.mark.parametrize(
    "exception,status",
    [
        (discord.NotFound, 404),
        (discord.Forbidden, 403),
        (discord.HTTPException, 502),
    ],
)
async def test_discord_errors(client, channel, exception, status):
    response = SimpleNamespace(status=status, reason="failure")
    channel.send.side_effect = exception(response, "sensitive upstream body")
    result = await client.post("/api/v1/channels/10/messages", json={"content": "hello"})
    assert result.status_code == status
    assert "sensitive" not in result.text


async def test_history_is_filtered_cache(client, channel, bot, message):
    bot.cached_messages = [message, SimpleNamespace(id=999, channel=SimpleNamespace(id=11))]
    response = await client.get("/api/v1/channels/10/history")
    assert response.json()["source"] == "cache"
    assert [m["id"] for m in response.json()["messages"]] == ["123"]
    assert (await client.get("/api/v1/channels/10/history?before=123")).json()["messages"] == []
    assert (await client.get("/api/v1/channels/10/history?limit=101")).status_code == 422
    channel.permissions_for.return_value = discord.Permissions.none()
    assert (await client.get("/api/v1/channels/10/history")).status_code == 403


async def test_channel_type_and_readiness(client, bot):
    bot.get_channel.return_value = MagicMock(spec=discord.CategoryChannel)
    assert (await client.get("/api/v1/channels/10/history")).status_code == 400
    bot.is_ready.return_value = False
    assert (await client.get("/api/v1/guilds/20/members")).status_code == 503


@pytest.fixture
def guild(bot):
    guild = MagicMock(id=20)
    guild.name = "Guild"
    guild.owner_id = 99
    guild.me.top_role = 10
    member = MagicMock(id=30, display_name="Person", bot=False, roles=[], top_role=1)
    member.name = "person"
    member.add_roles = AsyncMock()
    guild.get_member.return_value = member
    guild.members = [member]
    guild.chunked = True
    guild.member_count = 1
    role = MagicMock(id=5)
    role.is_assignable.return_value = True
    guild.get_role.return_value = role
    bot.get_guild.return_value = guild
    return guild


async def test_members_and_metadata(client, guild, channel):
    result = (await client.get("/api/v1/guilds/20/members")).json()
    assert result["members"][0]["id"] == "30"
    assert result["cache_complete"] is True
    assert (await client.get("/api/v1/guilds/20/members?after=30")).json()["members"] == []
    assert (await client.get("/api/v1/guilds/20")).json()["members_cached"] == 1
    assert (await client.get("/api/v1/channels/10")).json()["id"] == "10"


async def test_role_assignment(client, guild):
    response = await client.post(
        "/api/v1/guilds/20/roles/assign", json={"member_id": 30, "role_ids": [5]}
    )
    assert response.status_code == 200
    guild.get_member.return_value.add_roles.assert_awaited_once_with(
        guild.get_role.return_value,
        reason="Local Discord adapter API",
        atomic=True,
    )


@pytest.mark.parametrize(
    "case,status", [("permission", 403), ("role", 403), ("missing", 404), ("hierarchy", 403)]
)
async def test_role_safety(client, guild, case, status):
    if case == "permission":
        guild.me.guild_permissions.manage_roles = False
    elif case == "role":
        guild.get_role.return_value.is_assignable.return_value = False
    elif case == "missing":
        guild.get_role.return_value = None
    else:
        guild.get_member.return_value.top_role = 20
    response = await client.post(
        "/api/v1/guilds/20/roles/assign", json={"member_id": 30, "role_ids": [5]}
    )
    assert response.status_code == status
    guild.get_member.return_value.add_roles.assert_not_awaited()


async def test_missing_guild(client, bot):
    bot.get_guild.return_value = None
    assert (await client.get("/api/v1/guilds/20/members")).status_code == 404


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/v1/channels/10/history", None),
        ("POST", "/api/v1/channels/10/messages", {"content": "hello"}),
        ("GET", "/api/v1/guilds/20/members", None),
        ("POST", "/api/v1/guilds/20/roles/assign", {"member_id": 30, "role_ids": [5]}),
        ("GET", "/openapi.json", None),
    ],
)
async def test_routes_require_auth(client, method, path, body):
    client.headers.clear()
    assert (await client.request(method, path, json=body)).status_code == 401
