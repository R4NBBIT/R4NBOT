"""/건의사항: 누구나 쓸 수 있고, 모달(입력창)을 띄워서 건의사항을 받습니다.

입력 내용은 명령어를 쓴 사람 본인에게만 보이고(모달 자체가 원래 그럼), 제출하면 계란(봇 소유자)에게
DM으로 바로 전달됩니다. DM이 안 가는 경우(예: 계란이 봇과 서버를 공유하지 않거나 DM을 막아둔 경우)에
대비해서 data/suggestions.json에도 같이 남겨둡니다.
"""
import json
import os
import tempfile
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from core.config import EGG_ID
from core.raid_data import KST

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_BASE_DIR, "data")
SUGGESTIONS_FILE = os.path.join(_DATA_DIR, "suggestions.json")

CONTENT_MAX_LENGTH = 1500


def _load_suggestions() -> list[dict]:
    os.makedirs(_DATA_DIR, exist_ok=True)
    if not os.path.exists(SUGGESTIONS_FILE):
        return []
    try:
        with open(SUGGESTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or []
    except Exception as e:
        print(f"[건의사항] 로드 실패: {e}")
        return []


def _save_suggestions(data: list[dict]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=_DATA_DIR)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, SUGGESTIONS_FILE)
    except Exception as e:
        print(f"[건의사항] 저장 실패: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass


class SuggestionModal(discord.ui.Modal, title="건의사항"):
    content_input = discord.ui.TextInput(
        label="건의사항",
        style=discord.TextStyle.paragraph,
        placeholder="자유롭게 적어주세요. 작성자 외에는 아무도 못 봐요.",
        max_length=CONTENT_MAX_LENGTH,
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        content = self.content_input.value.strip()
        if not content:
            await interaction.response.send_message("❌ 내용을 입력해주세요.", ephemeral=True)
            return

        now = datetime.now(KST)
        guild_name = interaction.guild.name if interaction.guild else "(DM)"
        guild_id = interaction.guild.id if interaction.guild else None
        channel_id = interaction.channel.id if interaction.channel else None

        records = _load_suggestions()
        record = {
            "id": len(records) + 1,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": interaction.user.id,
            "user_tag": str(interaction.user),
            "guild_id": guild_id,
            "guild_name": guild_name,
            "channel_id": channel_id,
            "content": content,
            "delivered": False,
        }

        dm_text = (
            f"📮 **건의사항이 도착했어요**\n"
            f"보낸 사람: {interaction.user.mention} ({interaction.user})\n"
            f"서버: {guild_name}\n"
            f"시각: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"{'-' * 20}\n"
            f"{content}"
        )
        allowed = discord.AllowedMentions(everyone=False, roles=False, users=False)
        try:
            owner = self.bot.get_user(EGG_ID) or await self.bot.fetch_user(EGG_ID)
            await owner.send(dm_text, allowed_mentions=allowed)
            record["delivered"] = True
        except Exception as e:
            print(f"[건의사항] 계란에게 DM 전달 실패: {e}")

        records.append(record)
        _save_suggestions(records)

        await interaction.response.send_message(
            "✅ 건의사항이 전달됐어요. 감사합니다!", ephemeral=True
        )


class SuggestCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="건의사항", description="계란봇에 대한 건의사항을 남깁니다 (나만 입력 내용을 볼 수 있어요)")
    async def suggest(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SuggestionModal(self.bot))


async def setup(bot: commands.Bot):
    await bot.add_cog(SuggestCog(bot))
