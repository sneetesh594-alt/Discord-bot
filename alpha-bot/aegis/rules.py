"""Deterministic local checks. No network, database, or raw message persistence."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import OrderedDict, deque
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

from aegis.config import GuildPolicy

LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t"})
INVITE = re.compile(
    r"(?<![\w.])(?:https?://)?(?:www\.)?"
    r"(?:discord\.gg/|discord(?:app)?\.com/invite/)[a-z0-9-]+",
    re.I,
)
URL = re.compile(r"https?://[^\s<>]+", re.I)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().translate(LEET)
    # Only embedded substitutions: standalone @ mentions retain token boundaries.
    text = re.sub(r"(?<=[a-z])@(?=[a-z])", "a", text)
    text = re.sub(r"(?<=[a-z])\$(?=[a-z])", "s", text)
    # Preserve combining marks so Devanagari vowels are not destroyed.
    text = "".join(
        "" if unicodedata.category(c) == "Cf" else c if unicodedata.category(c)[0] in "LMN" else " "
        for c in text
    )
    return " ".join(text.split())


@lru_cache(maxsize=512)
def phrase_patterns(phrases: tuple[str, ...]) -> tuple[tuple[str, re.Pattern], ...]:
    patterns = []
    for phrase in phrases:
        folded = normalize(phrase)
        if not folded:
            continue
        # Whole-token matching avoids substring errors such as ass/bass. For
        # longer Latin terms, accept bounded spacing/punctuation and repetitions.
        if folded.isascii() and len(folded.replace(" ", "")) >= 4:
            chars = folded.replace(" ", "")
            # Atomic repetitions avoid exponential backtracking for configured
            # phrases containing long runs of the same character (Python 3.11+).
            letters = [
                "(?>"
                + re.escape(c)
                + ("" if i + 1 < len(chars) and chars[i + 1] == c else "{1,3}")
                + ")"
                for i, c in enumerate(chars)
            ]
            body = r"[\W_]{0,3}".join(letters)
        else:
            body = re.escape(folded).replace(r"\ ", r"\s+")
        patterns.append((phrase, re.compile(r"(?<!\w)" + body + r"(?!\w)")))
    return tuple(patterns)


@dataclass(frozen=True)
class MessageSample:
    guild_id: int
    user_id: int
    channel_id: int
    message_id: int
    content: str
    mentions: int
    timestamp: float  # monotonic receipt time


@dataclass(frozen=True)
class RuleHit:
    category: str
    reason: str
    action: str


@dataclass(frozen=True)
class Seen:
    timestamp: float
    digest: str
    channel_id: int
    message_id: int


class RuleEngine:
    def __init__(self, max_users: int = 10000):
        self.max_users = max_users
        self.history: OrderedDict[tuple[int, int], deque[Seen]] = OrderedDict()

    def clear_guild(self, guild_id: int) -> None:
        for key in list(self.history):
            if key[0] == guild_id:
                del self.history[key]

    def check(
        self, sample: MessageSample, policy: GuildPolicy, *, track_spam: bool = True
    ) -> RuleHit | None:
        if sample.mentions > policy.max_mentions:
            return RuleHit("mention_spam", "Too many user/role mentions", "spam")
        folded = normalize(sample.content)
        if track_spam:
            key = (sample.guild_id, sample.user_id)
            history = self.history.setdefault(key, deque())
            self.history.move_to_end(key)
            while len(self.history) > self.max_users:
                self.history.popitem(last=False)
            while history and sample.timestamp - history[0].timestamp >= policy.spam_window_seconds:
                history.popleft()
            # Gateway retries and edit events must not inflate rate counters.
            if not any(s.message_id == sample.message_id for s in history):
                digest = hashlib.sha256(folded.encode()).hexdigest() if folded else ""
                history.append(Seen(sample.timestamp, digest, sample.channel_id, sample.message_id))
            else:
                digest = ""
            while len(history) > 101:
                history.popleft()
            if len(history) > policy.spam_max_messages:
                return RuleHit("flood", "Message rate limit exceeded", "spam")
            if digest:
                duplicates = [s for s in history if s.digest == digest]
                if len({s.channel_id for s in duplicates}) >= policy.cross_channel_threshold:
                    return RuleHit("cross_channel_spam", "Repeated text across channels", "spam")
                if len(duplicates) >= policy.duplicate_threshold:
                    return RuleHit("duplicate_spam", "Repeated identical text", "spam")
        if policy.block_invites and INVITE.search(sample.content):
            return RuleHit("invite", "Discord invite links are disabled", "delete_warn")
        for match in URL.finditer(sample.content):
            try:
                host = (urlsplit(match[0].rstrip(".,;!?)\"'")).hostname or "").lower().rstrip(".")
                host = host.encode("idna").decode("ascii")
            except (ValueError, UnicodeError):
                continue
            if any(host == d or host.endswith("." + d) for d in policy.blocked_domains):
                return RuleHit("blocked_domain", "Link matches a blocked hostname", "delete_warn")
        for _phrase, pattern in phrase_patterns(policy.banned_phrases):
            if pattern.search(folded):
                return RuleHit("banned_phrase", "Server phrase rule matched", "delete_warn")
        return None
