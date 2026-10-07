"""/공지: 계란봇이 들어가 있는 모든 서버로 한 번에 공지를 보냅니다 (계란 전용).

채널을 미리 지정해 둘 필요가 없습니다. 각 서버의 "일반 채팅방"처럼 쓰이는 채널(시스템 메시지
채널, 없으면 봇이 글을 쓸 수 있는 가장 위쪽 채널)을 서버마다 자동으로 찾아서 그 채널에 바로
메시지로 보냅니다. 계란님이 그 서버에 들어가 있지 않아도, 봇만 그 서버에 있으면 보내집니다.
"""
import discord
from discord import app_commands
from discord.ext import commands

from core.config import EGG_ID

# 공지 내용(사용자 입력)에 @everyone/@here/역할 멘션이 섞여 있어도 터지지 않게 함
_ALLOWED_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=False)


def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == EGG_ID


def _pick_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """공지를 보낼 채널을 자동으로 고름. 시스템 채널을 우선 쓰고, 안 되면 글을 쓸 수 있는
    가장 위쪽(position이 가장 작은) 일반 텍스트 채널을 씀."""
    me = guild.me
    if me is None:
        return None

    def can_send(ch: discord.TextChannel) -> bool:
        perms = ch.permissions_for(me)
        return perms.view_channel and perms.send_messages

    if guild.system_channel is not None and can_send(guild.system_channel):
        return guild.system_channel

    candidates = sorted(
        (ch for ch in guild.text_channels if can_send(ch)),
        key=lambda ch: ch.position,
    )
    return candidates[0] if candidates else None


class AnnounceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------- 자동완성 (봇이 들어가 있는 서버 이름) ----------------
    async def guild_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower()
        return [
            app_commands.Choice(name=f"{g.name} ({g.id})", value=str(g.id))
            for g in self.bot.guilds
            if current in g.name.lower()
        ][:25]

    @app_commands.command(name="공지", description="봇이 들어가 있는 서버로 공지를 보냅니다 (계란 전용)")
    @app_commands.check(is_owner)
    @app_commands.autocomplete(서버=guild_autocomplete)
    @app_commands.describe(내용="보낼 공지 내용", 서버="보낼 서버 하나만 고르기 (비우면 전체 서버로 보냄)")
    async def announce(
        self,
        interaction: discord.Interaction,
        내용: app_commands.Range[str, 1, 1900],
        서버: str | None = None,
    ):
        내용 = 내용.strip()
        if not 내용:
            await interaction.response.send_message("❌ 내용을 입력해주세요.", ephemeral=True)
            return

        if 서버 is not None:
            try:
                target_id = int(서버)
            except ValueError:
                await interaction.response.send_message(
                    "❌ 서버는 자동완성 목록에서 선택해주세요.", ephemeral=True
                )
                return
            target_guild = self.bot.get_guild(target_id)
            if target_guild is None:
                await interaction.response.send_message(
                    "❌ 해당 서버를 찾을 수 없어요. (봇이 더 이상 그 서버에 없을 수 있어요)", ephemeral=True
                )
                return
            guilds = [target_guild]
        else:
            guilds = list(self.bot.guilds)

        await interaction.response.defer(ephemeral=True)

        text = f"📢 **공지**\n{내용}"
        sent = 0
        failed: list[str] = []

        for guild in guilds:
            channel = _pick_channel(guild)
            if channel is None:
                failed.append(f"{guild.name} (보낼 수 있는 채널 없음)")
                continue
            try:
                await channel.send(text, allowed_mentions=_ALLOWED_MENTIONS)
                sent += 1
            except discord.Forbidden:
                failed.append(f"{guild.name} (#{channel.name}, 권한 없음)")
            except Exception as e:
                failed.append(f"{guild.name} (#{channel.name}, {e})")

        summary = f"✅ {sent}개 서버에 공지를 보냈어요."
        if failed:
            summary += "\n⚠️ 실패: " + ", ".join(failed)
        await interaction.followup.send(summary, ephemeral=True)

    @announce.error
    async def announce_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send("⛔ 이 명령어는 계란 외에는 사용할 수 없습니다.", ephemeral=True)
            else:
                await interaction.response.send_message("⛔ 이 명령어는 계란 외에는 사용할 수 없습니다.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AnnounceCog(bot))
