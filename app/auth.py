import secrets

from fastapi import Header, HTTPException, Request


def valid_api_key(provided: str | None, expected: str) -> bool:
    return provided is not None and secrets.compare_digest(provided.encode(), expected.encode())


async def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    expected = request.app.state.settings.api_secret_key.get_secret_value()
    if not valid_api_key(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
