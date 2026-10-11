from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodyLimitMiddleware:
    """Bound request bodies including chunked bodies without Content-Length."""

    def __init__(self, app: ASGIApp, max_bytes: int = 65536) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        try:
            length = int(headers.get(b"content-length", b"0"))
            if length < 0:
                raise ValueError
        except ValueError:
            await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(
                scope, receive, send
            )
            return
        if length > self.max_bytes:
            await JSONResponse({"detail": "Request body too large"}, status_code=413)(
                scope, receive, send
            )
            return
        # Buffer at most max_bytes so oversized chunks never reach the JSON parser.
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                await JSONResponse({"detail": "Request body too large"}, status_code=413)(
                    scope, receive, send
                )
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        consumed = False

        async def limited_receive() -> Message:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, limited_receive, send)
