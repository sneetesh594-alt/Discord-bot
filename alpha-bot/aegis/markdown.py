"""Escape display text without deprecated positional regex arguments.

The app needs literal Markdown characters, mention-neutralization is separate.
Bare URLs are kept readable; masked-link syntax is escaped as a unit.
"""

import re

_TOKENS = re.compile(
    r"(?P<url>(?:https?|steam)://[^\s<]*[^\s<.,:;\"'\]]|<[^: >]+:/[^ >]+>)"
    r"|(?P<markup>[\\_*~`|]|^#{1,3}|^>{1,3}[ \t]|\[[^\n]+\]\([^\n]+\)|^[ \t]*-)",
    flags=re.MULTILINE,
)


def escape_markdown(text: str) -> str:
    def literal(match):
        return match["url"] if match["url"] is not None else "\\" + match["markup"]

    return _TOKENS.sub(literal, text)
