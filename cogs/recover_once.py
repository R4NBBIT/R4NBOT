"""1회용 레이드 데이터 복구 cog.

디스호스트 계정 이전 과정에서 data/*.json이 전부 날아간 뒤, 이미 올라와 있는 레이드 모집
포스트(아직 아카이브 안 된 스레드)를 봇이 시작될 때 자동으로 스캔해서 data/raids.json을
다시 만들어줍니다. 임베드에 적힌 날짜/시간/생성자/참가자/정원/입장레벨을 그대로 읽어서
복구하고, 일정이 이미 지난 글이나 이미 raids.json에 들어있는 글은 건드리지 않습니다.

한 번 실행되면 data/.raids_recovered 마커 파일을 남겨서, 봇이 재시작돼도 다시 실행되지
않습니다. 복구 결과는 콘솔 로그에 출력됩니다. 복구가 끝난 걸 확인한 뒤에는 이 파일과
core/bot_core.py의 "cogs.recover_once" 등록 줄을 지워도 됩니다 (평소 봇 기능과는 무관).
"""
import json
import os
import re
import tempfile
from datetime import date, datetime

from discord.ext import commands

from core.raid_data import KST, load_raid_data

# ---------------- 대상 서버/채널 ----------------
GUILD_ID = 1475850778634616874
RAID_CHANNEL_ID = 1520722582826127433
# ------------------------------------------------

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_BASE_DIR, "data")
RAIDS_FILE = os.path.join(_DATA_DIR, "raids.json")
MARKER_FILE = os.path.join(_DATA_DIR, ".raids_recovered")

DEFAULT_DEALER_SLOTS = 3
DEFAULT_SUPPORT_SLOTS = 1
OTHER_RAID_LABEL = "기타"

TITLE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\([^)]+\)\s+(?P<hour>\d{2}):(?P<minute>\d{2})\s+"
    r"(?P<raiddiff>.+?)\s+-\s+(?P<title>.+)$"
)
SLOTS_RE = re.compile(r"\((\d+)/(\d+)\)")
MENTION_RE = re.compile(r"^<@!?(\d+)>$")


def _load_raids() -> dict:
    if not os.path.exists(RAIDS_FILE):
        return {}
    try:
        with open(RAIDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[레이드복구] 기존 raids.json 로드 실패 (무시하고 빈 상태로 시작): {e}")
        return {}


def _save_raids(data: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=_DATA_DIR)
    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, RAIDS_FILE)


def _split_raid_diff(raiddiff: str, known_raid_names: list[str]):
    if raiddiff == OTHER_RAID_LABEL:
        return OTHER_RAID_LABEL, ""
    if raiddiff.startswith(OTHER_RAID_LABEL + " "):
        # "기타"는 raid_data에 등록된 조합이 아니라서, 난이도가 붙어있어도
        # (예: "기타 익스트림 나이트메어") 그대로 기타+나머지로 처리
        return OTHER_RAID_LABEL, raiddiff[len(OTHER_RAID_LABEL) + 1:].strip()
    for name in sorted(known_raid_names, key=len, reverse=True):
        if raiddiff == name:
            return name, ""
        if raiddiff.startswith(name + " "):
            return name, raiddiff[len(name) + 1:].strip()
    return None


def _parse_participant_field(value: str) -> list[dict]:
    if not value or value.strip() == "아직 없음":
        return []
    lines = [l for l in value.split("\n") if l.strip() != ""]
    participants = []
    i = 0
    while i < len(lines):
        who_line = lines[i].strip()
        info_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
        i += 2

        is_merc = who_line == "용병"
        user_id = None
        if not is_merc:
            m = MENTION_RE.match(who_line)
            if m:
                user_id = int(m.group(1))
            else:
                continue

        parts = [p.strip() for p in info_line.split(" · ")]
        if not parts:
            continue
        character = parts[0]
        level = None
        cls = None
        combat_power = None
        for p in parts[1:]:
            if p.startswith("Lv "):
                try:
                    level = float(p[3:].replace(",", "").strip())
                except ValueError:
                    pass
            elif p.startswith("⚡"):
                try:
                    combat_power = float(p.replace("⚡", "").replace(",", "").strip())
                except ValueError:
                    pass
            else:
                cls = p

        p_entry = {"character": character, "is_mercenary": is_merc}
        if user_id is not None:
            p_entry["user_id"] = user_id
        if level is not None:
            p_entry["level"] = level
        if cls:
            p_entry["class"] = cls
        if combat_power is not None:
            p_entry["combat_power"] = combat_power
        participants.append(p_entry)
    return participants


class RecoverOnceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        if os.path.exists(MARKER_FILE):
            return
        try:
            await self._recover()
        except Exception as e:
            print(f"[레이드복구] 복구 중 예외 발생: {e}")
        finally:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with open(MARKER_FILE, "w", encoding="utf-8") as f:
                f.write(datetime.now(KST).isoformat())

    async def _recover(self):
        raid_data = load_raid_data()
        known_raid_names = sorted({k[0] for k in raid_data.keys()})

        existing = _load_raids()
        recovered = 0
        skipped_past = 0
        skipped_existing = 0
        failed = []

        guild = self.bot.get_guild(GUILD_ID) or await self.bot.fetch_guild(GUILD_ID)
        channel = guild.get_channel(RAID_CHANNEL_ID) or await self.bot.fetch_channel(RAID_CHANNEL_ID)

        threads = list(channel.threads)
        print(f"[레이드복구] 열려있는 스레드 {len(threads)}개 발견")

        now = datetime.now(KST)

        for thread in threads:
            raid_id = str(thread.id)
            if raid_id in existing:
                skipped_existing += 1
                continue

            try:
                starter = thread.starter_message or await thread.fetch_message(thread.id)
            except Exception as e:
                failed.append(f"{thread.name} (시작 메시지 조회 실패: {e})")
                continue

            if not starter.embeds:
                failed.append(f"{thread.name} (임베드 없음)")
                continue
            embed = starter.embeds[0]

            m = TITLE_RE.match(embed.title or "")
            if not m:
                failed.append(f"{thread.name} (제목 형식 파싱 실패: {embed.title!r})")
                continue

            date_str = m.group("date")
            hour = int(m.group("hour"))
            minute = int(m.group("minute"))
            title_text = m.group("title")
            raiddiff = m.group("raiddiff")

            split = _split_raid_diff(raiddiff, known_raid_names)
            if split is None:
                failed.append(f"{thread.name} (레이드/난이도 매칭 실패: {raiddiff!r})")
                continue
            raid, diff = split

            try:
                raid_dt = datetime.combine(
                    date.fromisoformat(date_str), datetime.min.time().replace(hour=hour, minute=minute)
                ).replace(tzinfo=KST)
            except Exception as e:
                failed.append(f"{thread.name} (날짜 파싱 실패: {e})")
                continue

            if raid_dt < now:
                skipped_past += 1
                continue

            fields = {f.name: f.value for f in embed.fields}

            creator_id = None
            creator_field = fields.get("👑 공격대 생성자", "")
            cm = MENTION_RE.match(creator_field.strip())
            if cm:
                creator_id = int(cm.group(1))

            dealer_slots = DEFAULT_DEALER_SLOTS
            support_slots = DEFAULT_SUPPORT_SLOTS
            dealer_list, support_list = [], []
            for fname, fvalue in fields.items():
                if fname.startswith("⚔️ 딜러"):
                    sm = SLOTS_RE.search(fname)
                    if sm:
                        dealer_slots = int(sm.group(2))
                    dealer_list = _parse_participant_field(fvalue)
                elif fname.startswith("🛡️ 서포터"):
                    sm = SLOTS_RE.search(fname)
                    if sm:
                        support_slots = int(sm.group(2))
                    support_list = _parse_participant_field(fvalue)

            min_level = 0
            min_level_field = fields.get("🔒 입장레벨")
            if min_level_field:
                try:
                    min_level = float(min_level_field.replace(",", "").strip())
                except ValueError:
                    pass

            entry = {
                "guild_id": GUILD_ID,
                "channel_id": thread.id,
                "creator_id": creator_id,
                "title": title_text,
                "description": embed.description or "",
                "raid": raid,
                "diff": diff,
                "date": date_str,
                "hour": hour,
                "minute": minute,
                "dealer_slots": dealer_slots,
                "support_slots": support_slots,
                "min_level": min_level,
                "participants": {"dealer": dealer_list, "support": support_list},
                "queue": [],
                "reminder_sent": False,
            }
            existing[raid_id] = entry
            recovered += 1
            print(f"[레이드복구] 성공: {thread.name} (raid_id={raid_id})")

        _save_raids(existing)
        print(
            f"[레이드복구 완료] 새로 복구 {recovered}개 / 이미 있어서 건너뜀 {skipped_existing}개 / "
            f"일정 지나서 건너뜀 {skipped_past}개 / 실패 {len(failed)}개"
        )
        if failed:
            print("[레이드복구 실패 목록]")
            for f in failed:
                print(f"  - {f}")


async def setup(bot):
    await bot.add_cog(RecoverOnceCog(bot))
