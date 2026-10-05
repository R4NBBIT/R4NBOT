"""종합게임 모집 (/종겜).

/레이드와 비슷한 모집 게시물이지만 다음이 다르다.
- 레이드/난이도 대신 게임 제목을 직접 입력한다.
- 인원수 제한(1 이상의 자연수, 또는 제한없음)을 정한다.
- 캐릭터가 없으므로 참가자는 디스코드 계정 태그만으로 표시한다.
- 모집 설명을 쓸 때 "게임 구매 링크"와 "모집 내용"을 따로 입력받는다.

게시물은 /종겜채널로 지정한 포럼 채널에 올라간다. (/레이드채널과 같은 채널을 지정해도 된다.)
"""
import json
import os
import tempfile
from datetime import date, datetime, time, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.raid_channel import game_channel_manager
from cogs.raid_schedule import (
    KST,
    WEEKDAYS_KO,
    _build_date_options,
    _build_hour_options,
    _build_minute_options,
)

# 봇 소스 위치(EGG-BOT/) 기준 절대경로로 고정
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../EGG-BOT
_DATA_DIR = os.path.join(_BASE_DIR, "data")
GAMES_FILE = os.path.join(_DATA_DIR, "games.json")

UNLIMITED_LABEL = "제한없음"
CAPACITY_INPUT_MAX_LENGTH = 4  # "제한없음"이 4글자라서 최소 4. 숫자는 최대 9999까지 입력 가능

# 사용자가 입력한 텍스트(게임 제목 등)가 들어가는 메시지에서 @everyone/@here/역할 멘션이 터지지 않게 함
_ALLOWED_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)


# =========================
# 저장 / 로드 (종겜 모집 게시물 데이터, message_id를 키로 사용)
# =========================
def _load_games() -> dict:
    os.makedirs(_DATA_DIR, exist_ok=True)
    if not os.path.exists(GAMES_FILE):
        return {}
    try:
        with open(GAMES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        print(f"[종겜] 로드 완료: {len(data)}개 게시물 (경로: {GAMES_FILE})")
        return data
    except Exception as e:
        print(f"[종겜] 로드 실패: {e}")
        return {}


def _save_games(data: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=_DATA_DIR)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, GAMES_FILE)
    except Exception as e:
        print(f"[종겜] 저장 실패: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass


# =========================
# 순수 헬퍼 (디스코드 객체 없이 동작)
# =========================
def _parse_capacity(raw: str) -> tuple[bool, int | None]:
    """인원수 입력값을 해석함. (유효 여부, 인원수) 반환. 인원수 None은 제한없음.

    비워두거나 '제한없음'/'무제한'이면 제한없음, 1 이상의 자연수면 그 값, 그 외는 유효하지 않음.
    """
    text = raw.strip().replace(" ", "")
    if text in ("", UNLIMITED_LABEL, "무제한"):
        return True, None
    if text.isdecimal() and int(text) >= 1:
        return True, int(text)
    return False, None


def _capacity_text(capacity: int | None) -> str:
    return UNLIMITED_LABEL if capacity is None else str(capacity)


def _start_dt(entry: dict) -> datetime:
    return datetime.combine(
        date.fromisoformat(entry["date"]),
        time(hour=entry["hour"], minute=entry["minute"]),
        tzinfo=KST,
    )


def _thread_title(entry: dict) -> str:
    d = date.fromisoformat(entry["date"])
    title = (
        f"{entry['date']}({WEEKDAYS_KO[d.weekday()]}) {entry['hour']:02d}:{entry['minute']:02d} "
        f"{entry['game']} - {entry['title']}"
    )
    return title if len(title) <= 100 else title[:99] + "…"


def _find_user(entry: dict, user_id: int) -> str | None:
    """user_id가 이 모집에 신청한 위치를 반환 ("participant" | "queue" | None)."""
    if user_id in entry["participants"]:
        return "participant"
    if user_id in entry["queue"]:
        return "queue"
    return None


def _has_room(entry: dict) -> bool:
    capacity = entry.get("capacity")
    return capacity is None or len(entry["participants"]) < capacity


def _add_user(entry: dict, user_id: int) -> str:
    """자리가 있으면 참가자로, 없으면 대기열로 추가하고 어디에 들어갔는지("participant"|"queue") 반환."""
    if _has_room(entry):
        entry["participants"].append(user_id)
        return "participant"
    entry["queue"].append(user_id)
    return "queue"


def _fill_from_queue(entry: dict) -> None:
    """빈 자리가 있는 동안 대기열에서 먼저 신청한 사람부터 참가자로 승격시킴."""
    while entry["queue"] and _has_room(entry):
        entry["participants"].append(entry["queue"].pop(0))


def _add_mention_fields(embed: discord.Embed, name: str, user_ids: list[int], *, limit: int = 100) -> None:
    """멘션 목록을 임베드 필드로 추가. 필드 길이 제한(1024자)을 넘지 않게 나눠 담고, 너무 많으면 '외 N명'으로 줄임."""
    if not user_ids:
        embed.add_field(name=name, value="아직 없음", inline=False)
        return

    shown = user_ids[:limit]
    hidden = len(user_ids) - len(shown)
    chunks = [shown[i:i + 40] for i in range(0, len(shown), 40)]
    for idx, chunk in enumerate(chunks):
        value = " ".join(f"<@{uid}>" for uid in chunk)
        if hidden and idx == len(chunks) - 1:
            value += f"\n… 외 {hidden}명"
        embed.add_field(name=name if idx == 0 else "​", value=value, inline=False)


def _build_embed(entry: dict) -> discord.Embed:
    embed = discord.Embed(
        title=_thread_title(entry),
        description=entry.get("content") or None,
        color=discord.Color.green(),
    )
    d = date.fromisoformat(entry["date"])
    embed.add_field(name="🎮 게임", value=entry["game"], inline=False)
    embed.add_field(
        name="🗓️ 일시",
        value=f"{entry['date']}({WEEKDAYS_KO[d.weekday()]}) {entry['hour']:02d}:{entry['minute']:02d}",
        inline=False,
    )
    embed.add_field(name="👑 모집자", value=f"<@{entry['creator_id']}>", inline=False)
    if entry.get("link"):
        embed.add_field(name="🔗 게임 구매 링크", value=entry["link"], inline=False)

    participants = entry["participants"]
    _add_mention_fields(
        embed,
        f"👥 참가자 ({len(participants)}/{_capacity_text(entry.get('capacity'))})",
        participants,
    )
    if entry["queue"]:
        embed.add_field(
            name="⏳ 대기열",
            value=f"{len(entry['queue'])}명 대기 중 ('대기열 명단' 버튼으로 확인)",
            inline=False,
        )
    return embed


def _can_manage(interaction: discord.Interaction, entry: dict) -> bool:
    is_creator = interaction.user.id == entry.get("creator_id")
    is_admin = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator
    return is_creator or is_admin


# =========================
# 모달: 날짜 / 시 / 분 / 게임 제목 / 인원수 (생성용)
# =========================
class GameCreateModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "GameRecruitCog",
        title_text: str,
        *,
        defaults: dict | None = None,
        origin_message: discord.Message | None = None,
    ):
        super().__init__(title="종겜 일정 등록")
        self.cog = cog
        self.title_text = title_text
        self.origin_message = origin_message
        d = defaults or {}

        self.date_select = discord.ui.Select(placeholder="날짜 선택", options=_build_date_options(d.get("date")))
        self.hour_select = discord.ui.Select(placeholder="시 선택 (00-23)", options=_build_hour_options(d.get("hour")))
        self.minute_select = discord.ui.Select(
            placeholder="분 선택 (10분 단위)", options=_build_minute_options(d.get("minute"))
        )
        self.game_input = discord.ui.TextInput(
            placeholder="예: 마인크래프트", default=d.get("game") or None, max_length=100
        )
        self.capacity_input = discord.ui.TextInput(
            placeholder="숫자 또는 제한없음",
            default=d.get("capacity") or UNLIMITED_LABEL,
            required=False,
            max_length=CAPACITY_INPUT_MAX_LENGTH,
        )

        self.add_item(discord.ui.Label(text="날짜", component=self.date_select))
        self.add_item(discord.ui.Label(text="시", component=self.hour_select))
        self.add_item(discord.ui.Label(text="분", component=self.minute_select))
        self.add_item(discord.ui.Label(text="게임 제목", component=self.game_input))
        self.add_item(
            discord.ui.Label(
                text="인원수 제한",
                description="1 이상의 자연수, 제한이 없으면 제한없음",
                component=self.capacity_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        if self.origin_message is not None:
            try:
                await self.origin_message.edit(view=None)
            except Exception:
                pass

        date_str = self.date_select.values[0]
        hour = int(self.hour_select.values[0])
        minute = int(self.minute_select.values[0])
        game = self.game_input.value.strip()
        capacity_raw = self.capacity_input.value

        defaults = {
            "date": date_str, "hour": hour, "minute": minute,
            "game": self.game_input.value, "capacity": capacity_raw,
        }

        async def _retry(message: str) -> None:
            await interaction.response.send_message(
                message, view=_GameRetryView(self.cog, self.title_text, defaults), ephemeral=True
            )

        target_dt = datetime.combine(date.fromisoformat(date_str), time(hour=hour, minute=minute), tzinfo=KST)
        if target_dt <= datetime.now(KST):
            await _retry("❌ 이미 지난 시간으로는 종겜 일정을 등록할 수 없어요. 다른 날짜/시간을 선택해주세요.")
            return

        if not game:
            await _retry("❌ 게임 제목을 입력해주세요.")
            return

        ok, capacity = _parse_capacity(capacity_raw)
        if not ok:
            await _retry("❌ 인원수는 1 이상의 자연수 또는 '제한없음'만 입력할 수 있어요.")
            return

        base = {
            "title": self.title_text, "date": date_str, "hour": hour, "minute": minute,
            "game": game, "capacity": capacity,
        }
        await interaction.response.send_message(
            "종겜 일정이 확인됐어요. 설명을 추가하시겠어요? (안 넣어도 괜찮아요)",
            view=_GameDescriptionStepView(self.cog, base),
            ephemeral=True,
        )


class _GameRetryView(discord.ui.View):
    """입력값이 잘못됐을 때, 입력했던 값을 유지한 채 모달을 다시 띄우는 버튼."""

    def __init__(self, cog: "GameRecruitCog", title_text: str, defaults: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.title_text = title_text
        self.defaults = defaults

    @discord.ui.button(label="다시 입력", style=discord.ButtonStyle.blurple)
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = GameCreateModal(
            self.cog, self.title_text, defaults=self.defaults, origin_message=interaction.message
        )
        await interaction.response.send_modal(modal)


# =========================
# 생성 2단계: 설명 (게임 구매 링크 / 모집 내용 따로 입력)
# =========================
class _GameDescriptionStepView(discord.ui.View):
    def __init__(self, cog: "GameRecruitCog", base: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.base = base

    @discord.ui.button(label="설명 작성하기", style=discord.ButtonStyle.blurple)
    async def write_description(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = GameDescriptionModal(self.cog, self.base, origin_message=interaction.message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="설명 없이 게시", style=discord.ButtonStyle.gray)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="게시물을 생성하고 있어요...", view=None)
        await self.cog.create_game_post(interaction, self.base, link="", content="")


class GameDescriptionModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "GameRecruitCog",
        base: dict,
        *,
        origin_message: discord.Message | None = None,
    ):
        super().__init__(title="종겜 모집 설명 작성")
        self.cog = cog
        self.base = base
        self.origin_message = origin_message
        self.link_input = discord.ui.TextInput(
            label="게임 구매 링크 (선택)",
            required=False,
            max_length=500,
            placeholder="예: https://store.steampowered.com/...",
        )
        self.content_input = discord.ui.TextInput(
            label="모집 내용 (선택, 줄바꿈 가능)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
        )
        self.add_item(self.link_input)
        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction):
        if self.origin_message is not None:
            try:
                await self.origin_message.edit(view=None)
            except Exception:
                pass

        await self.cog.create_game_post(
            interaction,
            self.base,
            link=self.link_input.value.strip(),
            content=self.content_input.value.strip(),
        )


# =========================
# 관리: 일정(날짜/시/분) 수정
# =========================
class GameRescheduleModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "GameRecruitCog",
        game_id: str,
        defaults: dict,
        origin_message: discord.Message | None = None,
    ):
        super().__init__(title="종겜 일정 수정")
        self.cog = cog
        self.game_id = game_id
        self.origin_message = origin_message

        self.date_select = discord.ui.Select(
            placeholder="날짜 선택", options=_build_date_options(defaults.get("date"))
        )
        self.hour_select = discord.ui.Select(
            placeholder="시 선택 (00-23)", options=_build_hour_options(defaults.get("hour"))
        )
        self.minute_select = discord.ui.Select(
            placeholder="분 선택 (10분 단위)", options=_build_minute_options(defaults.get("minute"))
        )
        self.add_item(discord.ui.Label(text="날짜", component=self.date_select))
        self.add_item(discord.ui.Label(text="시", component=self.hour_select))
        self.add_item(discord.ui.Label(text="분", component=self.minute_select))

    async def on_submit(self, interaction: discord.Interaction):
        if self.origin_message is not None:
            try:
                await self.origin_message.edit(view=None)
            except Exception:
                pass

        date_str = self.date_select.values[0]
        hour = int(self.hour_select.values[0])
        minute = int(self.minute_select.values[0])

        target_dt = datetime.combine(date.fromisoformat(date_str), time(hour=hour, minute=minute), tzinfo=KST)
        if target_dt <= datetime.now(KST):
            defaults = {"date": date_str, "hour": hour, "minute": minute}
            await interaction.response.send_message(
                "❌ 이미 지난 시간으로는 일정을 수정할 수 없어요. 다른 날짜/시간을 선택해주세요.",
                view=_GameRescheduleRetryView(self.cog, self.game_id, defaults),
                ephemeral=True,
            )
            return

        await self.cog.update_game_schedule(interaction, self.game_id, date_str, hour, minute)


class _GameRescheduleRetryView(discord.ui.View):
    def __init__(self, cog: "GameRecruitCog", game_id: str, defaults: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.game_id = game_id
        self.defaults = defaults

    @discord.ui.button(label="다시 입력", style=discord.ButtonStyle.blurple)
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = GameRescheduleModal(self.cog, self.game_id, self.defaults, origin_message=interaction.message)
        await interaction.response.send_modal(modal)


# =========================
# 관리: 제목 / 게임 제목 / 인원수 / 구매 링크 / 모집 내용 수정
# =========================
class GameInfoEditModal(discord.ui.Modal):
    def __init__(self, cog: "GameRecruitCog", game_id: str):
        super().__init__(title="종겜 정보 수정")
        self.cog = cog
        self.game_id = game_id
        entry = cog.games.get(game_id) or {}

        self.title_input = discord.ui.TextInput(
            label="제목", default=entry.get("title", ""), max_length=100
        )
        self.game_input = discord.ui.TextInput(
            label="게임 제목", default=entry.get("game", ""), max_length=100
        )
        self.capacity_input = discord.ui.TextInput(
            label="인원수 제한 (1 이상의 자연수 / 제한없음)",
            default=_capacity_text(entry.get("capacity")),
            required=False,
            max_length=CAPACITY_INPUT_MAX_LENGTH,
        )
        self.link_input = discord.ui.TextInput(
            label="게임 구매 링크 (선택)", default=entry.get("link", ""), required=False, max_length=500
        )
        self.content_input = discord.ui.TextInput(
            label="모집 내용 (선택, 줄바꿈 가능)",
            style=discord.TextStyle.paragraph,
            default=entry.get("content", ""),
            required=False,
            max_length=1000,
        )
        for item in (self.title_input, self.game_input, self.capacity_input, self.link_input, self.content_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        entry = self.cog.games.get(self.game_id)
        if entry is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return

        title = self.title_input.value.strip()
        game = self.game_input.value.strip()
        if not title or not game:
            await interaction.response.send_message("❌ 제목과 게임 제목은 비워둘 수 없어요.", ephemeral=True)
            return

        ok, capacity = _parse_capacity(self.capacity_input.value)
        if not ok:
            await interaction.response.send_message(
                "❌ 인원수는 1 이상의 자연수 또는 '제한없음'만 입력할 수 있어요.", ephemeral=True
            )
            return
        if capacity is not None and capacity < len(entry["participants"]):
            await interaction.response.send_message(
                f"❌ 현재 참가자가 {len(entry['participants'])}명이라 그보다 적게 줄일 수 없어요. "
                f"먼저 강제취소로 인원을 정리해주세요.",
                ephemeral=True,
            )
            return

        # 스레드 이름 변경은 디스코드 제한(10분에 2번)에 걸리면 오래 걸릴 수 있어서 먼저 응답을 보류해둠
        await interaction.response.defer(ephemeral=True)

        entry["title"] = title
        entry["game"] = game
        entry["capacity"] = capacity
        entry["link"] = self.link_input.value.strip()
        entry["content"] = self.content_input.value.strip()
        _fill_from_queue(entry)  # 인원수를 늘렸다면 대기열에서 자동 승격

        self.cog.games[self.game_id] = entry
        _save_games(self.cog.games)
        await self.cog._rename_thread(entry)
        await self.cog._update_post_embed(self.game_id)
        await interaction.followup.send("✅ 종겜 정보가 수정됐어요.", ephemeral=True)


# =========================
# 관리: 참가자 태그
# =========================
class GameTagModal(discord.ui.Modal):
    def __init__(self, cog: "GameRecruitCog", game_id: str):
        super().__init__(title="참가자 태그")
        self.cog = cog
        self.game_id = game_id
        self.message_input = discord.ui.TextInput(
            label="같이 보낼 메시지 (선택, 줄바꿈 가능)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        entry = self.cog.games.get(self.game_id)
        if entry is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return
        if not entry["participants"]:
            await interaction.response.send_message("태그할 참가자가 없어요.", ephemeral=True)
            return

        text = " ".join(f"<@{uid}>" for uid in entry["participants"])
        extra = self.message_input.value.strip()
        if extra:
            text += f"\n{extra}"

        await interaction.response.send_message(text, allowed_mentions=_ALLOWED_MENTIONS)


# =========================
# 관리 패널
# =========================
class GameManagePanelView(discord.ui.View):
    def __init__(self, cog: "GameRecruitCog", game_id: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.game_id = game_id

    @discord.ui.button(label="정보수정", style=discord.ButtonStyle.blurple)
    async def edit_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.cog.games.get(self.game_id) is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return
        await interaction.response.send_modal(GameInfoEditModal(self.cog, self.game_id))

    @discord.ui.button(label="일정수정", style=discord.ButtonStyle.blurple)
    async def edit_schedule(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = self.cog.games.get(self.game_id)
        if entry is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return
        modal = GameRescheduleModal(
            self.cog, self.game_id,
            {"date": entry["date"], "hour": entry["hour"], "minute": entry["minute"]},
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="강제참여", style=discord.ButtonStyle.gray)
    async def force_join(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "강제 참여시킬 멤버를 아래에서 검색해주세요.",
            view=_GameMemberPickView(self.cog, self.game_id, mode="join"),
            ephemeral=True,
        )

    @discord.ui.button(label="강제취소", style=discord.ButtonStyle.gray)
    async def force_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "참가(또는 대기)를 강제로 취소시킬 멤버를 아래에서 검색해주세요.",
            view=_GameMemberPickView(self.cog, self.game_id, mode="cancel"),
            ephemeral=True,
        )

    @discord.ui.button(label="삭제", style=discord.ButtonStyle.red)
    async def delete_post(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "⚠️ 정말로 이 종겜 게시물을 삭제하시겠습니까? 되돌릴 수 없어요.",
            view=_GameDeleteConfirmView(self.cog, self.game_id),
            ephemeral=True,
        )


class _GameMemberPickView(discord.ui.View):
    """강제참여 / 강제취소 대상 멤버를 고르는 뷰."""

    def __init__(self, cog: "GameRecruitCog", game_id: str, *, mode: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.game_id = game_id
        self.mode = mode  # "join" | "cancel"

        self.member_select = discord.ui.UserSelect(placeholder="멤버 검색", min_values=1, max_values=1)
        self.member_select.callback = self._on_selected
        self.add_item(self.member_select)

    async def _on_selected(self, interaction: discord.Interaction):
        member = self.member_select.values[0]
        if self.mode == "join":
            await self.cog.force_join(interaction, self.game_id, member)
        else:
            await self.cog.force_cancel(interaction, self.game_id, member)


class _GameDeleteConfirmView(discord.ui.View):
    def __init__(self, cog: "GameRecruitCog", game_id: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.game_id = game_id

    @discord.ui.button(label="확인 (삭제)", style=discord.ButtonStyle.red)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = self.cog.games.pop(self.game_id, None)
        _save_games(self.cog.games)
        if entry is None:
            await interaction.response.edit_message(content="이미 삭제된 게시물이에요.", view=None)
            return
        await interaction.response.edit_message(content="🗑️ 종겜 게시물을 삭제했어요.", view=None)
        try:
            channel = self.cog.bot.get_channel(entry["channel_id"]) or await self.cog.bot.fetch_channel(entry["channel_id"])
            await channel.delete()
        except Exception as e:
            print(f"[종겜] 스레드 삭제 실패: {e}")

    @discord.ui.button(label="취소", style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="삭제를 취소했어요.", view=None)


# =========================
# 게시물에 붙는 버튼 (영속 View - 봇 재구동 후에도 동작하도록 custom_id 고정)
# =========================
class GamePostView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _context(self, interaction: discord.Interaction):
        """(cog, game_id, entry)를 돌려줌. 찾을 수 없으면 안내 메시지를 보내고 None."""
        cog = interaction.client.get_cog("GameRecruitCog")
        game_id = str(interaction.message.id)
        entry = cog.games.get(game_id) if cog else None
        if not cog or entry is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return None
        return cog, game_id, entry

    @discord.ui.button(label="참가신청", style=discord.ButtonStyle.green, custom_id="game_apply")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await self._context(interaction)
        if ctx is None:
            return
        cog, game_id, entry = ctx

        if _find_user(entry, interaction.user.id) is not None:
            await interaction.response.send_message("이미 이 모집에 신청하셨어요.", ephemeral=True)
            return
        await cog.handle_apply(interaction, game_id)

    @discord.ui.button(label="참가취소", style=discord.ButtonStyle.red, custom_id="game_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await self._context(interaction)
        if ctx is None:
            return
        cog, game_id, _ = ctx
        await cog.handle_cancel(interaction, game_id)

    @discord.ui.button(label="참가자 태그", style=discord.ButtonStyle.gray, custom_id="game_tag_participants")
    async def tag_participants(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await self._context(interaction)
        if ctx is None:
            return
        cog, game_id, entry = ctx

        is_participant = _find_user(entry, interaction.user.id) == "participant"
        if not (_can_manage(interaction, entry) or is_participant):
            await interaction.response.send_message(
                "참가자 태그는 작성자, 실제 참가자, 서버 관리자만 사용할 수 있어요. (대기열은 해당 안 돼요)",
                ephemeral=True,
            )
            return
        if not entry["participants"]:
            await interaction.response.send_message(
                "태그할 참가자가 없어요. (대기열 인원은 태그 대상이 아니에요)", ephemeral=True
            )
            return

        await interaction.response.send_modal(GameTagModal(cog, game_id))

    @discord.ui.button(label="대기열 명단", style=discord.ButtonStyle.gray, custom_id="game_queue_list")
    async def queue_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await self._context(interaction)
        if ctx is None:
            return
        _, _, entry = ctx

        if not entry["queue"]:
            await interaction.response.send_message("대기열이 비어있어요.", ephemeral=True)
            return

        lines = [f"{i}. <@{uid}>" for i, uid in enumerate(entry["queue"], 1)]
        embed = discord.Embed(title="⏳ 대기열 명단", description="\n".join(lines), color=discord.Color.orange())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="⚙️ 관리", style=discord.ButtonStyle.gray, custom_id="game_manage")
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await self._context(interaction)
        if ctx is None:
            return
        cog, game_id, entry = ctx

        if not _can_manage(interaction, entry):
            await interaction.response.send_message(
                "이 모집을 관리할 권한이 없어요. (서버 관리자 또는 작성자만 가능해요)", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "이 모집을 관리해요. (나만 보여요)", view=GameManagePanelView(cog, game_id), ephemeral=True
        )


# =========================
# Cog
# =========================
class GameRecruitCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: dict = _load_games()

    async def cog_load(self) -> None:
        self.cleanup_loop.start()

    def cog_unload(self) -> None:
        self.cleanup_loop.cancel()

    # ---------------- 시작 10분 전 알림 / 시작 30분 후 마감 ----------------
    @tasks.loop(minutes=1)
    async def cleanup_loop(self) -> None:
        now = datetime.now(KST)
        for game_id, entry in list(self.games.items()):
            try:
                start_dt = _start_dt(entry)
                remaining = start_dt - now
                if not entry.get("reminder_sent") and timedelta(0) < remaining <= timedelta(minutes=10):
                    await self._send_start_reminder(game_id, entry)
                if now >= start_dt + timedelta(minutes=30):
                    await self._close_and_lock(game_id, entry)
            except Exception as e:
                print(f"[종겜] 정리 작업 실패 (game_id={game_id}): {e}")

    @cleanup_loop.before_loop
    async def before_cleanup_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _send_start_reminder(self, game_id: str, entry: dict) -> None:
        # 전송 실패해도 매분 재시도로 스팸이 되지 않게, 먼저 플래그부터 저장함
        entry["reminder_sent"] = True
        self.games[game_id] = entry
        _save_games(self.games)

        text = f"⏰ 곧 시작! 약 10분 뒤에 **{entry['game']}** 게임이 시작돼요."
        if entry["participants"]:
            text += "\n" + " ".join(f"<@{uid}>" for uid in entry["participants"])
        await self._send_to_thread(entry, text)

    async def _close_and_lock(self, game_id: str, entry: dict) -> None:
        # 처리 실패해도 데이터는 먼저 지워서 무한 재시도로 매분 실패 로그가 쌓이지 않게 함
        self.games.pop(game_id, None)
        _save_games(self.games)

        try:
            thread = self.bot.get_channel(entry["channel_id"]) or await self.bot.fetch_channel(entry["channel_id"])
        except discord.NotFound:
            return
        except Exception as e:
            print(f"[종겜] 스레드 조회 실패 (game_id={game_id}): {e}")
            return

        try:
            await thread.send("🔒 게임 시작 후 30분이 지나서 이 게시물을 마감했어요.")
        except Exception as e:
            print(f"[종겜] 마감 메시지 전송 실패 (game_id={game_id}): {e}")

        try:
            await thread.edit(archived=True, locked=True)
        except discord.NotFound:
            pass  # 이미 삭제된 스레드
        except Exception as e:
            print(f"[종겜] 포스트 닫기/잠그기 실패 (game_id={game_id}): {e}")

    # ---------------- 명령어 ----------------
    @app_commands.command(name="종겜", description="종합게임 모집 일정을 등록합니다.")
    @app_commands.describe(제목="모집 게시물 제목")
    async def create_game(self, interaction: discord.Interaction, 제목: str):
        if interaction.guild is None:
            await interaction.response.send_message("이 명령어는 서버에서만 사용할 수 있어요.", ephemeral=True)
            return

        title_text = 제목.strip()
        if not title_text:
            await interaction.response.send_message("제목을 입력해주세요.", ephemeral=True)
            return

        if self._get_forum(interaction.guild) is None:
            await interaction.response.send_message(
                "종겜 채널이 아직 지정되지 않았거나 문제가 있어요. 관리자에게 `/종겜채널`로 지정해달라고 요청해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(GameCreateModal(self, title_text))

    # ---------------- 종겜 채널 지정 ----------------
    @app_commands.command(name="종겜채널", description="종겜 모집 게시물을 올릴 포럼 채널을 지정합니다. (관리자 전용)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(채널="종겜 모집 게시물을 올릴 포럼 채널 (레이드 채널과 같아도 돼요)")
    async def set_game_channel(self, interaction: discord.Interaction, 채널: discord.ForumChannel):
        if interaction.guild is None:
            await interaction.response.send_message("이 명령어는 서버에서만 사용할 수 있어요.", ephemeral=True)
            return

        me = interaction.guild.me
        if me is not None:
            perms = 채널.permissions_for(me)
            if not (perms.view_channel and perms.send_messages_in_threads and perms.create_public_threads):
                await interaction.response.send_message(
                    "봇에게 이 채널에 대한 권한(채널 보기 / 스레드 생성 / 스레드에서 메시지 보내기)이 부족해요.",
                    ephemeral=True,
                )
                return

        game_channel_manager.set_channel(interaction.guild.id, 채널.id)
        await interaction.response.send_message(
            f"✅ 종겜 채널이 {채널.mention}(으)로 설정됐어요. 서버당 하나의 채널만 지정할 수 있어서, "
            f"이전에 다른 채널이 지정돼 있었다면 이 채널로 교체됐어요.",
            ephemeral=True,
        )

    @app_commands.command(name="종겜채널해제", description="종겜 채널 설정을 해제합니다. (관리자 전용)")
    @app_commands.default_permissions(administrator=True)
    async def remove_game_channel(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("이 명령어는 서버에서만 사용할 수 있어요.", ephemeral=True)
            return
        if game_channel_manager.get_channel(interaction.guild.id) is None:
            await interaction.response.send_message("현재 설정된 종겜 채널이 없어요.", ephemeral=True)
            return
        game_channel_manager.remove_channel(interaction.guild.id)
        await interaction.response.send_message("✅ 종겜 채널 설정이 해제됐어요.", ephemeral=True)

    @app_commands.command(name="종겜채널정보", description="현재 설정된 종겜 채널을 확인합니다.")
    async def game_channel_info(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("이 명령어는 서버에서만 사용할 수 있어요.", ephemeral=True)
            return
        channel_id = game_channel_manager.get_channel(interaction.guild.id)
        if channel_id is None:
            await interaction.response.send_message(
                "현재 설정된 종겜 채널이 없어요. `/종겜채널`로 지정해주세요.", ephemeral=True
            )
            return
        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            game_channel_manager.remove_channel(interaction.guild.id)
            await interaction.response.send_message(
                "설정됐던 종겜 채널이 삭제된 것 같아서 자동으로 지정을 해제했어요. `/종겜채널`로 다시 지정해주세요.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(f"현재 종겜 채널: {channel.mention}", ephemeral=True)

    def _get_forum(self, guild: discord.Guild | None) -> discord.ForumChannel | None:
        """/종겜채널로 지정된 포럼 채널을 돌려줌. 지정이 없거나 깨져 있으면 None (깨진 지정은 해제)."""
        if guild is None:
            return None
        channel_id = game_channel_manager.get_channel(guild.id)
        if channel_id is None:
            return None
        forum = guild.get_channel(channel_id)
        if not isinstance(forum, discord.ForumChannel):
            game_channel_manager.remove_channel(guild.id)
            return None
        return forum

    # ---------------- 게시물 생성 ----------------
    async def create_game_post(self, interaction: discord.Interaction, base: dict, *, link: str, content: str):
        # 모달/버튼 응답 제한(3초) 안에 끝나지 않을 수 있어서, 아직 응답 전이라면 먼저 보류해둠
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        forum = self._get_forum(guild)
        if forum is None:
            await self._final_respond(
                interaction,
                "종겜 채널 설정에 문제가 생겼어요. 관리자에게 `/종겜채널`로 다시 지정해달라고 요청해주세요.",
            )
            return

        entry = {
            "guild_id": guild.id,
            "channel_id": None,  # 스레드 생성 후 채움
            "creator_id": interaction.user.id,
            "title": base["title"],
            "game": base["game"],
            "date": base["date"],
            "hour": base["hour"],
            "minute": base["minute"],
            "capacity": base["capacity"],  # None이면 제한없음
            "link": link,
            "content": content,
            "participants": [],
            "queue": [],
            "reminder_sent": False,
        }

        try:
            result = await forum.create_thread(
                name=_thread_title(entry), embed=_build_embed(entry), view=GamePostView()
            )
        except discord.Forbidden:
            await self._final_respond(interaction, "봇에게 종겜 채널에 글을 작성할 권한이 없어요.")
            return
        except discord.HTTPException as e:
            await self._final_respond(
                interaction,
                f"게시물 생성에 실패했어요: {e}\n"
                f"(포럼 채널에 필수 태그가 지정돼 있으면 지금 버전에서는 자동으로 못 붙여요.)",
            )
            return

        thread = result.thread
        entry["channel_id"] = thread.id
        self.games[str(result.message.id)] = entry
        _save_games(self.games)

        # 멘션이 걸린 첫 댓글이 있어야 디스코드 사이드바에 새 글이 바로 노출됨
        try:
            await thread.send(
                f"모집자 <@{entry['creator_id']}>", allowed_mentions=_ALLOWED_MENTIONS
            )
        except discord.HTTPException:
            pass

        await self._final_respond(interaction, f"✅ 종겜 모집 게시물을 만들었어요: {thread.mention}")

    # ---------------- 참가신청 / 취소 ----------------
    async def handle_apply(self, interaction: discord.Interaction, game_id: str):
        entry = self.games.get(game_id)
        if entry is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return

        where = _add_user(entry, interaction.user.id)
        self.games[game_id] = entry
        _save_games(self.games)
        await self._update_post_embed(game_id)

        if where == "participant":
            msg = "✅ 참가 신청이 완료됐어요!"
        else:
            msg = f"⏳ 자리가 가득 차서 대기열에 등록됐어요. (대기 순번 {len(entry['queue'])}번)"
        await interaction.response.send_message(msg, ephemeral=True)

    async def handle_cancel(self, interaction: discord.Interaction, game_id: str):
        entry = self.games.get(game_id)
        if entry is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return

        where = _find_user(entry, interaction.user.id)
        if where is None:
            await interaction.response.send_message("신청 내역을 찾을 수 없어요.", ephemeral=True)
            return

        if where == "participant":
            entry["participants"].remove(interaction.user.id)
            _fill_from_queue(entry)
            msg = "✅ 참가 신청이 취소됐어요."
        else:
            entry["queue"].remove(interaction.user.id)
            msg = "✅ 대기열에서 취소됐어요."

        self.games[game_id] = entry
        _save_games(self.games)
        await self._update_post_embed(game_id)
        await interaction.response.send_message(msg, ephemeral=True)

    # ---------------- 관리자 강제참여 / 강제취소 ----------------
    async def force_join(self, interaction: discord.Interaction, game_id: str, member: discord.abc.User):
        entry = self.games.get(game_id)
        if entry is None:
            await self._respond(interaction, "모집 정보를 찾을 수 없어요.")
            return
        if member.bot:
            await self._respond(interaction, "봇은 참가시킬 수 없어요.")
            return
        if _find_user(entry, member.id) is not None:
            await self._respond(interaction, f"{member.mention}님은 이미 참가 중이거나 대기 중이에요.")
            return

        where = _add_user(entry, member.id)
        self.games[game_id] = entry
        _save_games(self.games)
        await self._update_post_embed(game_id)

        if where == "participant":
            msg = f"✅ {member.mention}님을 강제참여시켰어요."
        else:
            msg = f"⏳ 자리가 가득 차서 {member.mention}님을 대기열로 강제 등록했어요. (대기 순번 {len(entry['queue'])}번)"
        await self._respond(interaction, msg)

        # "님"을 붙이면 뒤에 오는 이/을 조사가 대상 이름과 무관하게 항상 맞음.
        await self._send_to_thread(
            entry, f"<@{interaction.user.id}>님이 <@{member.id}>님을 강제참여시켰습니다."
        )

    async def force_cancel(self, interaction: discord.Interaction, game_id: str, member: discord.abc.User):
        entry = self.games.get(game_id)
        if entry is None:
            await self._respond(interaction, "모집 정보를 찾을 수 없어요.")
            return

        where = _find_user(entry, member.id)
        if where is None:
            await self._respond(interaction, f"{member.mention}님은 이 모집에 참가 중이 아니에요.")
            return

        if where == "participant":
            entry["participants"].remove(member.id)
            _fill_from_queue(entry)
        else:
            entry["queue"].remove(member.id)

        self.games[game_id] = entry
        _save_games(self.games)
        await self._update_post_embed(game_id)
        await self._respond(interaction, f"✅ {member.mention}님의 신청을 강제로 취소시켰어요.")

    # ---------------- 일정 수정 ----------------
    async def update_game_schedule(
        self, interaction: discord.Interaction, game_id: str, date_str: str, hour: int, minute: int
    ):
        entry = self.games.get(game_id)
        if entry is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return

        # 스레드 이름 변경은 디스코드 제한(10분에 2번)에 걸리면 오래 걸릴 수 있어서 먼저 응답을 보류해둠
        await interaction.response.defer(ephemeral=True)

        entry["date"] = date_str
        entry["hour"] = hour
        entry["minute"] = minute
        entry["reminder_sent"] = False  # 시간이 바뀌었을 수 있으니 알림 상태 초기화

        self.games[game_id] = entry
        _save_games(self.games)
        await self._rename_thread(entry)
        await self._update_post_embed(game_id)
        await interaction.followup.send("✅ 종겜 일정이 수정됐어요.", ephemeral=True)

    # ---------------- 공용 헬퍼 ----------------
    async def _final_respond(self, interaction: discord.Interaction, content: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)

    async def _respond(self, interaction: discord.Interaction, content: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            # 선택지가 있던 원래 메시지를 결과 메시지로 바로 바꿔서(셀렉트 제거) 눌렀던 흔적이 안 남게 함
            await interaction.response.edit_message(content=content, embed=None, view=None)

    async def _send_to_thread(self, entry: dict, text: str) -> None:
        try:
            channel = self.bot.get_channel(entry["channel_id"]) or await self.bot.fetch_channel(entry["channel_id"])
            await channel.send(text, allowed_mentions=_ALLOWED_MENTIONS)
        except Exception as e:
            print(f"[종겜] 스레드 메시지 전송 실패 (channel_id={entry.get('channel_id')}): {e}")

    async def _update_post_embed(self, game_id: str) -> None:
        entry = self.games.get(game_id)
        if entry is None:
            return
        guild = self.bot.get_guild(entry["guild_id"])
        if guild is None:
            return
        channel = guild.get_channel(entry["channel_id"]) or guild.get_thread(entry["channel_id"])
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(entry["channel_id"])
            except Exception:
                return
        try:
            message = await channel.fetch_message(int(game_id))
            await message.edit(embed=_build_embed(entry))
        except Exception as e:
            print(f"[종겜] 게시물 갱신 실패 (game_id={game_id}): {e}")

    async def _rename_thread(self, entry: dict) -> None:
        channel_id = entry.get("channel_id")
        if not channel_id:
            return
        guild = self.bot.get_guild(entry["guild_id"])
        thread = guild.get_thread(channel_id) if guild else None
        if thread is None:
            try:
                thread = await self.bot.fetch_channel(channel_id)
            except Exception:
                return
        new_name = _thread_title(entry)
        if thread.name == new_name:
            return  # 이름이 그대로면 굳이 수정하지 않음 (스레드 이름 변경은 디스코드 속도 제한이 빡빡함)
        try:
            await thread.edit(name=new_name)
        except Exception as e:
            print(f"[종겜] 스레드 이름 변경 실패: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(GameRecruitCog(bot))
    # 영속 View 등록: 봇이 재구동돼도 기존에 올라간 게시물의 버튼이 계속 동작하도록 함
    bot.add_view(GamePostView())
