import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.audio.buffer import PCMBuffer


def test_pcm_buffer_bounds_and_drain():
    buffer = PCMBuffer(max_bytes=8, max_users=1)
    buffer.write(1, b"aaaa")
    buffer.write(1, b"bbbbcccc")
    assert buffer.sizes() == {"1": 8}
    buffer.write(2, b"dddd")
    assert buffer.dropped_bytes == 8
    assert buffer.take(1) == b"bbbbcccc"
    assert buffer.take(1) == b""
    buffer.write(2, b"eeee")
    assert buffer.take(2) == b"eeee"
    buffer.close()
    buffer.write(1, b"ffff")
    assert buffer.sizes() == {}


def test_pcm_alignment_and_large_frames():
    buffer = PCMBuffer(max_bytes=8)
    buffer.write(1, b"bad")
    assert buffer.sizes() == {}
    buffer.write(1, b"abcd" * 100)
    assert buffer.take(1) == b"abcdabcd"
    assert buffer.dropped_bytes == 395
    with pytest.raises(ValueError):
        PCMBuffer(max_bytes=7)


@pytest.fixture
def voice_channel(bot, settings):
    settings.voice_enabled = True
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id = 10
    channel.guild = MagicMock(id=20)
    channel.guild.voice_client = None
    channel.permissions_for.return_value = discord.Permissions.all()
    voice_client = MagicMock(channel=channel)
    voice_client.is_connected.return_value = True
    voice_client.disconnect = AsyncMock()

    async def connect(**kwargs):
        channel.guild.voice_client = voice_client
        return voice_client

    channel.connect = AsyncMock(side_effect=connect)
    bot.get_channel.return_value = channel
    bot.get_guild.return_value = channel.guild
    return channel


async def test_join_idempotent_leave(client, voice_channel, app):
    body = {"channel_id": 10, "receive": False}
    first = await client.post("/api/v1/voice/join", json=body)
    assert first.status_code == 200
    assert first.json()["connected"] is True
    assert (await client.post("/api/v1/voice/join", json=body)).status_code == 200
    voice_channel.connect.assert_awaited_once()
    assert (await client.post("/api/v1/voice/join", json={"channel_id": 10})).status_code == 409
    assert (await client.post("/api/v1/voice/leave", json={"guild_id": 20})).status_code == 204
    voice_channel.guild.voice_client.disconnect.assert_awaited_once_with(force=True)
    assert not app.state.voice_manager.sessions


async def test_disabled_voice(client, bot):
    assert (await client.post("/api/v1/voice/join", json={"channel_id": 10})).status_code == 503


async def test_receive_buffer_route_and_disconnect(client, voice_channel, app, mocker):
    mocker.patch("app.audio.manager.discord.opus.is_loaded", return_value=True)
    backend = mocker.patch(
        "app.audio.manager.receive_backend", return_value=(MagicMock, MagicMock())
    )
    assert (await client.post("/api/v1/voice/join", json={"channel_id": 10})).status_code == 200
    buffer = backend.call_args.args[0]
    buffer.write(30, b"abcd" * 10)
    response = await client.get("/api/v1/voice/20/audio/30")
    assert response.content == b"abcd" * 10
    assert response.headers["X-Audio-Rate"] == "48000"
    assert (await client.get("/api/v1/voice/20/audio/30")).content == b""
    voice_channel.guild.voice_client.is_connected.return_value = False
    result = await client.get("/api/v1/voice/20/status")
    assert result.json()["connected"] is False
    assert buffer.closed
    assert (await client.get("/api/v1/voice/20/audio/30")).status_code == 409


async def test_voice_permission_and_type(client, voice_channel, bot):
    voice_channel.permissions_for.return_value = discord.Permissions.none()
    assert (await client.post("/api/v1/voice/join", json={"channel_id": 10})).status_code == 403
    bot.get_channel.return_value = MagicMock(spec=discord.TextChannel)
    assert (await client.post("/api/v1/voice/join", json={"channel_id": 10})).status_code == 400


async def test_voice_timeout_cleans_state(client, voice_channel, app):
    voice_channel.connect.side_effect = TimeoutError
    response = await client.post("/api/v1/voice/join", json={"channel_id": 10, "receive": False})
    assert response.status_code == 504
    assert not app.state.voice_manager.sessions
    assert not app.state.voice_manager._pending


async def test_optional_dependency_error(client, voice_channel, mocker):
    mocker.patch("app.audio.manager.receive_backend", side_effect=ImportError)
    response = await client.post("/api/v1/voice/join", json={"channel_id": 10})
    assert response.status_code == 503
    voice_channel.connect.assert_not_awaited()


def test_real_sink_integration():
    pytest.importorskip("discord.ext.voice_recv")
    from app.audio.sink import receive_backend

    buffer = PCMBuffer()
    _, sink = receive_backend(buffer)
    assert sink.wants_opus() is False
    sink.write(SimpleNamespace(id=1), SimpleNamespace(pcm=b"abcd"))
    sink.write(None, SimpleNamespace(pcm=b"efgh"))
    assert buffer.take(1) == b"abcd"
    sink.cleanup()
    assert buffer.closed


async def test_concurrent_joins_create_one_connection(client, voice_channel):
    original_connect = voice_channel.connect.side_effect

    async def delayed_connect(**kwargs):
        await asyncio.sleep(0.01)
        return await original_connect(**kwargs)

    voice_channel.connect.side_effect = delayed_connect
    body = {"channel_id": 10, "receive": False}
    results = await asyncio.gather(
        client.post("/api/v1/voice/join", json=body),
        client.post("/api/v1/voice/join", json=body),
    )
    assert [result.status_code for result in results] == [200, 200]
    voice_channel.connect.assert_awaited_once()
