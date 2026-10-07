from musicbot.bot import build_prompt


def test_build_prompt_without_genre():
    assert build_prompt("  song about cats ", None) == "song about cats"
    assert build_prompt("song about cats", "  ") == "song about cats"


def test_build_prompt_with_genre():
    assert build_prompt("song about cats", "Metal") == "song about cats\nGenre: Metal"
