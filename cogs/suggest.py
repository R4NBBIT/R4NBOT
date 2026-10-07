"""/건의사항: 누구나 쓸 수 있고, 모달(입력창)을 띄워서 건의사항을 받습니다.

입력 내용은 명령어를 쓴 사람 본인에게만 보이고(모달 자체가 원래 그럼), 제출하면 계란(봇 소유자)에게
DM으로 바로 전달됩니다. DM이 안 가는 경우(예: 계란이 봇과 서버를 공유하지 않거나 DM을 막아둔 경우)에
대비해서 data/suggestions.json에도 같이 남겨둡니다.

/건의사항답변: 계란 전용. 자동완성으로 아직 처리 안 한 건의사항을 골라서, 모달에 답변을 적으면
원래 작성자에게 DM으로 전달됩니다.

/건의사항완료처리: 계란 전용. 답장을 보내지 않고, 그냥 처리 완료로 표시해서 목록에서 빼고
싶을 때 씁니다 (예: 중복 건의, 반영할 필요 없는 건의 등).
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
            "answered": False,
            "answer": None,
            "answered_at": None,
            "closed": False,
            "closed_at": None,
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


def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == EGG_ID


ANSWER_CONTENT_MAX_LENGTH = 1500


class AnswerModal(discord.ui.Modal, title="건의사항 답변"):
    answer_input = discord.ui.TextInput(
        label="답변 내용",
        style=discord.TextStyle.paragraph,
        placeholder="건의사항을 보낸 사람에게 DM으로 전달됩니다.",
        max_length=ANSWER_CONTENT_MAX_LENGTH,
    )

    def __init__(self, bot: commands.Bot, suggestion_id: int):
        super().__init__()
        self.bot = bot
        self.suggestion_id = suggestion_id

    async def on_submit(self, interaction: discord.Interaction):
        answer = self.answer_input.value.strip()
        if not answer:
            await interaction.response.send_message("❌ 답변 내용을 입력해주세요.", ephemeral=True)
            return

        records = _load_suggestions()
        record = next((r for r in records if r.get("id") == self.suggestion_id), None)
        if record is None:
            await interaction.response.send_message("❌ 해당 건의사항을 찾을 수 없어요 (이미 삭제됐을 수 있어요).", ephemeral=True)
            return

        now = datetime.now(KST)
        reply_text = (
            f"💬 **건의사항에 대한 답변이 도착했어요**\n"
            f"{'-' * 20}\n"
            f"[내가 보낸 건의사항]\n{record['content']}\n"
            f"{'-' * 20}\n"
            f"[답변]\n{answer}"
        )
        allowed = discord.AllowedMentions(everyone=False, roles=False, users=False)

        try:
            target = self.bot.get_user(record["user_id"]) or await self.bot.fetch_user(record["user_id"])
            await target.send(reply_text, allowed_mentions=allowed)
            sent = True
        except Exception as e:
            print(f"[건의사항답변] DM 전달 실패 (user_id={record['user_id']}): {e}")
            sent = False

        record["answered"] = True
        record["answer"] = answer
        record["answered_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        _save_suggestions(records)

        if sent:
            await interaction.response.send_message(f"✅ #{self.suggestion_id} 건의사항에 답변을 전달했어요.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"⚠️ #{self.suggestion_id} 답변은 기록했지만, DM 전달에는 실패했어요 "
                f"(상대방이 봇과 서버를 공유하지 않거나 DM을 막아뒀을 수 있어요).",
                ephemeral=True,
            )


class SuggestCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="건의사항", description="계란봇에 대한 건의사항을 남깁니다 (나만 입력 내용을 볼 수 있어요)")
    async def suggest(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SuggestionModal(self.bot))

    # ---------------- 건의사항 답변 / 완료처리 (계란 전용) ----------------
    async def unanswered_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower()
        records = _load_suggestions()
        unanswered = [r for r in records if not r.get("answered") and not r.get("closed")]
        unanswered.sort(key=lambda r: r.get("id", 0), reverse=True)

        choices = []
        for r in unanswered:
            label = f"#{r['id']} {r.get('user_tag', '?')}: {r.get('content', '')}"
            if len(label) > 100:
                label = label[:99] + "…"
            if current and current not in label.lower():
                continue
            choices.append(app_commands.Choice(name=label, value=str(r["id"])))
        return choices[:25]

    @app_commands.command(name="건의사항답변", description="받은 건의사항에 답변을 보냅니다 (계란 전용)")
    @app_commands.check(is_owner)
    @app_commands.autocomplete(건의사항=unanswered_autocomplete)
    @app_commands.describe(건의사항="답변할 건의사항을 자동완성 목록에서 선택")
    async def answer_suggestion(self, interaction: discord.Interaction, 건의사항: str):
        try:
            suggestion_id = int(건의사항)
        except ValueError:
            await interaction.response.send_message("❌ 건의사항은 자동완성 목록에서 선택해주세요.", ephemeral=True)
            return

        records = _load_suggestions()
        record = next((r for r in records if r.get("id") == suggestion_id), None)
        if record is None:
            await interaction.response.send_message("❌ 해당 건의사항을 찾을 수 없어요.", ephemeral=True)
            return

        await interaction.response.send_modal(AnswerModal(self.bot, suggestion_id))

    @answer_suggestion.error
    async def answer_suggestion_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send("⛔ 이 명령어는 계란 외에는 사용할 수 없습니다.", ephemeral=True)
            else:
                await interaction.response.send_message("⛔ 이 명령어는 계란 외에는 사용할 수 없습니다.", ephemeral=True)

    @app_commands.command(
        name="건의사항완료처리",
        description="답장 없이 건의사항을 처리 완료로 표시해서 목록에서 뺍니다 (계란 전용)",
    )
    @app_commands.check(is_owner)
    @app_commands.autocomplete(건의사항=unanswered_autocomplete)
    @app_commands.describe(건의사항="처리 완료로 표시할 건의사항을 자동완성 목록에서 선택")
    async def close_suggestion(self, interaction: discord.Interaction, 건의사항: str):
        try:
            suggestion_id = int(건의사항)
        except ValueError:
            await interaction.response.send_message("❌ 건의사항은 자동완성 목록에서 선택해주세요.", ephemeral=True)
            return

        records = _load_suggestions()
        record = next((r for r in records if r.get("id") == suggestion_id), None)
        if record is None:
            await interaction.response.send_message("❌ 해당 건의사항을 찾을 수 없어요.", ephemeral=True)
            return

        record["closed"] = True
        record["closed_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        _save_suggestions(records)

        await interaction.response.send_message(
            f"✅ #{suggestion_id} 건의사항을 처리 완료로 표시했어요. (답변 DM은 안 보내졌어요)",
            ephemeral=True,
        )

    @close_suggestion.error
    async def close_suggestion_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send("⛔ 이 명령어는 계란 외에는 사용할 수 없습니다.", ephemeral=True)
            else:
                await interaction.response.send_message("⛔ 이 명령어는 계란 외에는 사용할 수 없습니다.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SuggestCog(bot))
