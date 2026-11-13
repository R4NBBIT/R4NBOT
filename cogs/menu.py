import json
import os
import tempfile
import discord
from discord.ext import commands
from discord import app_commands
import random
import typing
from collections import Counter
from pathlib import Path

from core.config import EGG_ID


def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == EGG_ID


# =========================
# 리롤 버튼
# =========================
class RerollView(discord.ui.View):
    def __init__(self, cog: "MenuCog", menus: list[str], author_id: int):
        super().__init__(timeout=30)
        self.cog = cog
        self.menus = menus
        self.author_id = author_id
        self.reroll_count = 0
        self.history: list[str] = []
        self.message: discord.Message | None = None

    # -------------------------
    def pick_menu(self):
        if not self.menus:
            return None

        choice = random.choice(self.menus)
        self.history.append(choice)
        return choice

    # -------------------------
    def build_live_message(self) -> str | None:
        if not self.history:
            return None

        current = self.history[-1]

        return (
            f"🍽️ 오늘의 추천 메뉴는 **{current}**입니다, 마스터 🧐\n"
            f"└ 🎲 리롤: {self.reroll_count}회"
        )

    # -------------------------
    def build_final_message(self) -> str | None:
        if not self.history:
            return None

        current = self.history[-1]

        # 중복 메뉴는 "메뉴 x2"처럼 한 번만 적고 등장 순서(첫 등장 기준)는 유지함.
        counts = Counter(self.history)
        seen: set[str] = set()
        ordered_unique = []
        for m in self.history:
            if m not in seen:
                seen.add(m)
                ordered_unique.append(m)
        history_items = [
            f"{m} x{counts[m]}" if counts[m] > 1 else m
            for m in ordered_unique
        ]
        history_text = ", ".join(history_items)

        base = (
            f"🍽️ 오늘의 추천 메뉴는 **{current}**입니다, 마스터 🧐\n"
            f"└ 🎲 리롤: {self.reroll_count}회\n\n"
            f"📜 추천 기록\n{history_text}\n"
        )

        # 🔥 리롤 했을 때만 멘트 출력
        if self.reroll_count > 0:
            base += "\n_이쯤 되면 그냥 드십시오, 마스터._"

        return base

    # -------------------------
    async def on_timeout(self):
        if not self.message:
            return

        try:
            msg = self.build_final_message()

            if msg is None:
                msg = "메뉴가 비어있어요 😢"

            await self.message.edit(
                content=msg,
                view=None
            )
        except:
            pass

    # -------------------------
    @discord.ui.button(label="🎲 리롤", style=discord.ButtonStyle.blurple)
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "⛔ 명령어를 사용하신 마스터만 사용하실 수 있습니다.",
                ephemeral=True
            )

        # 이 뷰를 여기서 멈춰야, 나중에 이 뷰 혼자 60초 타임아웃이 발동해서
        # (그 시점 기준의 더 짧은) 옛날 기록으로 메시지를 덮어쓰는 일이 없음.
        self.stop()

        new_view = RerollView(self.cog, self.menus, self.author_id)
        new_view.history = self.history.copy()
        new_view.reroll_count = self.reroll_count + 1

        new_view.pick_menu()

        await self.cog.edit_menu(interaction, new_view)


# =========================
# Cog
# =========================
class MenuCog(commands.Cog):
    """
    메뉴는 두 종류로 나뉨:

    - 공통 메뉴 (core/menu.txt): 모든 서버가 기본으로 공유. 추가는 계란만 가능.
      삭제를 요청해도 실제로 파일에서 지워지지 않고, 요청한 서버의 "숨김" 목록에
      들어가서 그 서버에서만 안 보이게 됨 (다른 서버/공통 목록엔 영향 없음).
    - 서버 메뉴 (data/guild_menus.json): 서버별로 추가한 메뉴. 누구나 추가/삭제 가능하고,
      그 서버에만 반영됨.

    실제로 보여주는 메뉴 목록(점메추, 메뉴판) = (공통 메뉴 - 이 서버의 숨김 목록) + 이 서버의 추가 목록.
    DM에는 서버 개념이 없어서 공통 메뉴만 보여주고, 메뉴추가/메뉴삭제는 DM에서 쓸 수 없음
    (공통 메뉴 추가는 예외: 계란이면 DM에서도 가능).
    """

    def __init__(self, bot):
        self.bot = bot
        self.base_dir = Path(__file__).resolve().parent.parent
        self.menu_path = self.base_dir / "core" / "menu.txt"
        self.guild_menu_path = self.base_dir / "data" / "guild_menus.json"

        self.menu_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.menu_path.exists():
            self.menu_path.write_text("", encoding="utf-8")

        self.guild_menu_path.parent.mkdir(parents=True, exist_ok=True)

    # ---------------- 공통 메뉴 (core/menu.txt) ----------------
    def load_common_menus(self) -> list[str]:
        with open(self.menu_path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]

    def save_common_menus(self, menus: list[str]):
        unique = sorted(set(m.strip() for m in menus if m.strip()))
        with open(self.menu_path, "w", encoding="utf-8") as f:
            f.write("\n".join(unique) + "\n" if unique else "")

    # ---------------- 서버별 추가/숨김 목록 (data/guild_menus.json) ----------------
    def _load_all_guild_overrides(self) -> dict:
        if not self.guild_menu_path.exists():
            return {}
        try:
            with open(self.guild_menu_path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception as e:
            print(f"[메뉴] 서버별 메뉴 로드 실패: {e}")
            return {}

    def _save_all_guild_overrides(self, data: dict):
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self.guild_menu_path.parent)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.guild_menu_path)
        except Exception as e:
            print(f"[메뉴] 서버별 메뉴 저장 실패: {e}")
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    def load_guild_overrides(self, guild_id: int) -> dict:
        all_overrides = self._load_all_guild_overrides()
        entry = all_overrides.get(str(guild_id)) or {}
        return {
            "added": list(entry.get("added") or []),
            "removed": list(entry.get("removed") or []),
        }

    def save_guild_overrides(self, guild_id: int, overrides: dict):
        all_overrides = self._load_all_guild_overrides()
        all_overrides[str(guild_id)] = {
            "added": sorted(set(m.strip() for m in overrides.get("added", []) if m.strip())),
            "removed": sorted(set(m.strip() for m in overrides.get("removed", []) if m.strip())),
        }
        self._save_all_guild_overrides(all_overrides)

    # ---------------- 실제로 보여줄 메뉴 계산 ----------------
    def effective_menus(self, guild_id: int | None) -> list[str]:
        common = self.load_common_menus()

        if guild_id is None:
            return sorted(set(common))

        overrides = self.load_guild_overrides(guild_id)
        removed = set(overrides["removed"])
        visible_common = [m for m in common if m not in removed]
        return sorted(set(visible_common) | set(overrides["added"]))

    # -------------------------
    async def send_menu(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        menus = self.effective_menus(guild_id)
        view = RerollView(self, menus, interaction.user.id)

        if view.pick_menu() is None:
            return await interaction.response.send_message("메뉴가 비어있어요 😢")

        msg = view.build_live_message()

        await interaction.response.send_message(msg, view=view)
        view.message = await interaction.original_response()

    async def edit_menu(self, interaction: discord.Interaction, view: RerollView):
        msg = view.build_live_message()

        if msg is None:
            return await interaction.response.edit_message(
                content="메뉴가 비어있어요 😢",
                view=None
            )

        await interaction.response.edit_message(content=msg, view=view)
        view.message = await interaction.original_response()

    # -------------------------
    @app_commands.command(name="점메추", description="오늘의 점심 메뉴 추천")
    async def recommend_menu(self, interaction: discord.Interaction):
        await self.send_menu(interaction)

    # -------------------------
    @app_commands.command(
        name="메뉴추가",
        description="메뉴 추가 (기본: 이 서버에만 적용 / 공통=예는 계란 전용)",
    )
    @app_commands.describe(
        메뉴="추가할 메뉴 이름",
        공통="예로 하면 모든 서버가 공유하는 공통 메뉴에 추가합니다 (계란 전용). 기본은 아니오(이 서버에만 추가).",
    )
    async def add_menu(
        self,
        interaction: discord.Interaction,
        메뉴: str,
        공통: typing.Optional[typing.Literal["예", "아니오"]] = "아니오",
    ):
        menu = 메뉴.strip()
        is_common = (공통 == "예")

        # ---- 공통 메뉴에 추가: 계란만 가능 ----
        if is_common:
            if interaction.user.id != EGG_ID:
                return await interaction.response.send_message(
                    "⛔ 공통 메뉴 추가는 계란만 할 수 있어요.",
                    ephemeral=True,
                )

            common = self.load_common_menus()
            if menu in common:
                return await interaction.response.send_message(
                    "⚠️ 이미 등록된 공통 메뉴입니다.",
                    ephemeral=True,
                )

            common.append(menu)
            self.save_common_menus(common)

            return await interaction.response.send_message(
                f"✅ 공통 메뉴 추가 완료 (모든 서버에 적용): **{menu}**",
                ephemeral=True,
            )

        # ---- 서버 메뉴에 추가: 누구나 가능, 이 서버에만 적용 ----
        if interaction.guild is None:
            return await interaction.response.send_message(
                "❌ 서버 메뉴 추가는 서버 안에서만 할 수 있어요 (DM 불가).",
                ephemeral=True,
            )

        common = self.load_common_menus()
        overrides = self.load_guild_overrides(interaction.guild.id)

        if menu in common and menu in overrides["removed"]:
            # 공통 메뉴인데 이 서버에서 숨겨둔 상태 -> 다시 보이게 함
            overrides["removed"].remove(menu)
            self.save_guild_overrides(interaction.guild.id, overrides)
            return await interaction.response.send_message(
                f"✅ 메뉴를 다시 보이게 했어요 (이 서버에만 적용): **{menu}**",
                ephemeral=True,
            )

        if menu in common or menu in overrides["added"]:
            return await interaction.response.send_message(
                "⚠️ 이미 등록된 메뉴입니다.",
                ephemeral=True,
            )

        overrides["added"].append(menu)
        self.save_guild_overrides(interaction.guild.id, overrides)

        await interaction.response.send_message(
            f"✅ 메뉴 추가 완료 (이 서버에만 적용): **{menu}**",
            ephemeral=True,
        )

    # -------------------------
    @app_commands.command(
        name="메뉴삭제",
        description="메뉴 삭제 (이 서버에만 적용되고, 공통 메뉴 자체는 지워지지 않아요)",
    )
    @app_commands.describe(메뉴="삭제할 메뉴 이름")
    async def remove_menu(self, interaction: discord.Interaction, 메뉴: str):
        menu = 메뉴.strip()

        if interaction.guild is None:
            return await interaction.response.send_message(
                "❌ 메뉴 삭제는 서버 안에서만 할 수 있어요 (DM 불가).",
                ephemeral=True,
            )

        common = self.load_common_menus()
        overrides = self.load_guild_overrides(interaction.guild.id)

        if menu in overrides["added"]:
            # 이 서버에서 직접 추가한 메뉴 -> 그냥 제거
            overrides["added"].remove(menu)
            self.save_guild_overrides(interaction.guild.id, overrides)
            return await interaction.response.send_message(
                f"🗑️ 메뉴 삭제 완료 (이 서버에만 적용): **{menu}**",
                ephemeral=True,
            )

        if menu in common:
            if menu in overrides["removed"]:
                return await interaction.response.send_message(
                    "❌ 해당 메뉴는 존재하지 않습니다.",
                    ephemeral=True,
                )
            # 공통 메뉴 -> 실제로 지우지 않고 이 서버에서만 숨김
            overrides["removed"].append(menu)
            self.save_guild_overrides(interaction.guild.id, overrides)
            return await interaction.response.send_message(
                f"🗑️ 메뉴 삭제 완료 (공통 메뉴는 유지되고, 이 서버에서만 안 보여요): **{menu}**",
                ephemeral=True,
            )

        await interaction.response.send_message(
            "❌ 해당 메뉴는 존재하지 않습니다.",
            ephemeral=True,
        )

    # -------------------------
    @app_commands.command(name="메뉴판", description="전체 메뉴 보기")
    async def menu_list(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        menus = self.effective_menus(guild_id)

        if not menus:
            return await interaction.response.send_message(
                "메뉴가 비어있어요 😢",
                ephemeral=True
            )

        menu_text = "\n".join(f"• {m}" for m in menus)

        embed = discord.Embed(
            title="🍽️ 메뉴판",
            description=menu_text,
            color=discord.Color.green()
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(MenuCog(bot))
