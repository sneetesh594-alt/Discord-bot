from types import SimpleNamespace

import pytest

from aegis.leveling_config import LevelingConfig
from aegis.levels import level_from_xp, meaningful_text, multiplied_xp, progress, total_for_level


@pytest.mark.parametrize("level,total", [(0, 0), (1, 100), (2, 255), (3, 475), (5, 1150)])
def test_known_level_thresholds(level, total):
    assert total_for_level(level) == total
    assert level_from_xp(total) == level
    if level:
        assert level_from_xp(total - 1) == level - 1


def test_every_level_boundary_and_cap_use_exact_integers():
    for level in range(1, 1001):
        threshold = total_for_level(level)
        assert level_from_xp(threshold) == level
        assert level_from_xp(threshold - 1) == level - 1
    assert level_from_xp(9_000_000_000_000_000) == 1000
    assert progress(total_for_level(1000)).fraction == 1
    assert progress(50).fraction == 0.5


@pytest.mark.parametrize(
    "text",
    ["", "hi", "!!!🙂", "https://example.com", "<@123456789>", "!rank please", "/command test"],
)
def test_low_effort_messages_and_command_or_link_only_content_do_not_earn_xp(text):
    assert not meaningful_text(text, 5)


@pytest.mark.parametrize("text", ["Hello everyone", "नमस्ते दोस्तों", "kaise ho sab", "Nice game 👍"])
def test_english_hindi_and_hinglish_conversation_qualifies(text):
    assert meaningful_text(text, 5)


def test_role_channel_and_temporary_boosts_stack_but_role_boosts_do_not():
    member = SimpleNamespace(roles=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
    channel = SimpleNamespace(id=3, parent_id=4)
    config = LevelingConfig(
        role_multipliers={"1": 150, "2": 200},
        channel_multipliers={"4": 200},
        bonus_percent=200,
        bonus_until=100,
    )
    assert multiplied_xp(20, member, channel, config, 99) == 160
    assert multiplied_xp(20, member, channel, config, 100) == 80
    assert multiplied_xp(20, member, channel, LevelingConfig(role_multipliers={"1": 50}), 0) == 10


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": "false"},
        {"text_min_xp": 30, "text_max_xp": 20},
        {"text_cooldown_seconds": 0},
        {"voice_min_members": 1},
        {"reward_roles": {"1": True}},
        {"reward_roles": {"1001": 20}},
        {"role_multipliers": {"1": 0}},
        {"announcement_channel_id": -1},
    ],
)
def test_invalid_leveling_policy_is_rejected(changes):
    with pytest.raises(ValueError):
        LevelingConfig(**changes)
