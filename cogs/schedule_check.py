"""/일정확인: 특정 사용자가 참가 신청(또는 대기열)한 레이드 + 종겜 + 정모 일정을 날짜순으로 한 번에 보여줍니다."""
from datetime import date, datetime, time

import discord
from discord import app_commands
from discord.ext import commands

from cogs.raid_schedule import KST, ROLE_LABEL, WEEKDAYS_KO, _find_application

_DESC_LIMIT = 4000  # 임베드 설명 최대 4096자 여유분


def _start(entry: dict) -> datetime | None:
    try:
        return datetime.combine(
            date.fromisoformat(entry["date"]),
            time(hour=entry["hour"], minute=entry["minute"]),
            tzinfo=KST,
        )
    except Exception:
        return None


def _when(start_dt: datetime) -> str:
    return f"{start_dt:%Y-%m-%d}({WEEKDAYS_KO[start_dt.weekday()]}) {start_dt:%H:%M}"


class ScheduleCheckCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="일정확인", description="특정 사용자가 참가 신청(또는 대기열)한 레이드·종겜·정모 일정을 한 번에 확인합니다.")
    @app_commands.describe(사용자="확인할 사용자 (생략하면 본인)")
    async def check_schedule(self, interaction: discord.Interaction, 사용자: discord.Member | None = None):
        if interaction.guild is None:
            await interaction.response.send_message("이 명령어는 서버에서만 사용할 수 있어요.", ephemeral=True)
            return

        target = 사용자 or interaction.user
        guild_id = interaction.guild.id
        found: list[tuple[datetime, str]] = []

        raid_cog = self.bot.get_cog("RaidScheduleCog")
        for entry in (raid_cog.raids.values() if raid_cog else []):
            if entry.get("guild_id") != guild_id:
                continue
            app = _find_application(entry, target.id)
            start_dt = _start(entry)
            if app is None or start_dt is None:
                continue

            diff_part = f" ({entry['diff']})" if entry.get("diff") else ""
            role_label = ROLE_LABEL.get(app["role"], app["role"])
            if app["where"] == "participant":
                status = f"✅ 참가자 ({role_label})"
            else:
                same_role_queue = [p for p in entry.get("queue", []) if p.get("role") == app["role"]]
                position = next(
                    (i + 1 for i, p in enumerate(same_role_queue) if p.get("user_id") == target.id), None
                )
                pos_text = f" {position}번째" if position else ""
                status = f"⏳ 대기열{pos_text} ({role_label})"

            where = f"<#{entry['channel_id']}>" if entry.get("channel_id") else entry["title"]
            found.append((start_dt, f"⚔️ **{_when(start_dt)} · {entry['raid']}{diff_part}**\n└ {status} · {where}"))

        game_cog = self.bot.get_cog("GameRecruitCog")
        for entry in (game_cog.games.values() if game_cog else []):
            if entry.get("guild_id") != guild_id:
                continue
            start_dt = _start(entry)
            if start_dt is None:
                continue
            if target.id in entry.get("participants", []):
                status = "✅ 참가자"
            elif target.id in entry.get("queue", []):
                position = entry["queue"].index(target.id) + 1
                status = f"⏳ 대기열 {position}번째"
            else:
                continue

            where = f"<#{entry['channel_id']}>" if entry.get("channel_id") else entry["title"]
            found.append((start_dt, f"🎮 **{_when(start_dt)} · {entry['game']}**\n└ {status} · {where}"))

        meet_cog = self.bot.get_cog("MeetupCog")
        for entry in (meet_cog.meets.values() if meet_cog else []):
            if entry.get("guild_id") != guild_id or target.id not in entry.get("participants", []):
                continue
            start_dt = _start(entry)
            if start_dt is None:
                continue
            where = f"<#{entry['channel_id']}>" if entry.get("channel_id") else entry["title"]
            found.append(
                (start_dt, f"🍻 **{_when(start_dt)} · {entry['activity']}**\n└ ✅ 참가자 · 📍 {entry['place']} · {where}")
            )

        if not found:
            await interaction.response.send_message(
                f"**{target.display_name}**님이 참가 신청한 레이드·종겜·정모 일정이 없어요.", ephemeral=True
            )
            return

        found.sort(key=lambda x: x[0])

        lines: list[str] = []
        length = 0
        for i, (_, text) in enumerate(found):
            if length + len(text) + 2 > _DESC_LIMIT:
                lines.append(f"… 외 {len(found) - i}건")
                break
            lines.append(text)
            length += len(text) + 2

        embed = discord.Embed(
            title=f"📅 {target.display_name}님의 일정",
            description="\n\n".join(lines),
            color=discord.Color.teal(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(ScheduleCheckCog(bot))
