import discord
from discord.ext import commands

from app.audio.manager import VoiceManager
from app.event_dispatcher import EventDispatcher
from app.serialization import member_payload, message_payload


class EventListener(commands.Cog):
    def __init__(
        self, dispatcher: EventDispatcher, voice_manager: VoiceManager | None = None
    ) -> None:
        self.dispatcher = dispatcher
        self.voice_manager = voice_manager

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        self.dispatcher.publish("message", message_payload(message))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        self.dispatcher.publish(
            "member_join",
            {
                "guild_id": str(member.guild.id),
                "member": member_payload(member),
            },
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        def state_payload(state: discord.VoiceState) -> dict[str, str | bool | None]:
            return {
                "channel_id": str(state.channel.id) if state.channel else None,
                "self_mute": state.self_mute,
                "self_deaf": state.self_deaf,
                "mute": state.mute,
                "deaf": state.deaf,
            }

        self.dispatcher.publish(
            "voice_state_update",
            {
                "guild_id": str(member.guild.id),
                "member_id": str(member.id),
                "before": state_payload(before),
                "after": state_payload(after),
            },
        )
        if self.voice_manager is not None and member.guild.me is not None:
            if member.id == member.guild.me.id and after.channel is None:
                await self.voice_manager.reconcile(member.guild)
