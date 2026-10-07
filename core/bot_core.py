import os
import sys
import traceback
import discord
from discord.ext import commands

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class MococoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True
        intents.voice_states = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

        self._scheduler_started = False

    async def on_ready(self):
        print(f"[Bot] Ready: {self.user}")

    async def setup_hook(self):
        extensions = [
            "cogs.clean",
            "cogs.cleargold",
            "cogs.menu",
            "cogs.tts",
            "cogs.raid_schedule",
            "cogs.raid_notify",
            "cogs.game_recruit",
            "cogs.meetup",
            "cogs.schedule_check",
            "cogs.announce",
            "cogs.suggest",
            "cogs.help",
            "cogs.recover_once",  # 1회용 레이드 데이터 복구 (복구 확인 후 이 줄과 cogs/recover_once.py 삭제 가능)
        ]

        for ext in extensions:
            try:
                await self.load_extension(ext)
                print(f"[EXT] loaded {ext}")
            except Exception as e:
                print(f"[EXT] failed {ext}: {e}")
                traceback.print_exc()

        try:
            await self.tree.sync()
            print("[Sync] success")
        except Exception as e:
            print(f"[Sync] failed: {e}")


def create_bot():
    return MococoBot()
