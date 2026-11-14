import warnings

import pytest

from aegis.markdown import escape_markdown
from aegis.security import clean


@pytest.mark.parametrize(
    "text,expected",
    [
        ("**hello**", r"\*\*hello\*\*"),
        ("_name_", r"\_name\_"),
        ("||spoiler||", r"\|\|spoiler\|\|"),
        ("`code`", r"\`code\`"),
        ("# title\n> quote\n- list", "\\# title\n\\> quote\n\\- list"),
        ("[click](https://example.invalid/a_b)", r"\[click](https://example.invalid/a_b)"),
        ("https://example.invalid/a_b", "https://example.invalid/a_b"),
        ("नमस्ते खिलाड़ी", "नमस्ते खिलाड़ी"),
        ("one\\two", "one\\\\two"),
    ],
)
def test_display_markdown_escaping_is_literal_and_warning_free(text, expected):
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert escape_markdown(text) == expected


def test_mentions_remain_neutralized_and_lengths_are_bounded():
    result = clean("@everyone **notice** <@123456789012345678>", 100)
    assert "@\u200beveryone" in result and "<@\u200b123456789012345678>" in result
    assert r"\*\*notice\*\*" in result
    assert len(clean("**" * 1000, 50)) <= 50
