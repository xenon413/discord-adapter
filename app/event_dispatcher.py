import asyncio
import ipaddress
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)
Event = dict[str, Any]
EVENT_TYPES = frozenset({"message", "member_join", "voice_state_update"})


def local_webhook_url(value: str) -> str:
    """Pin local destinations to numeric loopback addresses, without DNS or redirects."""
    if len(value) > 2048 or any(ord(char) < 33 for char in value) or "\\" in value:
        raise ValueError("Invalid webhook URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Webhook URL must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("Webhook URL cannot contain credentials or fragments")
    host = parsed.hostname
    if host.lower() == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("Webhook host must be localhost or a loopback IP literal") from exc
    if not address.is_loopback or (
        isinstance(address, ipaddress.IPv6Address)
        and (address.ipv4_mapped is not None or address.scope_id is not None)
    ):
        raise ValueError("Only loopback webhook destinations are allowed")
    port = parsed.port
    if port is not None and port < 1:
        raise ValueError("Invalid webhook port")
    authority = f"[{address}]" if address.version == 6 else str(address)
    if port is not None:
        authority += f":{port}"
    return urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, ""))


@dataclass
class Subscriber:
    id: str
    url: str
    events: frozenset[str]
    queue: asyncio.Queue[Event]
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class EventDispatcher:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.queue: asyncio.Queue[Event] = asyncio.Queue(settings.event_queue_size)
        self.subscribers: dict[str, Subscriber] = {}
        self.sockets: dict[str, asyncio.Queue[Event]] = {}
        self.client = client
        self.task: asyncio.Task[None] | None = None
        self.accepting = False
        self.published = 0
        self.dropped = 0
        self.delivered = 0
        self.failed = 0

    async def start(self) -> None:
        if self.task is not None:
            return
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=self.settings.webhook_timeout,
                trust_env=False,
                follow_redirects=False,
                limits=httpx.Limits(max_connections=self.settings.max_subscribers),
            )
        self.accepting = True
        self.task = asyncio.create_task(self._dispatch(), name="event-dispatcher")
        for subscriber in self.subscribers.values():
            self._start_subscriber(subscriber)

    def subscribe(self, url: str, events: frozenset[str]) -> Subscriber:
        url = local_webhook_url(url)
        if not events or not events <= EVENT_TYPES:
            raise ValueError("Unknown or empty event selection")
        for subscriber in self.subscribers.values():
            if subscriber.url == url:
                subscriber.events = events
                return subscriber
        self._check_capacity()
        subscriber = Subscriber(
            str(uuid4()), url, events, asyncio.Queue(self.settings.subscriber_queue_size)
        )
        self.subscribers[subscriber.id] = subscriber
        if self.accepting:
            self._start_subscriber(subscriber)
        return subscriber

    def _check_capacity(self) -> None:
        if len(self.subscribers) + len(self.sockets) >= self.settings.max_subscribers:
            raise ValueError("Subscriber capacity reached")

    def _start_subscriber(self, subscriber: Subscriber) -> None:
        subscriber.task = asyncio.create_task(
            self._deliver(subscriber), name=f"webhook-{subscriber.id}"
        )

    async def unsubscribe(self, subscriber_id: str) -> bool:
        subscriber = self.subscribers.pop(subscriber_id, None)
        if subscriber is None:
            return False
        if subscriber.task is not None:
            subscriber.task.cancel()
            await asyncio.gather(subscriber.task, return_exceptions=True)
        return True

    def add_socket(self) -> tuple[str, asyncio.Queue[Event]]:
        self._check_capacity()
        socket_id = str(uuid4())
        queue: asyncio.Queue[Event] = asyncio.Queue(self.settings.subscriber_queue_size)
        self.sockets[socket_id] = queue
        return socket_id, queue

    def remove_socket(self, socket_id: str) -> None:
        self.sockets.pop(socket_id, None)

    def publish(self, event_type: str, data: dict[str, Any]) -> bool:
        if event_type not in EVENT_TYPES:
            raise ValueError("Unknown event type")
        event = {
            "id": str(uuid4()),
            "type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": data,
        }
        if not self.accepting:
            self.dropped += 1
            return False
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1
            return False
        self.published += 1
        return True

    def _enqueue(self, queue: asyncio.Queue[Event], event: Event) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1

    async def _dispatch(self) -> None:
        while True:
            event = await self.queue.get()
            try:
                for subscriber in self.subscribers.values():
                    if event["type"] in subscriber.events:
                        self._enqueue(subscriber.queue, event)
                for queue in self.sockets.values():
                    self._enqueue(queue, event)
            finally:
                self.queue.task_done()

    async def _deliver(self, subscriber: Subscriber) -> None:
        while True:
            event = await subscriber.queue.get()
            try:
                if await self._post(subscriber, event):
                    self.delivered += 1
                else:
                    self.failed += 1
                    logger.warning(
                        "Webhook delivery exhausted", extra={"subscriber_id": subscriber.id}
                    )
            finally:
                subscriber.queue.task_done()

    async def _post(self, subscriber: Subscriber, event: Event) -> bool:
        assert self.client is not None
        for attempt in range(self.settings.webhook_attempts):
            try:
                # Do not buffer untrusted response bodies, follow redirects, or use proxy env vars.
                async with self.client.stream(
                    "POST",
                    subscriber.url,
                    json=event,
                    headers={"X-Event-ID": event["id"]},
                    timeout=self.settings.webhook_timeout,
                    follow_redirects=False,
                ) as response:
                    if 200 <= response.status_code < 300:
                        return True
                    if response.status_code != 429 and response.status_code < 500:
                        return False
            except httpx.HTTPError:
                pass
            if attempt + 1 < self.settings.webhook_attempts:
                await asyncio.sleep(min(self.settings.webhook_retry_delay * 2**attempt, 10))
        return False

    async def drain(self) -> None:
        await self.queue.join()
        await asyncio.gather(*(subscriber.queue.join() for subscriber in self.subscribers.values()))

    async def close(self) -> None:
        self.accepting = False
        try:
            if self.task is not None:
                try:
                    await asyncio.wait_for(self.drain(), timeout=self.settings.shutdown_timeout)
                except TimeoutError:
                    logger.warning("Event drain deadline exceeded")
        finally:
            tasks = [s.task for s in self.subscribers.values() if s.task is not None]
            if self.task is not None:
                tasks.append(self.task)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.client is not None:
                await self.client.aclose()
            self.task = None
            self.subscribers.clear()
            self.sockets.clear()

    def stats(self) -> dict[str, int | bool]:
        return {
            "accepting": self.accepting,
            "queued": self.queue.qsize(),
            "webhooks": len(self.subscribers),
            "websockets": len(self.sockets),
            "published": self.published,
            "dropped": self.dropped,
            "delivered": self.delivered,
            "failed": self.failed,
        }
