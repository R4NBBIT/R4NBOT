"""레이드 종류 / 난이도 / 클리어 골드 / 입장레벨 / 파티 정원 공용 데이터.

cogs/cleargold.py 와 cogs/raid_schedule.py 가 이 모듈을 공유합니다.
레이드+난이도 조합을 새로 추가/수정/삭제하는 건 /클골추가, /클골수정, /클골삭제
명령어(cogs/cleargold.py)를 통해서만 하고, 여기 저장된 데이터를 두 cog가
동일하게 읽어들이는 구조입니다.

각 값은 (골드, 귀속골드, 총합, 입장레벨, 딜러정원, 서포터정원) 6개짜리 튜플입니다.
입장레벨은 0이면 "제한 없음"을 의미합니다.
딜러정원/서포터정원 기본값은 4인 레이드 기준(3, 1)이며, 8인 레이드는 (6, 2),
16인 레이드는 (12, 4)로 /클골수정에서 직접 채워주면 됩니다.

주의: "싱글" 난이도는 혼자 도는 레이드라 /클골 조회에는 나오지만
cogs/raid_schedule.py(레이드 모집 일정)의 난이도 선택지에서는 제외됩니다.
"""
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone

# 봇 소스 위치(EGG-BOT/) 기준 절대경로로 고정
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../EGG-BOT
_DATA_DIR = os.path.join(_BASE_DIR, "data")
DATA_FILE = os.path.join(_DATA_DIR, "raid_gold.json")
PERIODS_FILE = os.path.join(_DATA_DIR, "raid_periods.json")

KST = timezone(timedelta(hours=9))

# 값 튜플에서 빠진 뒷부분을 채울 때 쓰는 기본값 (순서: 입장레벨, 딜러정원, 서포터정원)
_TAIL_DEFAULTS = [0, 3, 1]

# 최초 실행 시 파일이 없으면 이 기본값으로 data/raid_gold.json을 생성함
# 파티 정원은 대부분 4인(3,1) 기준. "1막 노말"만 8인(6,2)로 확인돼서 반영함.
# 나머지 레이드의 정원(8인/16인 여부)은 확인되는 대로 /클골수정으로 채워주세요.
DEFAULT_RAID_DATA = {
    ("에키드나", "하드"): (3600, 3600, 7200, 1640, 6, 2),
    ("베히모스", "노말"): (3600, 3600, 7200, 1640, 12, 4),

    ("1막", "싱글"): (5750, 5750, 11500, 1660, 6, 2),
    ("1막", "노말"): (5750, 5750, 11500, 1660, 6, 2),
    ("1막", "하드"): (9000, 9000, 18000, 1680, 6, 2),

    ("2막", "싱글"): (8250, 8250, 16500, 1670, 6, 2),
    ("2막", "노말"): (8250, 8250, 16500, 1670, 6, 2),
    ("2막", "하드"): (11500, 11500, 23000, 1690, 6, 2),

    ("3막", "싱글"): (10500, 10500, 21000, 1680, 6, 2),
    ("3막", "노말"): (10500, 10500, 21000, 1680, 6, 2),
    ("3막", "하드"): (13500, 13500, 27000, 1700, 6, 2),
    ("3막", "익스트림 노말"): (20000, 0, 20000, 1730, 6, 2),
    ("3막", "익스트림 하드"): (50000, 0, 50000, 1770, 6, 2),
    ("3막", "익스트림 나이트메어"): (50000, 0, 50000, 1780, 6, 2),

    ("4막", "싱글"): (16500, 16500, 33000, 1700, 6, 2),
    ("4막", "노말"): (16500, 16500, 33000, 1700, 6, 2),
    ("4막", "하드"): (42000, 0, 42000, 1720, 6, 2),

    ("종막", "싱글"): (20000, 20000, 40000, 1710, 6, 2),
    ("종막", "노말"): (20000, 20000, 40000, 1710, 6, 2),
    ("종막", "하드"): (52000, 0, 52000, 1730, 6, 2),
    ("종막", "익스트림 노말"): (20000, 0, 20000, 1730, 6, 2),
    ("종막", "익스트림 하드"): (50000, 0, 50000, 1770, 6, 2),
    ("종막", "익스트림 나이트메어"): (50000, 0, 50000, 1780, 6, 2),

    ("세르카", "매칭"): (17500, 17500, 35000, 1710, 3, 1),
    ("세르카", "노말"): (17500, 17500, 35000, 1710, 3, 1),
    ("세르카", "하드"): (44000, 0, 44000, 1730, 3, 1),
    ("세르카", "나이트메어"): (54000, 0, 54000, 0, 3, 1),

    ("지평의 성당", "1단계"): (0, 30000, 30000, 1700, 3, 1),
    ("지평의 성당", "2단계"): (0, 40000, 40000, 1720, 3, 1),
    ("지평의 성당", "3단계"): (0, 50000, 50000, 1750, 3, 1),

    ("벨가르딘", "노말"): (50000, 0, 50000, 1750, 6, 2),
    ("벨가르딘", "하드"): (62000, 0, 62000, 1770, 6, 2),
    ("벨가르딘", "나이트메어"): (75000, 0, 75000, 1780, 6, 2)
}

DIFF_ORDER = {
    "노말": 1,
    "하드": 2,
    "나이트메어": 3,

    "EX 노말": 4,
    "EX 하드": 5,
    "EX 나이트메어": 6,

    "익스트림 노말": 7,
    "익스트림 하드": 8,
    "익스트림 나이트메어": 9,

    "1단계": 10,
    "2단계": 11,
    "3단계": 12,
}

# 레이드 모집(cogs/raid_schedule.py)에서는 선택할 수 없는 난이도.
# "싱글"은 혼자 도는 레이드라 파티 모집 대상이 아님. /클골 조회에는 그대로 나옴.
NON_SCHEDULABLE_DIFFS = {"싱글"}

# raid_data에 값이 없을 때(이론상 발생 안 함) 쓰는 최후 폴백
DEFAULT_DEALER_SLOTS = 3
DEFAULT_SUPPORT_SLOTS = 1


# =========================
# 튜플 키 <-> 문자열 키 변환 (JSON은 튜플 키를 못 담아서 "레이드|난이도" 문자열로 변환)
# =========================
def key_to_str(key: tuple[str, str]) -> str:
    return f"{key[0]}|{key[1]}"


def str_to_key(s: str) -> tuple[str, str]:
    raid, diff = s.split("|", 1)
    return (raid, diff)


def _pad_value(v: list) -> list:
    """옛 버전 파일과의 호환: (골드,귀속골드,총합)만 있던 3개짜리, 입장레벨까지만
    있던 4개짜리 데이터를 6개짜리로 채워줌."""
    while len(v) < 6:
        v.append(_TAIL_DEFAULTS[len(v) - 3])
    return v


# =========================
# 로드 / 저장
# =========================
def load_raid_data() -> dict[tuple[str, str], tuple[int, int, int, int, int, int]]:
    os.makedirs(_DATA_DIR, exist_ok=True)
    if not os.path.exists(DATA_FILE):
        data = dict(DEFAULT_RAID_DATA)
        save_raid_data(data)
        print(f"[레이드데이터] 파일이 없어 기본값으로 생성했습니다: {DATA_FILE}")
        return data
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        data = {}
        for k, v in raw.items():
            data[str_to_key(k)] = tuple(_pad_value(list(v)))
        print(f"[레이드데이터] 로드 완료: {len(data)}개 조합 (경로: {DATA_FILE})")
        return data
    except Exception as e:
        print(f"[레이드데이터] 로드 실패, 기본값 사용: {e}")
        return dict(DEFAULT_RAID_DATA)


def save_raid_data(data: dict[tuple[str, str], tuple[int, int, int, int, int, int]]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=_DATA_DIR)
    try:
        serializable = {key_to_str(k): list(v) for k, v in data.items()}
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, DATA_FILE)
    except Exception as e:
        print(f"[레이드데이터] 저장 실패: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass


# =========================
# 조회 헬퍼
# =========================
def get_raids(data: dict[tuple[str, str], tuple]) -> list[str]:
    return sorted(set(r for r, _ in data.keys()))


def get_diffs(data: dict[tuple[str, str], tuple]) -> list[str]:
    return sorted(set(d for _, d in data.keys()), key=lambda x: DIFF_ORDER.get(x, 999))


def get_min_level(data: dict[tuple[str, str], tuple], key: tuple[str, str]) -> float:
    entry = data.get(key)
    if not entry or len(entry) < 4:
        return 0
    return entry[3]


def get_party_slots(data: dict[tuple[str, str], tuple], key: tuple[str, str]) -> tuple[int, int]:
    entry = data.get(key)
    if not entry or len(entry) < 6:
        return DEFAULT_DEALER_SLOTS, DEFAULT_SUPPORT_SLOTS
    return int(entry[4]), int(entry[5])


def get_schedulable_raid_data(data: dict[tuple[str, str], tuple]) -> dict[tuple[str, str], tuple]:
    """레이드 모집(raid_schedule.py)에서 선택 가능한 조합만 남긴 딕셔너리.

    - "싱글"처럼 혼자 도는 난이도는 파티 모집 대상이 아니라서 제외함.
    - 종료일이 지난 이벤트 레이드는 목록에서 빠짐. (시작일 전인 레이드는 미리 만들 수 있게 목록에 남김)
    """
    return {
        k: v for k, v in data.items()
        if k[1] not in NON_SCHEDULABLE_DIFFS and not is_expired(k)
    }


# =========================
# 모집 가능 기간 (시작일 / 종료일) - 이벤트 레이드처럼 기간이 있는 레이드에만 설정함
#
# 레이드 데이터(raid_gold.json)와 별도 파일(raid_periods.json)에 {"레이드|난이도": [시작일, 종료일]} 형태로 저장.
# 시작일/종료일은 각각 비워둘 수 있고(제한 없음), 날짜는 모두 포함(그날 포함)으로 취급함.
# - 종료일이 지나면 (오늘 > 종료일) 모집/알림 목록에서 자동으로 사라짐
# - 레이드 "일정 날짜"가 시작일 이전이거나 종료일 이후면 만들 수 없음
# =========================
# 최초 실행 시 파일이 없으면 이 값으로 data/raid_periods.json을 만듦 (그 뒤로는 /클골기간으로 관리)
DEFAULT_PERIODS = {
    ("3막", "익스트림 노말"): ("2026-09-23", "2026-10-20"),
    ("3막", "익스트림 하드"): ("2026-09-23", "2026-10-20"),
    ("3막", "익스트림 나이트메어"): ("2026-09-23", "2026-10-20"),
    ("종막", "익스트림 노말"): ("2026-10-21", "2026-11-17"),
    ("종막", "익스트림 하드"): ("2026-10-21", "2026-11-17"),
    ("종막", "익스트림 나이트메어"): ("2026-10-21", "2026-11-17"),
}


def save_periods(periods: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=_DATA_DIR)
    try:
        serializable = {key_to_str(k): list(v) for k, v in periods.items()}
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, PERIODS_FILE)
    except Exception as e:
        print(f"[레이드기간] 저장 실패: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def load_periods() -> dict[tuple[str, str], tuple[str, str]]:
    os.makedirs(_DATA_DIR, exist_ok=True)
    if not os.path.exists(PERIODS_FILE):
        periods = dict(DEFAULT_PERIODS)
        save_periods(periods)
        print(f"[레이드기간] 파일이 없어 기본값으로 생성했습니다: {PERIODS_FILE}")
        return periods
    try:
        with open(PERIODS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        periods = {}
        for k, v in raw.items():
            start, end = (list(v) + ["", ""])[:2]
            periods[str_to_key(k)] = (start or "", end or "")
        print(f"[레이드기간] 로드 완료: {len(periods)}개 (경로: {PERIODS_FILE})")
        return periods
    except Exception as e:
        print(f"[레이드기간] 로드 실패, 기본값 사용: {e}")
        return dict(DEFAULT_PERIODS)


# 모든 cog가 같은 객체를 보도록 모듈 단위로 하나만 들고 있음 (/클골기간으로 바꾸면 바로 전체에 반영)
PERIODS: dict[tuple[str, str], tuple[str, str]] = load_periods()


def _today() -> date:
    return datetime.now(KST).date()


def get_period(key: tuple[str, str]) -> tuple[date | None, date | None]:
    """(시작일, 종료일) 반환. 설정 안 된 쪽은 None."""
    start_s, end_s = PERIODS.get(key, ("", ""))
    start = date.fromisoformat(start_s) if start_s else None
    end = date.fromisoformat(end_s) if end_s else None
    return start, end


def set_period(key: tuple[str, str], start: date | None, end: date | None) -> None:
    """기간을 저장함. 둘 다 None이면 기간 설정을 지움."""
    if start is None and end is None:
        PERIODS.pop(key, None)
    else:
        PERIODS[key] = (start.isoformat() if start else "", end.isoformat() if end else "")
    save_periods(PERIODS)


def remove_period(key: tuple[str, str]) -> None:
    if key in PERIODS:
        PERIODS.pop(key)
        save_periods(PERIODS)


def is_expired(key: tuple[str, str], today: date | None = None) -> bool:
    """종료일이 지났으면(오늘 > 종료일) True. 종료일이 없으면 항상 False."""
    _, end = get_period(key)
    return end is not None and (today or _today()) > end


def format_period(key: tuple[str, str]) -> str:
    """'2026-09-23 ~ 2026-10-20' 형태 (한쪽만 있으면 '2026-09-23 ~', '~ 2026-10-20'). 없으면 빈 문자열."""
    start, end = get_period(key)
    if start is None and end is None:
        return ""
    return f"{start.isoformat() if start else ''} ~ {end.isoformat() if end else ''}".strip()


def check_schedule_date(key: tuple[str, str], schedule_date: date) -> str | None:
    """레이드 일정 날짜가 모집 가능 기간 안인지 확인. 문제가 있으면 사용자에게 보여줄 문구, 괜찮으면 None."""
    start, end = get_period(key)
    name = f"{key[0]} {key[1]}"
    if start is not None and schedule_date < start:
        return f"❌ {name}: {start.isoformat()}부터 진행되는 레이드라서, 그 전 날짜로는 일정을 만들 수 없어요."
    if end is not None and schedule_date > end:
        return f"❌ {name}: {end.isoformat()}까지만 진행되는 레이드라서, 그 이후 날짜로는 일정을 만들 수 없어요."
    return None


_DATE_RE = re.compile(r"(?:(\d{4})[-./년])?(\d{1,2})[-./월](\d{1,2})일?")


def parse_date_text(raw: str, today: date | None = None) -> date | None:
    """날짜 텍스트를 해석함. 연도를 생략하면 올해로 봄. (예: 2026-10-20 / 10/20 / 10월 20일)"""
    today = today or _today()
    m = _DATE_RE.fullmatch((raw or "").strip().replace(" ", ""))
    if not m:
        return None
    year_text, month, day = m.group(1), int(m.group(2)), int(m.group(3))
    try:
        return date(int(year_text) if year_text else today.year, month, day)
    except ValueError:
        return None
