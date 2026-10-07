import pytest

from musicbot.bot import MusicBot, build_prompt
from musicbot.treblo import GenerationResult, TrebloError


def test_build_prompt_without_genre():
    assert build_prompt("  song about cats ", None) == "song about cats"
    assert build_prompt("song about cats", "  ") == "song about cats"


def test_build_prompt_with_genre():
    assert build_prompt("song about cats", "Metal") == "song about cats\nGenre: Metal"


# --- /generate flow with a fake Discord interaction and fake API client ---

# Shape of a real finished task returned by GET /v1/generations/{task_id}.
REAL_TASK = {
    "id": "a83c3867-c361-4a45-9d88-e6fe056d29df",
    "status": "SUCCESS",
    "song_paths": ["https://cdn.treblo.com/pubapi/generations3/audio_x_0.mp3"],
    "error_message": None,
    "lyrics": "[Verse 1]\nScreen went black at a quarter past nine",
    "tags": ["country pop", "humorous"],
}


class FakeTreblo:
    def __init__(self, error=None, audio=b"ID3audio"):
        self.error = error
        self.audio = audio
        self.prompts = []

    async def generate(self, request, **kwargs):
        self.prompts.append(request.prompt)
        if self.error:
            raise self.error
        return GenerationResult(REAL_TASK["id"], REAL_TASK["song_paths"], REAL_TASK)

    async def download(self, url, max_bytes=None):
        return self.audio

    async def close(self):
        pass


class FakeResponse:
    def __init__(self):
        self.deferred = False
        self.messages = []

    async def defer(self, thinking=False):
        self.deferred = thinking

    async def send_message(self, content, ephemeral=False):
        self.messages.append((content, ephemeral))


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, *, files=None, file=None, allowed_mentions=None):
        self.sent.append({"content": content, "files": files or ([file] if file else [])})


class FakeUser:
    id = 42


class FakeGuild:
    filesize_limit = 10 * 1024**2


class FakeInteraction:
    def __init__(self):
        self.user = FakeUser()
        self.guild = FakeGuild()
        self.response = FakeResponse()
        self.followup = FakeFollowup()


@pytest.fixture
def make_bot():
    return lambda treblo: MusicBot(treblo, None)


async def test_generate_posts_song_and_lyrics(make_bot):
    treblo = FakeTreblo()
    bot = make_bot(treblo)
    inter = FakeInteraction()

    await bot.generate(inter, "My WiFi going out mid-meeting", "Country", show_lyrics=True)

    assert inter.response.deferred
    assert treblo.prompts == ["My WiFi going out mid-meeting\nGenre: Country"]
    song, lyrics = inter.followup.sent
    assert "My WiFi going out mid-meeting" in song["content"]
    assert "Genre: Country" in song["content"]
    assert [f.filename for f in song["files"]] == ["song_1.mp3"]
    assert "Screen went black" in lyrics["content"]
    assert 42 not in bot.active_users


async def test_too_large_file_becomes_link(make_bot):
    bot = make_bot(FakeTreblo(audio=None))
    inter = FakeInteraction()
    await bot.generate(inter, "lofi beat", None)
    song = inter.followup.sent[0]
    assert song["files"] == []
    assert REAL_TASK["song_paths"][0] in song["content"]


async def test_api_error_is_reported(make_bot):
    bot = make_bot(FakeTreblo(error=TrebloError("Treblo API error 402 (out of credits)")))
    inter = FakeInteraction()
    await bot.generate(inter, "lofi beat", None)
    assert "out of credits" in inter.followup.sent[0]["content"]
    assert 42 not in bot.active_users


async def test_cooldown_blocks_second_request(make_bot):
    bot = make_bot(FakeTreblo())
    await bot.generate(FakeInteraction(), "first song", None)
    second = FakeInteraction()
    await bot.generate(second, "second song", None)
    content, ephemeral = second.response.messages[0]
    assert "Slow down" in content and ephemeral
    assert second.followup.sent == []


async def test_lyrics_off_by_default(make_bot):
    bot = make_bot(FakeTreblo())
    inter = FakeInteraction()
    await bot.generate(inter, "lofi beat", None)
    assert len(inter.followup.sent) == 1
    assert "Lyrics" not in inter.followup.sent[0]["content"]
