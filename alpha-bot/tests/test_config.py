import pytest

from aegis.config import AIConfig, GuildPolicy, RuntimeConfig, Settings, from_mapping


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "destructive"},
        {"spam_timeout_seconds": 2419201},
        {"spam_max_messages": True},
        {"max_mentions": 0},
        {"ai_enabled": "false"},
        {"banned_phrases": "not a list"},
        {"blocked_domains": ["https://evil.example"]},
        {"mod_log_channel_id": -1},
        {"exempt_role_ids": [True]},
    ],
)
def test_invalid_policy_is_rejected(kwargs):
    with pytest.raises(ValueError):
        GuildPolicy(**kwargs)


def test_unknown_keys_and_invalid_ai_settings_are_rejected():
    with pytest.raises(ValueError):
        from_mapping(GuildPolicy, {"misspelled_setting": 1})
    with pytest.raises(ValueError):
        AIConfig(sample_rate=1.1)
    with pytest.raises(ValueError):
        RuntimeConfig(queue_size=0)


def test_config_can_be_checked_offline_but_startup_requires_token(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("policy:\n  mode: observe\n", encoding="utf-8")
    monkeypatch.setattr("aegis.config.load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("CONFIG_PATH", str(config))
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("AI_ENABLED", "false")
    monkeypatch.delenv("MOD_LOG_CHANNEL_ID", raising=False)
    assert Settings.load(require_token=False).policy.mode == "observe"
    with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
        Settings.load()
    config.write_text("policy: []", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        Settings.load(require_token=False)


def test_secrets_are_not_in_settings_repr(settings):
    assert settings.token not in repr(settings)
