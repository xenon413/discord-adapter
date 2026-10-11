from typing import Any

import discord

from app.audio.buffer import PCMBuffer


def receive_backend(buffer: PCMBuffer) -> tuple[type[Any], Any]:
    """Import only when receiving is requested, keeping the core install voice-free."""
    from discord.ext import voice_recv

    class BufferedSink(voice_recv.AudioSink):
        def __init__(self) -> None:
            super().__init__()

        def wants_opus(self) -> bool:
            return False

        def write(self, user: Any, data: Any) -> None:
            # Called on the extension's audio thread. Never call asyncio APIs here.
            if user is not None and data.pcm:
                buffer.write(user.id, data.pcm)

        def cleanup(self) -> None:
            buffer.close()

    return voice_recv.VoiceRecvClient, BufferedSink()


def prepare_opus() -> None:
    # The decoder's public constructor loads the bundled/system library lazily.
    discord.opus.Decoder()
