"""/도움 명령어: 계란봇에 등록된 모든 명령어의 사용법과 목적을 안내합니다.

카테고리별로 나눠서 보여주고, 아래 드롭다운으로 카테고리를 바꿔가며 볼 수 있습니다.
새 cog/명령어를 추가하면 HELP_CATEGORIES에도 같이 등록해줘야 /도움에 반영됩니다.
"""
import discord
from discord.ext import commands
from discord import app_commands

BOT_NAME = "🐣 계란봇"

# =========================
# 카테고리별 명령어 설명
# (카테고리 이름, 이모지, 설명 한 줄, [(명령어, 사용법, 권한), ...])
# =========================
HELP_CATEGORIES: list[dict] = [
    {
        "key": "청소",
        "emoji": "🧹",
        "summary": "채팅방 메시지를 한 번에 정리합니다.",
        "commands": [
            ("/청소 [개수]", "현재 채널 메시지 삭제 (개수 생략 시 전체 삭제)", "관리자"),
        ],
    },
    {
        "key": "클리어골드",
        "emoji": "💰",
        "summary": "로스트아크 레이드별 클리어 골드·입장레벨·파티 정원을 조회/관리합니다.",
        "commands": [
            ("/클골 <레이드> <난이도>", "클리어 골드·입장레벨·파티 정원 조회", "전체"),
            ("/클골추가 <레이드> <난이도> <골드> <귀속골드> [입장레벨] [딜러정원] [서포터정원]",
             "새 레이드/난이도 조합 추가 (총합 자동 계산, 정원 기본값 딜러3/서포터1)", "계란 전용"),
            ("/클골수정 <레이드> <난이도> <골드> <귀속골드> [입장레벨] [딜러정원] [서포터정원]",
             "기존 조합 수정", "계란 전용"),
            ("/클골삭제 <레이드> <난이도>", "조합 삭제", "계란 전용"),
        ],
        "note": "\"싱글\" 난이도는 혼자 도는 레이드라 /클골에는 나오지만 /레이드 모집 대상에서는 제외돼요.",
    },
    {
        "key": "점심메뉴",
        "emoji": "🍽️",
        "summary": "오늘 뭐 먹을지 랜덤으로 추천받습니다.",
        "commands": [
            ("/점메추", "메뉴 랜덤 추천 (🎲 리롤 버튼으로 다시 뽑기 가능)", "전체"),
            ("/메뉴추가 <메뉴>", "추천 목록에 메뉴 추가", "계란 전용"),
            ("/메뉴삭제 <메뉴>", "추천 목록에서 메뉴 삭제", "계란 전용"),
            ("/메뉴판", "등록된 전체 메뉴 목록 조회", "전체"),
        ],
    },
    {
        "key": "레이드일정",
        "emoji": "⚔️",
        "summary": "레이드 모집 게시물을 만들고 참가신청/대기열/관리를 처리합니다.",
        "commands": [
            ("/레이드채널 <채널>", "레이드 모집 게시물을 올릴 포럼 채널 지정 (서버당 1개)", "관리자"),
            ("/레이드채널해제", "레이드 채널 지정 해제", "관리자"),
            ("/레이드채널정보", "현재 지정된 레이드 채널 확인", "전체"),
            ("/레이드 <제목>", "날짜/시/분/레이드/난이도 입력 → 모집 게시물 생성", "전체"),
        ],
        "note": (
            "레이드 선택지에는 /클골에 등록된 조합 외에 \"기타\"도 고를 수 있어요 (정원 16인 고정, 입장레벨 제한 없음).\n"
            "게시물에는 참가신청/참가변경/참가취소/대기열 명단/⚙️ 관리 버튼이 달리고, "
            "시작 10분 전 알림과 종료 30분 후 자동 마감(archive+lock)이 자동으로 처리돼요."
        ),
    },
    {
        "key": "고정공격대",
        "emoji": "🔁",
        "summary": "매주 같은 요일·시간에 자동으로 레이드 모집 게시물을 올립니다.",
        "commands": [
            ("/고정공격대생성", "요일/시/분/레이드/난이도 + 로스터(멤버) 등록", "전체"),
            ("/고정공격대목록", "이 서버에 등록된 고정공격대 목록 조회", "전체"),
            ("/고정공격대관리 <고정공격대>", "일정/로스터 관리 (실제 조작은 작성자 또는 관리자만)", "작성자/관리자"),
        ],
    },
    {
        "key": "TTS",
        "emoji": "🔊",
        "summary": "채팅 메시지를 음성으로 읽어줍니다.",
        "commands": [
            ("/tts <채널>", "TTS 채널로 지정", "관리자"),
            ("/tts해제", "TTS 채널 지정 해제", "관리자"),
            ("/tts정보", "현재 TTS 채널 확인", "전체"),
            ("/tts해결", "TTS 상태 오류 시 초기화", "관리자"),
            ("/join", "현재 음성 채널로 봇 소환 + 이 텍스트 채널을 임시 TTS 채널로 지정", "전체"),
            ("/leave", "음성 채널에서 나가고 임시 TTS 채널 해제", "전체"),
            ("/엔진 [목소리]", "TTS 목소리 엔진 조회/설정", "전체"),
            ("/tts커스텀 [옵션] [별명] [파일]", "서버별 커스텀 효과음 추가/삭제/조회", "관리자"),
        ],
    },
]


def build_overview_embed() -> discord.Embed:
    embed = discord.Embed(
        title=f"{BOT_NAME} 도움말",
        description=(
            "아래 드롭다운에서 카테고리를 고르면 해당 명령어들의 사용법이 보여요.\n"
            "`<>`는 필수 입력, `[]`는 생략 가능한 입력이에요."
        ),
        color=discord.Color.gold(),
    )
    for cat in HELP_CATEGORIES:
        embed.add_field(
            name=f"{cat['emoji']} {cat['key']}",
            value=cat["summary"],
            inline=False,
        )
    embed.set_footer(text="계란 전용 명령어는 봇 소유자만 사용할 수 있어요.")
    return embed


def build_category_embed(cat: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"{cat['emoji']} {cat['key']} 명령어",
        description=cat["summary"],
        color=discord.Color.gold(),
    )
    for name, usage, perm in cat["commands"]:
        embed.add_field(
            name=name,
            value=f"{usage}\n> 권한: **{perm}**",
            inline=False,
        )
    if cat.get("note"):
        embed.add_field(name="📌 참고", value=cat["note"], inline=False)
    embed.set_footer(text="◀ 드롭다운에서 다른 카테고리도 볼 수 있어요")
    return embed


# =========================
# 카테고리 선택 드롭다운
# =========================
class HelpCategorySelect(discord.ui.Select):
    def __init__(self, author_id: int):
        self.author_id = author_id
        options = [
            discord.SelectOption(label="전체 보기", value="__overview__", emoji="📖")
        ] + [
            discord.SelectOption(label=cat["key"], value=cat["key"], emoji=cat["emoji"])
            for cat in HELP_CATEGORIES
        ]
        super().__init__(
            placeholder="카테고리를 선택하세요",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "⛔ 명령어를 사용하신 분만 카테고리를 바꿀 수 있습니다.",
                ephemeral=True,
            )

        value = self.values[0]
        if value == "__overview__":
            embed = build_overview_embed()
        else:
            cat = next(c for c in HELP_CATEGORIES if c["key"] == value)
            embed = build_category_embed(cat)

        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=120)
        self.add_item(HelpCategorySelect(author_id))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# =========================
# Cog
# =========================
class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="도움", description="계란봇 명령어 사용법을 안내합니다.")
    async def help_command(self, interaction: discord.Interaction):
        embed = build_overview_embed()
        view = HelpView(interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
