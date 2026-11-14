import pytest

from aegis.config import GuildPolicy
from aegis.rules import MessageSample, RuleEngine, normalize


def sample(index=0, content="hello", channel=20, guild=1, user=10, mentions=0, timestamp=None):
    return MessageSample(
        guild,
        user,
        channel,
        index + 1,
        content,
        mentions,
        float(index) if timestamp is None else timestamp,
    )


def test_sixth_message_triggers_and_old_samples_expire():
    engine, policy = RuleEngine(), GuildPolicy(duplicate_threshold=100)
    assert all(engine.check(sample(i, f"message {i}"), policy) is None for i in range(5))
    assert engine.check(sample(5, "sixth"), policy).category == "flood"
    assert engine.check(sample(6, "after window", timestamp=16), policy) is None


def test_window_boundary_is_exclusive():
    engine = RuleEngine()
    policy = GuildPolicy(spam_max_messages=2, duplicate_threshold=100)
    engine.check(sample(0, "one", timestamp=0), policy)
    engine.check(sample(1, "two", timestamp=1), policy)
    assert engine.check(sample(2, "three", timestamp=10), policy) is None


def test_duplicate_and_cross_channel_patterns():
    engine = RuleEngine()
    policy = GuildPolicy()
    assert engine.check(sample(0), policy) is None
    assert engine.check(sample(1), policy) is None
    assert engine.check(sample(2), policy).category == "duplicate_spam"
    engine = RuleEngine()
    for i in range(2):
        assert engine.check(sample(i, channel=20 + i), policy) is None
    assert engine.check(sample(2, channel=22), policy).category == "cross_channel_spam"


def test_users_and_guilds_are_isolated():
    engine, policy = RuleEngine(), GuildPolicy()
    for i in range(2):
        engine.check(sample(i), policy)
    assert engine.check(sample(2, user=11), policy) is None
    assert engine.check(sample(2, guild=2), policy) is None


def test_edits_and_gateway_retries_do_not_count_as_new_messages():
    engine, policy = RuleEngine(), GuildPolicy(spam_max_messages=2)
    for _ in range(10):
        assert engine.check(sample(0), policy) is None
    for i in range(10):
        assert engine.check(sample(i, "edited", timestamp=i), policy, track_spam=False) is None
    assert len(engine.history[(1, 10)]) == 1


@pytest.mark.parametrize("content", ["bass", "class", "passage", "assistant"])
def test_whole_word_false_positive_regression(content):
    assert RuleEngine().check(sample(content=content), GuildPolicy(banned_phrases=("ass",))) is None


@pytest.mark.parametrize(
    "content",
    ["idiot", "1d10t", "i.d.i.o.t", "i d i o t", "idiiot", "ｉｄｉｏｔ", "i\u200bd\u200biot"],
)
def test_latin_obfuscation(content):
    assert (
        RuleEngine().check(sample(content=content), GuildPolicy(banned_phrases=("idiot",))).category
        == "banned_phrase"
    )


@pytest.mark.parametrize("content", ["मादरचोद", "तू मादरचोद है", "MADARCHOD", "m.a.d.a.r.c.h.o.d"])
def test_hindi_and_hinglish(content):
    policy = GuildPolicy(banned_phrases=("मादरचोद", "madarchod"))
    assert RuleEngine().check(sample(content=content), policy).category == "banned_phrase"


def test_devanagari_combining_marks_are_preserved():
    assert normalize("नमस्ते दुनिया") == "नमस्ते दुनिया"


@pytest.mark.parametrize(
    "content",
    ["https://discord.gg/AbC", "discord.com/invite/AbC", "https://discordapp.com/invite/test"],
)
def test_invite_rule(content):
    assert (
        RuleEngine().check(sample(content=content), GuildPolicy(block_invites=True)).category
        == "invite"
    )
    assert RuleEngine().check(sample(content=content), GuildPolicy()) is None


@pytest.mark.parametrize(
    "url,blocked",
    [
        ("https://evil.example/path", True),
        ("https://a.evil.example/test", True),
        ("https://evil.example./", True),
        ("https://not-evil.example", False),
        ("https://evil.example.good.example", False),
        ("https://evil.example@good.example", False),
        ("https://good.example@evil.example", True),
    ],
)
def test_url_host_parsing_not_substrings(url, blocked):
    hit = RuleEngine().check(sample(content=url), GuildPolicy(blocked_domains=("evil.example",)))
    assert (hit is not None) == blocked


def test_mentions_and_empty_messages():
    policy = GuildPolicy()
    assert RuleEngine().check(sample(mentions=5), policy) is None
    assert RuleEngine().check(sample(mentions=6), policy).category == "mention_spam"
    engine = RuleEngine()
    assert all(engine.check(sample(i, content=""), policy) is None for i in range(3))


def test_memory_is_bounded_and_guild_clear_works():
    engine = RuleEngine(max_users=2)
    for user in range(20):
        engine.check(sample(user=user), GuildPolicy())
    assert len(engine.history) == 2
    engine.clear_guild(1)
    assert not engine.history


def test_repeated_character_phrases_do_not_cause_pathological_backtracking():
    policy = GuildPolicy(banned_phrases=("a" * 63 + "b",))
    assert RuleEngine().check(sample(content="a" * 4000), policy) is None
