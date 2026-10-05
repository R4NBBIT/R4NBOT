"""정기모임 모집 (/정모).

/레이드, /종겜과 같은 포럼 모집 게시물이지만 다음이 다르다.
- 일시(시간 포함) / 장소 / 활동(무엇을 하는지)을 입력한다.
- 캐릭터가 없으므로 참가자는 디스코드 계정 태그만으로 표시한다.
- 인원 제한과 대기열이 없다. (참가신청한 사람은 모두 참가자)

게시물은 /정모채널로 지정한 포럼 채널에 올라간다. (/레이드채널, /종겜채널과 같은 채널을 지정해도 된다.)
"""
import json
import os
import re
import tempfile
from datetime import date, datetime, time, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.raid_channel import meet_channel_manager
from cogs.raid_schedule import (
    KST,
    WEEKDAYS_KO,
    _build_hour_options,
    _build_minute_options,
)

# 봇 소스 위치(EGG-BOT/) 기준 절대경로로 고정
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../EGG-BOT
_DATA_DIR = os.path.join(_BASE_DIR, "data")
MEETS_FILE = os.path.join(_DATA_DIR, "meets.json")

# 사용자가 입력한 텍스트(장소/활동 등)가 들어가는 메시지에서 @everyone/@here/역할 멘션이 터지지 않게 함
_ALLOWED_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)


# =========================
# 저장 / 로드 (정모 모집 게시물 데이터, message_id를 키로 사용)
# =========================
def _load_meets() -> dict:
    os.makedirs(_DATA_DIR, exist_ok=True)
    if not os.path.exists(MEETS_FILE):
        return {}
    try:
        with open(MEETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        print(f"[정모] 로드 완료: {len(data)}개 게시물 (경로: {MEETS_FILE})")
        return data
    except Exception as e:
        print(f"[정모] 로드 실패: {e}")
        return {}


def _save_meets(data: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=_DATA_DIR)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, MEETS_FILE)
    except Exception as e:
        print(f"[정모] 저장 실패: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass


# =========================
# 순수 헬퍼 (디스코드 객체 없이 동작)
# =========================
MAX_DAYS_AHEAD = 366  # 오타(예: 2062년) 방지용으로 1년 정도까지만 허용
DATE_FORMAT_HINT = "예: 11/15, 11월 15일, 2026-11-15"


def _parse_date_input(raw: str, today: date | None = None) -> date | None:
    """날짜 텍스트를 해석함. 연도를 생략하면 올해로 보고, 이미 지난 날짜면 내년으로 넘김.

    허용 형식: 2026-11-15 / 2026.11.15 / 2026/11/15 / 11/15 / 11-15 / 11.15 / 11월 15일
    해석할 수 없거나 존재하지 않는 날짜면 None.
    """
    today = today or datetime.now(KST).date()
    text = raw.strip().replace(" ", "")
    m = re.fullmatch(r"(?:(\d{4})[-./년])?(\d{1,2})[-./월](\d{1,2})일?", text)
    if not m:
        return None
    year_text, month, day = m.group(1), int(m.group(2)), int(m.group(3))
    try:
        if year_text:
            return date(int(year_text), month, day)
        d = date(today.year, month, day)
        if d < today:
            d = date(today.year + 1, month, day)
        return d
    except ValueError:
        return None


def _check_date_range(d: date, today: date | None = None) -> str | None:
    """너무 먼 미래면 안내 문구를 돌려줌 (과거 여부는 시간까지 합쳐서 따로 검사)."""
    today = today or datetime.now(KST).date()
    if (d - today).days > MAX_DAYS_AHEAD:
        return "❌ 너무 먼 날짜예요. 1년 이내의 날짜로 적어주세요."
    return None


def _start_dt(entry: dict) -> datetime:
    return datetime.combine(
        date.fromisoformat(entry["date"]),
        time(hour=entry["hour"], minute=entry["minute"]),
        tzinfo=KST,
    )


def _when_text(entry: dict) -> str:
    d = date.fromisoformat(entry["date"])
    return f"{entry['date']}({WEEKDAYS_KO[d.weekday()]}) {entry['hour']:02d}:{entry['minute']:02d}"


def _thread_title(entry: dict) -> str:
    d = date.fromisoformat(entry["date"])
    title = (
        f"{entry['date']}({WEEKDAYS_KO[d.weekday()]}) {entry['hour']:02d}:{entry['minute']:02d} "
        f"{entry['place']} - {entry['title']}"
    )
    return title if len(title) <= 100 else title[:99] + "…"


def _add_user(entry: dict, user_id: int) -> bool:
    """참가자로 추가. 이미 참가 중이면 False."""
    if user_id in entry["participants"]:
        return False
    entry["participants"].append(user_id)
    return True


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
    embed.add_field(
        name="🗓️ 일시",
        value=f"{entry['date']}({WEEKDAYS_KO[d.weekday()]}) {entry['hour']:02d}:{entry['minute']:02d}",
        inline=False,
    )
    embed.add_field(name="📍 장소", value=entry["place"], inline=False)
    embed.add_field(name="🎯 활동", value=entry["activity"], inline=False)
    embed.add_field(name="👑 모집자", value=f"<@{entry['creator_id']}>", inline=False)

    participants = entry["participants"]
    _add_mention_fields(embed, f"👥 참가자 ({len(participants)}명)", participants)
    return embed


def _can_manage(interaction: discord.Interaction, entry: dict) -> bool:
    is_creator = interaction.user.id == entry.get("creator_id")
    is_admin = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator
    return is_creator or is_admin


# =========================
# 모달: 날짜 / 시 / 분 / 장소 / 활동 (생성용)
# =========================
class MeetCreateModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "MeetupCog",
        title_text: str,
        *,
        defaults: dict | None = None,
        origin_message: discord.Message | None = None,
    ):
        super().__init__(title="정모 일정 등록")
        self.cog = cog
        self.title_text = title_text
        self.origin_message = origin_message
        d = defaults or {}

        self.date_input = discord.ui.TextInput(
            placeholder=DATE_FORMAT_HINT, default=d.get("date_text") or None, max_length=20
        )
        self.hour_select = discord.ui.Select(placeholder="시 선택 (00-23)", options=_build_hour_options(d.get("hour")))
        self.minute_select = discord.ui.Select(
            placeholder="분 선택 (10분 단위)", options=_build_minute_options(d.get("minute"))
        )
        self.place_input = discord.ui.TextInput(
            placeholder="예: 강남역 2번 출구 앞 / 디스코드 음성채널", default=d.get("place") or None, max_length=100
        )
        self.activity_input = discord.ui.TextInput(
            placeholder="예: 저녁 먹고 보드게임", default=d.get("activity") or None, max_length=100
        )

        self.add_item(discord.ui.Label(text="날짜", component=self.date_input))
        self.add_item(discord.ui.Label(text="시", component=self.hour_select))
        self.add_item(discord.ui.Label(text="분", component=self.minute_select))
        self.add_item(discord.ui.Label(text="장소", component=self.place_input))
        self.add_item(
            discord.ui.Label(
                text="활동 (무엇을 하나요?)",
                component=self.activity_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        if self.origin_message is not None:
            try:
                await self.origin_message.edit(view=None)
            except Exception:
                pass

        date_text = self.date_input.value
        hour = int(self.hour_select.values[0])
        minute = int(self.minute_select.values[0])
        place = self.place_input.value.strip()
        activity = self.activity_input.value.strip()

        defaults = {
            "date_text": date_text, "hour": hour, "minute": minute,
            "place": self.place_input.value, "activity": self.activity_input.value,
        }

        async def _retry(message: str) -> None:
            await interaction.response.send_message(
                message, view=_MeetRetryView(self.cog, self.title_text, defaults), ephemeral=True
            )

        parsed = _parse_date_input(date_text)
        if parsed is None:
            await _retry(f"❌ 날짜를 읽을 수 없어요. 이렇게 적어주세요. ({DATE_FORMAT_HINT})")
            return
        range_error = _check_date_range(parsed)
        if range_error:
            await _retry(range_error)
            return
        date_str = parsed.isoformat()

        target_dt = datetime.combine(parsed, time(hour=hour, minute=minute), tzinfo=KST)
        if target_dt <= datetime.now(KST):
            await _retry("❌ 이미 지난 시간으로는 정모 일정을 등록할 수 없어요. 다른 날짜/시간으로 적어주세요.")
            return

        if not place:
            await _retry("❌ 장소를 입력해주세요.")
            return
        if not activity:
            await _retry("❌ 활동(무엇을 하는지)을 입력해주세요.")
            return

        base = {
            "title": self.title_text, "date": date_str, "hour": hour, "minute": minute,
            "place": place, "activity": activity,
        }
        await interaction.response.send_message(
            f"정모 일정이 확인됐어요. ({_when_text(base)}) 설명을 추가하시겠어요? (안 넣어도 괜찮아요)",
            view=_MeetDescriptionStepView(self.cog, base),
            ephemeral=True,
        )


class _MeetRetryView(discord.ui.View):
    """입력값이 잘못됐을 때, 입력했던 값을 유지한 채 모달을 다시 띄우는 버튼."""

    def __init__(self, cog: "MeetupCog", title_text: str, defaults: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.title_text = title_text
        self.defaults = defaults

    @discord.ui.button(label="다시 입력", style=discord.ButtonStyle.blurple)
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = MeetCreateModal(
            self.cog, self.title_text, defaults=self.defaults, origin_message=interaction.message
        )
        await interaction.response.send_modal(modal)


# =========================
# 생성 2단계: 추가 설명 (선택)
# =========================
class _MeetDescriptionStepView(discord.ui.View):
    def __init__(self, cog: "MeetupCog", base: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.base = base

    @discord.ui.button(label="설명 작성하기", style=discord.ButtonStyle.blurple)
    async def write_description(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = MeetDescriptionModal(self.cog, self.base, origin_message=interaction.message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="설명 없이 게시", style=discord.ButtonStyle.gray)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="게시물을 생성하고 있어요...", view=None)
        await self.cog.create_meet_post(interaction, self.base, content="")


class MeetDescriptionModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "MeetupCog",
        base: dict,
        *,
        origin_message: discord.Message | None = None,
    ):
        super().__init__(title="정모 추가 설명 작성")
        self.cog = cog
        self.base = base
        self.origin_message = origin_message
        self.content_input = discord.ui.TextInput(
            label="추가 설명 (선택)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
        )
        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction):
        if self.origin_message is not None:
            try:
                await self.origin_message.edit(view=None)
            except Exception:
                pass

        await self.cog.create_meet_post(
            interaction,
            self.base,
            content=self.content_input.value.strip(),
        )


# =========================
# 관리: 일정(날짜/시/분) 수정
# =========================
class MeetRescheduleModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "MeetupCog",
        meet_id: str,
        defaults: dict,
        origin_message: discord.Message | None = None,
    ):
        super().__init__(title="정모 일정 수정")
        self.cog = cog
        self.meet_id = meet_id
        self.origin_message = origin_message

        self.date_input = discord.ui.TextInput(
            placeholder=DATE_FORMAT_HINT, default=defaults.get("date_text") or defaults.get("date") or None, max_length=20
        )
        self.hour_select = discord.ui.Select(
            placeholder="시 선택 (00-23)", options=_build_hour_options(defaults.get("hour"))
        )
        self.minute_select = discord.ui.Select(
            placeholder="분 선택 (10분 단위)", options=_build_minute_options(defaults.get("minute"))
        )
        self.add_item(discord.ui.Label(text="날짜", component=self.date_input))
        self.add_item(discord.ui.Label(text="시", component=self.hour_select))
        self.add_item(discord.ui.Label(text="분", component=self.minute_select))

    async def on_submit(self, interaction: discord.Interaction):
        if self.origin_message is not None:
            try:
                await self.origin_message.edit(view=None)
            except Exception:
                pass

        date_text = self.date_input.value
        hour = int(self.hour_select.values[0])
        minute = int(self.minute_select.values[0])
        defaults = {"date_text": date_text, "hour": hour, "minute": minute}

        async def _retry(message: str) -> None:
            await interaction.response.send_message(
                message, view=_MeetRescheduleRetryView(self.cog, self.meet_id, defaults), ephemeral=True
            )

        parsed = _parse_date_input(date_text)
        if parsed is None:
            await _retry(f"❌ 날짜를 읽을 수 없어요. 이렇게 적어주세요. ({DATE_FORMAT_HINT})")
            return
        range_error = _check_date_range(parsed)
        if range_error:
            await _retry(range_error)
            return
        date_str = parsed.isoformat()

        target_dt = datetime.combine(parsed, time(hour=hour, minute=minute), tzinfo=KST)
        if target_dt <= datetime.now(KST):
            await _retry("❌ 이미 지난 시간으로는 일정을 수정할 수 없어요. 다른 날짜/시간으로 적어주세요.")
            return

        await self.cog.update_meet_schedule(interaction, self.meet_id, date_str, hour, minute)


class _MeetRescheduleRetryView(discord.ui.View):
    def __init__(self, cog: "MeetupCog", meet_id: str, defaults: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.meet_id = meet_id
        self.defaults = defaults

    @discord.ui.button(label="다시 입력", style=discord.ButtonStyle.blurple)
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = MeetRescheduleModal(self.cog, self.meet_id, self.defaults, origin_message=interaction.message)
        await interaction.response.send_modal(modal)


# =========================
# 관리: 제목 / 장소 / 활동 / 추가 설명 수정
# =========================
class MeetInfoEditModal(discord.ui.Modal):
    def __init__(self, cog: "MeetupCog", meet_id: str):
        super().__init__(title="정모 정보 수정")
        self.cog = cog
        self.meet_id = meet_id
        entry = cog.meets.get(meet_id) or {}

        self.title_input = discord.ui.TextInput(
            label="제목", default=entry.get("title", ""), max_length=100
        )
        self.place_input = discord.ui.TextInput(
            label="장소", default=entry.get("place", ""), max_length=100
        )
        self.activity_input = discord.ui.TextInput(
            label="활동 (무엇을 하나요?)", default=entry.get("activity", ""), max_length=100
        )
        self.content_input = discord.ui.TextInput(
            label="추가 설명 (선택)",
            style=discord.TextStyle.paragraph,
            default=entry.get("content", ""),
            required=False,
            max_length=1000,
        )
        for item in (self.title_input, self.place_input, self.activity_input, self.content_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        entry = self.cog.meets.get(self.meet_id)
        if entry is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return

        title = self.title_input.value.strip()
        place = self.place_input.value.strip()
        activity = self.activity_input.value.strip()
        if not title or not place or not activity:
            await interaction.response.send_message("❌ 제목, 장소, 활동은 비워둘 수 없어요.", ephemeral=True)
            return

        # 스레드 이름 변경은 디스코드 제한(10분에 2번)에 걸리면 오래 걸릴 수 있어서 먼저 응답을 보류해둠
        await interaction.response.defer(ephemeral=True)

        entry["title"] = title
        entry["place"] = place
        entry["activity"] = activity
        entry["content"] = self.content_input.value.strip()

        self.cog.meets[self.meet_id] = entry
        _save_meets(self.cog.meets)
        await self.cog._rename_thread(entry)
        await self.cog._update_post_embed(self.meet_id)
        await interaction.followup.send("✅ 정모 정보가 수정됐어요.", ephemeral=True)


# =========================
# 관리: 참가자 태그
# =========================
class MeetTagModal(discord.ui.Modal):
    def __init__(self, cog: "MeetupCog", meet_id: str):
        super().__init__(title="참가자 태그")
        self.cog = cog
        self.meet_id = meet_id
        self.message_input = discord.ui.TextInput(
            label="같이 보낼 메시지 (선택)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        entry = self.cog.meets.get(self.meet_id)
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
class MeetManagePanelView(discord.ui.View):
    def __init__(self, cog: "MeetupCog", meet_id: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.meet_id = meet_id

    @discord.ui.button(label="정보수정", style=discord.ButtonStyle.blurple)
    async def edit_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.cog.meets.get(self.meet_id) is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return
        await interaction.response.send_modal(MeetInfoEditModal(self.cog, self.meet_id))

    @discord.ui.button(label="일정수정", style=discord.ButtonStyle.blurple)
    async def edit_schedule(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = self.cog.meets.get(self.meet_id)
        if entry is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return
        modal = MeetRescheduleModal(
            self.cog, self.meet_id,
            {"date": entry["date"], "hour": entry["hour"], "minute": entry["minute"]},
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="강제참여", style=discord.ButtonStyle.gray)
    async def force_join(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "강제 참여시킬 멤버를 아래에서 검색해주세요.",
            view=_MeetMemberPickView(self.cog, self.meet_id, mode="join"),
            ephemeral=True,
        )

    @discord.ui.button(label="강제취소", style=discord.ButtonStyle.gray)
    async def force_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "참가를 강제로 취소시킬 멤버를 아래에서 검색해주세요.",
            view=_MeetMemberPickView(self.cog, self.meet_id, mode="cancel"),
            ephemeral=True,
        )

    @discord.ui.button(label="삭제", style=discord.ButtonStyle.red)
    async def delete_post(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "⚠️ 정말로 이 정모 게시물을 삭제하시겠습니까? 되돌릴 수 없어요.",
            view=_MeetDeleteConfirmView(self.cog, self.meet_id),
            ephemeral=True,
        )


class _MeetMemberPickView(discord.ui.View):
    """강제참여 / 강제취소 대상 멤버를 고르는 뷰."""

    def __init__(self, cog: "MeetupCog", meet_id: str, *, mode: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.meet_id = meet_id
        self.mode = mode  # "join" | "cancel"

        self.member_select = discord.ui.UserSelect(placeholder="멤버 검색", min_values=1, max_values=1)
        self.member_select.callback = self._on_selected
        self.add_item(self.member_select)

    async def _on_selected(self, interaction: discord.Interaction):
        member = self.member_select.values[0]
        if self.mode == "join":
            await self.cog.force_join(interaction, self.meet_id, member)
        else:
            await self.cog.force_cancel(interaction, self.meet_id, member)


class _MeetDeleteConfirmView(discord.ui.View):
    def __init__(self, cog: "MeetupCog", meet_id: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.meet_id = meet_id

    @discord.ui.button(label="확인 (삭제)", style=discord.ButtonStyle.red)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = self.cog.meets.pop(self.meet_id, None)
        _save_meets(self.cog.meets)
        if entry is None:
            await interaction.response.edit_message(content="이미 삭제된 게시물이에요.", view=None)
            return
        await interaction.response.edit_message(content="🗑️ 정모 게시물을 삭제했어요.", view=None)
        try:
            channel = self.cog.bot.get_channel(entry["channel_id"]) or await self.cog.bot.fetch_channel(entry["channel_id"])
            await channel.delete()
        except Exception as e:
            print(f"[정모] 스레드 삭제 실패: {e}")

    @discord.ui.button(label="취소", style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="삭제를 취소했어요.", view=None)


# =========================
# 게시물에 붙는 버튼 (영속 View - 봇 재구동 후에도 동작하도록 custom_id 고정)
# =========================
class MeetPostView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _context(self, interaction: discord.Interaction):
        """(cog, meet_id, entry)를 돌려줌. 찾을 수 없으면 안내 메시지를 보내고 None."""
        cog = interaction.client.get_cog("MeetupCog")
        meet_id = str(interaction.message.id)
        entry = cog.meets.get(meet_id) if cog else None
        if not cog or entry is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return None
        return cog, meet_id, entry

    @discord.ui.button(label="참가신청", style=discord.ButtonStyle.green, custom_id="meet_apply")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await self._context(interaction)
        if ctx is None:
            return
        cog, meet_id, entry = ctx

        if interaction.user.id in entry["participants"]:
            await interaction.response.send_message("이미 이 모집에 신청하셨어요.", ephemeral=True)
            return
        await cog.handle_apply(interaction, meet_id)

    @discord.ui.button(label="참가취소", style=discord.ButtonStyle.red, custom_id="meet_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await self._context(interaction)
        if ctx is None:
            return
        cog, meet_id, _ = ctx
        await cog.handle_cancel(interaction, meet_id)

    @discord.ui.button(label="참가자 태그", style=discord.ButtonStyle.gray, custom_id="meet_tag_participants")
    async def tag_participants(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await self._context(interaction)
        if ctx is None:
            return
        cog, meet_id, entry = ctx

        is_participant = interaction.user.id in entry["participants"]
        if not (_can_manage(interaction, entry) or is_participant):
            await interaction.response.send_message(
                "참가자 태그는 작성자, 참가자, 서버 관리자만 사용할 수 있어요.",
                ephemeral=True,
            )
            return
        if not entry["participants"]:
            await interaction.response.send_message(
                "태그할 참가자가 없어요.", ephemeral=True
            )
            return

        await interaction.response.send_modal(MeetTagModal(cog, meet_id))

    @discord.ui.button(label="⚙️ 관리", style=discord.ButtonStyle.gray, custom_id="meet_manage")
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await self._context(interaction)
        if ctx is None:
            return
        cog, meet_id, entry = ctx

        if not _can_manage(interaction, entry):
            await interaction.response.send_message(
                "이 모집을 관리할 권한이 없어요. (서버 관리자 또는 작성자만 가능해요)", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "이 모집을 관리해요. (나만 보여요)", view=MeetManagePanelView(cog, meet_id), ephemeral=True
        )


# =========================
# Cog
# =========================
class MeetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.meets: dict = _load_meets()

    async def cog_load(self) -> None:
        self.cleanup_loop.start()

    def cog_unload(self) -> None:
        self.cleanup_loop.cancel()

    # ---------------- 시작 10분 전 알림 / 시작 30분 후 마감 ----------------
    @tasks.loop(minutes=1)
    async def cleanup_loop(self) -> None:
        now = datetime.now(KST)
        for meet_id, entry in list(self.meets.items()):
            try:
                start_dt = _start_dt(entry)
                remaining = start_dt - now
                if not entry.get("reminder_sent") and timedelta(0) < remaining <= timedelta(minutes=10):
                    await self._send_start_reminder(meet_id, entry)
                if now >= start_dt + timedelta(minutes=30):
                    await self._close_and_lock(meet_id, entry)
            except Exception as e:
                print(f"[정모] 정리 작업 실패 (meet_id={meet_id}): {e}")

    @cleanup_loop.before_loop
    async def before_cleanup_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _send_start_reminder(self, meet_id: str, entry: dict) -> None:
        # 전송 실패해도 매분 재시도로 스팸이 되지 않게, 먼저 플래그부터 저장함
        entry["reminder_sent"] = True
        self.meets[meet_id] = entry
        _save_meets(self.meets)

        text = f"⏰ 곧 시작! 약 10분 뒤에 **{entry['place']}**에서 **{entry['activity']}** 정모가 시작돼요."
        if entry["participants"]:
            text += "\n" + " ".join(f"<@{uid}>" for uid in entry["participants"])
        await self._send_to_thread(entry, text)

    async def _close_and_lock(self, meet_id: str, entry: dict) -> None:
        # 처리 실패해도 데이터는 먼저 지워서 무한 재시도로 매분 실패 로그가 쌓이지 않게 함
        self.meets.pop(meet_id, None)
        _save_meets(self.meets)

        try:
            thread = self.bot.get_channel(entry["channel_id"]) or await self.bot.fetch_channel(entry["channel_id"])
        except discord.NotFound:
            return
        except Exception as e:
            print(f"[정모] 스레드 조회 실패 (meet_id={meet_id}): {e}")
            return

        try:
            await thread.send("🔒 정모 시작 후 30분이 지나서 이 게시물을 마감했어요.")
        except Exception as e:
            print(f"[정모] 마감 메시지 전송 실패 (meet_id={meet_id}): {e}")

        try:
            await thread.edit(archived=True, locked=True)
        except discord.NotFound:
            pass  # 이미 삭제된 스레드
        except Exception as e:
            print(f"[정모] 포스트 닫기/잠그기 실패 (meet_id={meet_id}): {e}")

    # ---------------- 명령어 ----------------
    @app_commands.command(name="정모", description="정기모임 모집 일정을 등록합니다.")
    @app_commands.describe(제목="모집 게시물 제목")
    async def create_meet(self, interaction: discord.Interaction, 제목: str):
        if interaction.guild is None:
            await interaction.response.send_message("이 명령어는 서버에서만 사용할 수 있어요.", ephemeral=True)
            return

        title_text = 제목.strip()
        if not title_text:
            await interaction.response.send_message("제목을 입력해주세요.", ephemeral=True)
            return

        if self._get_forum(interaction.guild) is None:
            await interaction.response.send_message(
                "정모 채널이 아직 지정되지 않았거나 문제가 있어요. 관리자에게 `/정모채널`로 지정해달라고 요청해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(MeetCreateModal(self, title_text))

    # ---------------- 정모 채널 지정 ----------------
    @app_commands.command(name="정모채널", description="정모 모집 게시물을 올릴 포럼 채널을 지정합니다. (관리자 전용)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(채널="정모 모집 게시물을 올릴 포럼 채널 (다른 모집 채널과 같아도 돼요)")
    async def set_meet_channel(self, interaction: discord.Interaction, 채널: discord.ForumChannel):
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

        meet_channel_manager.set_channel(interaction.guild.id, 채널.id)
        await interaction.response.send_message(
            f"✅ 정모 채널이 {채널.mention}(으)로 설정됐어요. 서버당 하나의 채널만 지정할 수 있어서, "
            f"이전에 다른 채널이 지정돼 있었다면 이 채널로 교체됐어요.",
            ephemeral=True,
        )

    @app_commands.command(name="정모채널해제", description="정모 채널 설정을 해제합니다. (관리자 전용)")
    @app_commands.default_permissions(administrator=True)
    async def remove_meet_channel(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("이 명령어는 서버에서만 사용할 수 있어요.", ephemeral=True)
            return
        if meet_channel_manager.get_channel(interaction.guild.id) is None:
            await interaction.response.send_message("현재 설정된 정모 채널이 없어요.", ephemeral=True)
            return
        meet_channel_manager.remove_channel(interaction.guild.id)
        await interaction.response.send_message("✅ 정모 채널 설정이 해제됐어요.", ephemeral=True)

    @app_commands.command(name="정모채널정보", description="현재 설정된 정모 채널을 확인합니다.")
    async def meet_channel_info(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("이 명령어는 서버에서만 사용할 수 있어요.", ephemeral=True)
            return
        channel_id = meet_channel_manager.get_channel(interaction.guild.id)
        if channel_id is None:
            await interaction.response.send_message(
                "현재 설정된 정모 채널이 없어요. `/정모채널`로 지정해주세요.", ephemeral=True
            )
            return
        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            meet_channel_manager.remove_channel(interaction.guild.id)
            await interaction.response.send_message(
                "설정됐던 정모 채널이 삭제된 것 같아서 자동으로 지정을 해제했어요. `/정모채널`로 다시 지정해주세요.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(f"현재 정모 채널: {channel.mention}", ephemeral=True)

    def _get_forum(self, guild: discord.Guild | None) -> discord.ForumChannel | None:
        """/정모채널로 지정된 포럼 채널을 돌려줌. 지정이 없거나 깨져 있으면 None (깨진 지정은 해제)."""
        if guild is None:
            return None
        channel_id = meet_channel_manager.get_channel(guild.id)
        if channel_id is None:
            return None
        forum = guild.get_channel(channel_id)
        if not isinstance(forum, discord.ForumChannel):
            meet_channel_manager.remove_channel(guild.id)
            return None
        return forum

    # ---------------- 게시물 생성 ----------------
    async def create_meet_post(self, interaction: discord.Interaction, base: dict, *, content: str):
        # 모달/버튼 응답 제한(3초) 안에 끝나지 않을 수 있어서, 아직 응답 전이라면 먼저 보류해둠
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        forum = self._get_forum(guild)
        if forum is None:
            await self._final_respond(
                interaction,
                "정모 채널 설정에 문제가 생겼어요. 관리자에게 `/정모채널`로 다시 지정해달라고 요청해주세요.",
            )
            return

        entry = {
            "guild_id": guild.id,
            "channel_id": None,  # 스레드 생성 후 채움
            "creator_id": interaction.user.id,
            "title": base["title"],
            "place": base["place"],
            "activity": base["activity"],
            "date": base["date"],
            "hour": base["hour"],
            "minute": base["minute"],
            "content": content,
            "participants": [],
            "reminder_sent": False,
        }

        try:
            result = await forum.create_thread(
                name=_thread_title(entry), embed=_build_embed(entry), view=MeetPostView()
            )
        except discord.Forbidden:
            await self._final_respond(interaction, "봇에게 정모 채널에 글을 작성할 권한이 없어요.")
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
        self.meets[str(result.message.id)] = entry
        _save_meets(self.meets)

        # 멘션이 걸린 첫 댓글이 있어야 디스코드 사이드바에 새 글이 바로 노출됨
        try:
            await thread.send(
                f"모집자 <@{entry['creator_id']}>", allowed_mentions=_ALLOWED_MENTIONS
            )
        except discord.HTTPException:
            pass

        await self._final_respond(interaction, f"✅ 정모 모집 게시물을 만들었어요: {thread.mention}")

    # ---------------- 참가신청 / 취소 ----------------
    async def handle_apply(self, interaction: discord.Interaction, meet_id: str):
        entry = self.meets.get(meet_id)
        if entry is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return

        if not _add_user(entry, interaction.user.id):
            await interaction.response.send_message("이미 이 모집에 신청하셨어요.", ephemeral=True)
            return
        self.meets[meet_id] = entry
        _save_meets(self.meets)
        await self._update_post_embed(meet_id)
        await interaction.response.send_message("✅ 참가 신청이 완료됐어요!", ephemeral=True)

    async def handle_cancel(self, interaction: discord.Interaction, meet_id: str):
        entry = self.meets.get(meet_id)
        if entry is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return

        if interaction.user.id not in entry["participants"]:
            await interaction.response.send_message("신청 내역을 찾을 수 없어요.", ephemeral=True)
            return

        entry["participants"].remove(interaction.user.id)
        self.meets[meet_id] = entry
        _save_meets(self.meets)
        await self._update_post_embed(meet_id)
        await interaction.response.send_message("✅ 참가 신청이 취소됐어요.", ephemeral=True)

    # ---------------- 관리자 강제참여 / 강제취소 ----------------
    async def force_join(self, interaction: discord.Interaction, meet_id: str, member: discord.abc.User):
        entry = self.meets.get(meet_id)
        if entry is None:
            await self._respond(interaction, "모집 정보를 찾을 수 없어요.")
            return
        if member.bot:
            await self._respond(interaction, "봇은 참가시킬 수 없어요.")
            return
        if not _add_user(entry, member.id):
            await self._respond(interaction, f"{member.mention}님은 이미 참가 중이에요.")
            return

        self.meets[meet_id] = entry
        _save_meets(self.meets)
        await self._update_post_embed(meet_id)
        await self._respond(interaction, f"✅ {member.mention}님을 강제참여시켰어요.")

        # "님"을 붙이면 뒤에 오는 이/을 조사가 대상 이름과 무관하게 항상 맞음.
        await self._send_to_thread(
            entry, f"<@{interaction.user.id}>님이 <@{member.id}>님을 강제참여시켰습니다."
        )

    async def force_cancel(self, interaction: discord.Interaction, meet_id: str, member: discord.abc.User):
        entry = self.meets.get(meet_id)
        if entry is None:
            await self._respond(interaction, "모집 정보를 찾을 수 없어요.")
            return

        if member.id not in entry["participants"]:
            await self._respond(interaction, f"{member.mention}님은 이 모집에 참가 중이 아니에요.")
            return

        entry["participants"].remove(member.id)

        self.meets[meet_id] = entry
        _save_meets(self.meets)
        await self._update_post_embed(meet_id)
        await self._respond(interaction, f"✅ {member.mention}님의 신청을 강제로 취소시켰어요.")

    # ---------------- 일정 수정 ----------------
    async def update_meet_schedule(
        self, interaction: discord.Interaction, meet_id: str, date_str: str, hour: int, minute: int
    ):
        entry = self.meets.get(meet_id)
        if entry is None:
            await interaction.response.send_message("모집 정보를 찾을 수 없어요.", ephemeral=True)
            return

        # 스레드 이름 변경은 디스코드 제한(10분에 2번)에 걸리면 오래 걸릴 수 있어서 먼저 응답을 보류해둠
        await interaction.response.defer(ephemeral=True)

        entry["date"] = date_str
        entry["hour"] = hour
        entry["minute"] = minute
        entry["reminder_sent"] = False  # 시간이 바뀌었을 수 있으니 알림 상태 초기화

        self.meets[meet_id] = entry
        _save_meets(self.meets)
        await self._rename_thread(entry)
        await self._update_post_embed(meet_id)
        await interaction.followup.send("✅ 정모 일정이 수정됐어요.", ephemeral=True)

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
            print(f"[정모] 스레드 메시지 전송 실패 (channel_id={entry.get('channel_id')}): {e}")

    async def _update_post_embed(self, meet_id: str) -> None:
        entry = self.meets.get(meet_id)
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
            message = await channel.fetch_message(int(meet_id))
            await message.edit(embed=_build_embed(entry))
        except Exception as e:
            print(f"[정모] 게시물 갱신 실패 (meet_id={meet_id}): {e}")

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
            print(f"[정모] 스레드 이름 변경 실패: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(MeetupCog(bot))
    # 영속 View 등록: 봇이 재구동돼도 기존에 올라간 게시물의 버튼이 계속 동작하도록 함
    bot.add_view(MeetPostView())
