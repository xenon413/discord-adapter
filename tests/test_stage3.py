import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.cogs.event_listener import EventListener
from app.event_dispatcher import EventDispatcher, local_webhook_url


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://169.254.169.254/latest",
        "http://10.0.0.1",
        "http://localhost.evil.test",
        "http://user:pass@localhost",
        "file:///tmp/x",
        "http://127.0.0.1/#fragment",
        "http://2130706433",
        "http://127.0.0.1:0",
        "http://127.0.0.1\n.evil.test",
        "http://127.0.0.1:99999",
        "http://[::ffff:127.0.0.1]",
    ],
)
def test_webhook_destination_safety(url):
    with pytest.raises(ValueError):
        local_webhook_url(url)


def test_localhost_is_pinned():
    assert local_webhook_url("http://localhost:9000/events") == "http://127.0.0.1:9000/events"
    assert local_webhook_url("http://[::1]:9000/events") == "http://[::1]:9000/events"


async def test_message_forwarding_retries_and_event_id(settings):
    received = []

    async def receive(request):
        received.append(json.loads(request.content))
        assert request.headers["X-Event-ID"] == received[-1]["id"]
        return httpx.Response(503 if len(received) == 1 else 204)

    settings.webhook_retry_delay = 0
    client = httpx.AsyncClient(transport=httpx.MockTransport(receive))
    dispatcher = EventDispatcher(settings, client)
    dispatcher.subscribe("http://127.0.0.1:9000/events", frozenset({"message"}))
    await dispatcher.start()
    try:
        listener = EventListener(dispatcher)
        await listener.on_message(
            SimpleNamespace(
                id=100,
                channel=SimpleNamespace(id=200),
                guild=None,
                author=SimpleNamespace(id=300, name="test", bot=False),
                content="hello",
                embeds=[],
                attachments=[],
                created_at=datetime.now(UTC),
            )
        )
        await asyncio.wait_for(dispatcher.drain(), 1)
        assert len(received) == 2
        assert received[0] == received[1]
        assert received[0]["data"]["content"] == "hello"
        assert received[0]["data"]["guild_id"] is None
        assert dispatcher.delivered == 1
    finally:
        await dispatcher.close()
    assert client.is_closed


@pytest.mark.parametrize("status,attempts", [(302, 1), (400, 1), (429, 3), (500, 3)])
async def test_retry_policy(settings, status, attempts):
    calls = []

    async def receive(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://example.com"})

    settings.webhook_retry_delay = 0
    dispatcher = EventDispatcher(
        settings, httpx.AsyncClient(transport=httpx.MockTransport(receive))
    )
    dispatcher.subscribe("http://127.0.0.1", frozenset({"message"}))
    await dispatcher.start()
    try:
        dispatcher.publish("message", {})
        await asyncio.wait_for(dispatcher.drain(), 1)
        assert len(calls) == attempts
        assert dispatcher.failed == 1
    finally:
        await dispatcher.close()


async def test_bounded_queue_filters_and_slow_subscriber(settings):
    settings.event_queue_size = 1
    settings.subscriber_queue_size = 1
    dispatcher = EventDispatcher(settings)
    await dispatcher.start()
    try:
        socket_id, queue = dispatcher.add_socket()
        assert dispatcher.publish("message", {"n": 1})
        assert not dispatcher.publish("message", {"n": 2})
        await dispatcher.queue.join()
        assert dispatcher.publish("message", {"n": 3})
        await dispatcher.queue.join()
        assert queue.qsize() == 1
        assert dispatcher.dropped == 2
        dispatcher.remove_socket(socket_id)
    finally:
        await dispatcher.close()


async def test_other_gateway_events(settings):
    dispatcher = EventDispatcher(settings)
    await dispatcher.start()
    try:
        _, queue = dispatcher.add_socket()
        listener = EventListener(dispatcher)
        member = SimpleNamespace(
            id=1, guild=SimpleNamespace(id=2), name="one", display_name="One", bot=False, roles=[]
        )
        before = SimpleNamespace(
            channel=None, self_mute=False, self_deaf=False, mute=False, deaf=False
        )
        after = SimpleNamespace(
            channel=SimpleNamespace(id=3), self_mute=True, self_deaf=False, mute=False, deaf=False
        )
        await listener.on_member_join(member)
        await listener.on_voice_state_update(member, before, after)
        await dispatcher.queue.join()
        assert (await queue.get())["type"] == "member_join"
        voice = await queue.get()
        assert voice["data"]["before"]["channel_id"] is None
        assert voice["data"]["after"]["channel_id"] == "3"
    finally:
        await dispatcher.close()


async def test_subscription_api(client):
    result = await client.post("/api/v1/webhooks/subscribe", json={"url": "http://localhost:9000"})
    assert result.status_code == 201
    subscriber_id = result.json()["id"]
    repeat = await client.post("/api/v1/webhooks/subscribe", json={"url": "http://localhost:9000"})
    assert repeat.json()["id"] == subscriber_id
    assert len((await client.get("/api/v1/webhooks")).json()) == 1
    assert (await client.delete(f"/api/v1/webhooks/{subscriber_id}")).status_code == 204
    assert (await client.delete(f"/api/v1/webhooks/{subscriber_id}")).status_code == 404
    assert (
        await client.post("/api/v1/webhooks/subscribe", json={"url": "http://example.com"})
    ).status_code == 400


def test_websocket_auth_and_disconnect_cleanup(app, settings):
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/v1/events/ws"):
                pass
        with client.websocket_connect(
            "/api/v1/events/ws",
            headers={"X-API-Key": settings.api_secret_key.get_secret_value()},
        ) as socket:
            client.portal.call(app.state.dispatcher.publish, "message", {"content": "hello"})
            assert socket.receive_json()["data"] == {"content": "hello"}

        async def wait_for_cleanup():
            async with asyncio.timeout(1):
                while app.state.dispatcher.sockets:
                    await asyncio.sleep(0.001)

        client.portal.call(wait_for_cleanup)
        assert not app.state.dispatcher.sockets


async def test_shutdown_deadline_cancels_delivery(settings):
    entered = asyncio.Event()

    async def receive(request):
        entered.set()
        await asyncio.Event().wait()

    settings.shutdown_timeout = 0.02
    client = httpx.AsyncClient(transport=httpx.MockTransport(receive))
    dispatcher = EventDispatcher(settings, client)
    dispatcher.subscribe("http://127.0.0.1", frozenset({"message"}))
    await dispatcher.start()
    dispatcher.publish("message", {})
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.wait_for(dispatcher.close(), 1)
    assert client.is_closed
    assert not dispatcher.publish("message", {})


async def test_slow_webhook_does_not_block_other_subscribers(settings):
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    fast_delivered = asyncio.Event()
    deliveries = []

    async def receive(request):
        if request.url.port == 9001:
            slow_started.set()
            await release_slow.wait()
        else:
            fast_delivered.set()
        deliveries.append(json.loads(request.content)["type"])
        return httpx.Response(204)

    dispatcher = EventDispatcher(
        settings, httpx.AsyncClient(transport=httpx.MockTransport(receive))
    )
    dispatcher.subscribe("http://127.0.0.1:9001", frozenset({"message"}))
    dispatcher.subscribe("http://127.0.0.1:9002", frozenset({"message"}))
    await dispatcher.start()
    try:
        dispatcher.publish("message", {})
        dispatcher.publish("member_join", {})
        await asyncio.wait_for(slow_started.wait(), 1)
        await asyncio.wait_for(fast_delivered.wait(), 1)
        assert not release_slow.is_set()
        release_slow.set()
        await asyncio.wait_for(dispatcher.drain(), 1)
        assert deliveries == ["message", "message"]
    finally:
        release_slow.set()
        await dispatcher.close()
