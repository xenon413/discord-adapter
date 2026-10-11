import discord
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(discord.HTTPException)
    async def discord_error(request: Request, exc: discord.HTTPException) -> JSONResponse:
        if isinstance(exc, discord.NotFound):
            return JSONResponse(status_code=404, content={"detail": "Discord resource not found"})
        if isinstance(exc, discord.Forbidden):
            return JSONResponse(status_code=403, content={"detail": "Discord permission denied"})
        return JSONResponse(status_code=502, content={"detail": "Discord upstream request failed"})

    @app.exception_handler(discord.ClientException)
    async def discord_client_error(request: Request, exc: discord.ClientException) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "Discord client state conflict"})
