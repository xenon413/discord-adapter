import asyncio
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import Field

from app.auth import valid_api_key
from app.event_dispatcher import EVENT_TYPES, EventDispatcher
from app.models import InputModel

router = APIRouter(prefix="/api/v1", tags=["events"])
socket_router = APIRouter()


def get_dispatcher(request: Request) -> EventDispatcher:
    return request.app.state.dispatcher


Dispatcher = Annotated[EventDispatcher, Depends(get_dispatcher)]


class SubscriptionCreate(InputModel):
    url: str = Field(min_length=1, max_length=2048)
    events: set[Literal["message", "member_join", "voice_state_update"]] = Field(
        default_factory=lambda: set(EVENT_TYPES),
        min_length=1,
    )


@router.post("/webhooks/subscribe", status_code=201)
async def subscribe(body: SubscriptionCreate, dispatcher: Dispatcher) -> dict[str, Any]:
    try:
        subscriber = dispatcher.subscribe(body.url, frozenset(body.events))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": subscriber.id, "url": subscriber.url, "events": sorted(subscriber.events)}


@router.get("/webhooks")
async def subscriptions(dispatcher: Dispatcher) -> list[dict[str, Any]]:
    return [
        {"id": s.id, "url": s.url, "events": sorted(s.events)}
        for s in dispatcher.subscribers.values()
    ]


@router.delete("/webhooks/{subscriber_id}", status_code=204)
async def unsubscribe(subscriber_id: str, dispatcher: Dispatcher) -> None:
    if not await dispatcher.unsubscribe(subscriber_id):
        raise HTTPException(404, "Subscriber not found")


@router.get("/events/status")
async def event_status(dispatcher: Dispatcher) -> dict[str, int | bool]:
    return dispatcher.stats()


@socket_router.websocket("/api/v1/events/ws")
async def events_socket(websocket: WebSocket) -> None:
    expected = websocket.app.state.settings.api_secret_key.get_secret_value()
    if not valid_api_key(websocket.headers.get("x-api-key"), expected):
        await websocket.close(code=1008)
        return
    dispatcher: EventDispatcher = websocket.app.state.dispatcher
    try:
        socket_id, queue = dispatcher.add_socket()
    except ValueError:
        await websocket.close(code=1013)
        return
    tasks: list[asyncio.Task[None]] = []
    try:
        await websocket.accept()

        async def send() -> None:
            while True:
                event = await queue.get()
                try:
                    await asyncio.wait_for(websocket.send_json(event), timeout=10)
                finally:
                    queue.task_done()

        async def receive() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return

        tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except TimeoutError:
        await websocket.close(code=1013)
    finally:
        # Release capacity before awaiting: ASGI cancellation can interrupt cleanup awaits.
        dispatcher.remove_socket(socket_id)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
