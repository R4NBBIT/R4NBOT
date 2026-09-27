"""레이드+난이도별 개인 알림 구독 관리 모듈.

특정 (레이드, 난이도) 조합의 모집 게시물이 새로 올라올 때 멘션받고 싶은 사람들을
서버별로 저장한다. cogs/raid_notify.py(/레이드알림추가 등)에서 구독을 관리하고,
cogs/raid_schedule.py가 게시물 생성 직후 구독자 목록을 조회해 멘션한다.

저장 구조:
{
  "<guild_id>": {
    "<레이드>|<난이도>": [user_id, ...]
  }
}
"""
import json
import os
import tempfile
from typing import Dict, List

# 봇 소스 위치(EGG-BOT/) 기준 절대경로로 고정
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../EGG-BOT
_DATA_DIR = os.path.join(_BASE_DIR, "data")
DATA_FILE = os.path.join(_DATA_DIR, "raid_notify.json")


def _key(raid: str, diff: str) -> str:
    return f"{raid}|{diff}"


class RaidNotifyManager:
    def __init__(self):
        # {guild_id: {"레이드|난이도": [user_id, ...]}}
        self.data: Dict[int, Dict[str, List[int]]] = {}

        os.makedirs(_DATA_DIR, exist_ok=True)
        self._load()

    # =========================
    # 파일 로드 / 저장
    # =========================
    def _load(self):
        if not os.path.exists(DATA_FILE):
            return

        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.data = {
                int(guild_id): {
                    combo_key: [int(uid) for uid in user_ids]
                    for combo_key, user_ids in combos.items()
                }
                for guild_id, combos in raw.items()
            }
            print(f"[레이드알림] 로드 완료: {len(self.data)}개 서버 (경로: {DATA_FILE})")
        except Exception as e:
            print(f"[레이드알림] 로드 실패: {e}")

    def _save(self):
        os.makedirs(_DATA_DIR, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=_DATA_DIR)
        try:
            serializable = {str(k): v for k, v in self.data.items()}
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, DATA_FILE)
        except Exception as e:
            print(f"[레이드알림] 저장 실패: {e}")
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    # =========================
    # 구독 관리
    # =========================
    def subscribe(self, guild_id: int, user_id: int, raid: str, diff: str) -> bool:
        """구독 추가. 이미 구독 중이었으면 False, 새로 추가됐으면 True."""
        combos = self.data.setdefault(guild_id, {})
        subscribers = combos.setdefault(_key(raid, diff), [])

        if user_id in subscribers:
            return False

        subscribers.append(user_id)
        self._save()
        return True

    def unsubscribe(self, guild_id: int, user_id: int, raid: str, diff: str) -> bool:
        """구독 해제. 원래 구독 중이 아니었으면 False, 해제됐으면 True."""
        combos = self.data.get(guild_id)
        if not combos:
            return False

        subscribers = combos.get(_key(raid, diff))
        if not subscribers or user_id not in subscribers:
            return False

        subscribers.remove(user_id)
        if not subscribers:
            del combos[_key(raid, diff)]
        self._save()
        return True

    def get_subscribers(self, guild_id: int, raid: str, diff: str) -> List[int]:
        """해당 (레이드, 난이도)를 구독 중인 유저 id 목록."""
        combos = self.data.get(guild_id, {})
        return list(combos.get(_key(raid, diff), []))

    def get_user_subscriptions(self, guild_id: int, user_id: int) -> List[tuple[str, str]]:
        """해당 유저가 이 서버에서 구독 중인 (레이드, 난이도) 목록."""
        combos = self.data.get(guild_id, {})
        result = []
        for combo_key, user_ids in combos.items():
            if user_id in user_ids:
                raid, diff = combo_key.split("|", 1)
                result.append((raid, diff))
        return result


# 전역 인스턴스
raid_notify_manager = RaidNotifyManager()
