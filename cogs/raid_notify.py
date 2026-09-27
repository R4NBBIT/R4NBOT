"""/레이드알림추가, /레이드알림삭제, /레이드알림목록.

원하는 (레이드, 난이도) 조합을 구독해두면, 해당 조합의 모집 게시물이
새로 생성될 때(cogs/raid_schedule.py) 자동으로 멘션돼요.
"""
import discord
from discord.ext import commands
from discord import app_commands

from core.raid_data import load_raid_data, DIFF_ORDER, get_schedulable_raid_data
from core.raid_notify import raid_notify_manager


class RaidNotifyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.raid_data = load_raid_data()

    def _schedulable(self) -> dict[tuple[str, str], tuple]:
        # /레이드 생성 시 실제로 고를 수 있는 조합만 대상으로 함 ("싱글" 등은 제외).
        return get_schedulable_raid_data(self.raid_data)

    # ---------------- 자동완성 ----------------
    async def raid_autocomplete(self, interaction: discord.Interaction, current: str):
        raids = sorted(set(r for r, _ in self._schedulable().keys()))
        return [
            app_commands.Choice(name=r, value=r)
            for r in raids if current.lower() in r.lower()
        ][:25]

    async def diff_autocomplete(self, interaction: discord.Interaction, current: str):
        raid = interaction.namespace.레이드
        combos = self._schedulable()

        if raid:
            valid = {d for (r, d) in combos.keys() if r == raid}
        else:
            valid = {d for (_, d) in combos.keys()}

        sorted_valid = sorted(valid, key=lambda x: DIFF_ORDER.get(x, 999))

        return [
            app_commands.Choice(name=d, value=d)
            for d in sorted_valid
            if current.lower() in d.lower()
        ][:25]

    # ---------------- 구독 추가 ----------------
    @app_commands.command(name="레이드알림추가", description="특정 레이드+난이도 모집 게시물이 올라올 때 멘션받도록 구독합니다.")
    @app_commands.autocomplete(레이드=raid_autocomplete, 난이도=diff_autocomplete)
    async def add_notify(self, interaction: discord.Interaction, 레이드: str, 난이도: str):
        key = (레이드.strip(), 난이도.strip())

        if key not in self._schedulable():
            return await interaction.response.send_message(
                f"❌ 존재하지 않거나 모집 대상이 아닌 조합이에요: **{key[0]} - {key[1]}**",
                ephemeral=True,
            )

        added = raid_notify_manager.subscribe(interaction.guild.id, interaction.user.id, *key)

        if not added:
            return await interaction.response.send_message(
                f"⚠️ 이미 구독 중이에요: **{key[0]} ({key[1]})**",
                ephemeral=True,
            )

        await interaction.response.send_message(
            f"🔔 구독 완료: **{key[0]} ({key[1]})** 모집 게시물이 올라오면 멘션해드릴게요.",
            ephemeral=True,
        )

    # ---------------- 구독 삭제 ----------------
    @app_commands.command(name="레이드알림삭제", description="레이드 알림 구독을 해제합니다.")
    @app_commands.autocomplete(레이드=raid_autocomplete, 난이도=diff_autocomplete)
    async def remove_notify(self, interaction: discord.Interaction, 레이드: str, 난이도: str):
        key = (레이드.strip(), 난이도.strip())
        removed = raid_notify_manager.unsubscribe(interaction.guild.id, interaction.user.id, *key)

        if not removed:
            return await interaction.response.send_message(
                f"❌ 구독 중이 아니에요: **{key[0]} - {key[1]}**",
                ephemeral=True,
            )

        await interaction.response.send_message(
            f"🔕 구독 해제 완료: **{key[0]} ({key[1]})**",
            ephemeral=True,
        )

    # ---------------- 구독 목록 ----------------
    @app_commands.command(name="레이드알림목록", description="내가 구독 중인 레이드 알림 목록을 확인합니다.")
    async def list_notify(self, interaction: discord.Interaction):
        subs = raid_notify_manager.get_user_subscriptions(interaction.guild.id, interaction.user.id)

        if not subs:
            return await interaction.response.send_message(
                "구독 중인 레이드 알림이 없어요. `/레이드알림추가`로 등록해보세요.",
                ephemeral=True,
            )

        subs_sorted = sorted(subs, key=lambda rd: (rd[0], DIFF_ORDER.get(rd[1], 999)))
        lines = "\n".join(f"• {r} ({d})" for r, d in subs_sorted)

        embed = discord.Embed(
            title="🔔 내 레이드 알림 구독 목록",
            description=lines,
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(RaidNotifyCog(bot))
