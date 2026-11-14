import logging

import pytest

from aegis.__main__ import invite_url
from aegis.logging_config import RedactingFormatter
from aegis.security import target_error


def target(**changes):
    values = dict(
        actor_id=10,
        target_id=20,
        owner_id=1,
        bot_id=99,
        actor_role=10,
        target_role=2,
        bot_role=20,
        target_admin=False,
        action="ban",
    )
    values.update(changes)
    return target_error(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"target_id": 1},
        {"target_id": 10},
        {"target_id": 99},
        {"target_role": 10},
        {"target_role": 20},
        {"target_admin": True, "action": "timeout"},
    ],
)
def test_protected_targets_are_rejected(changes):
    assert target(**changes)


def test_owner_bypasses_actor_role_but_not_bot_role():
    assert target(actor_id=1, actor_role=1) is None
    assert target(actor_id=1, target_role=20)
    assert target() is None


def test_logs_redact_secrets_in_formatted_messages_and_exceptions():
    formatter = RedactingFormatter(("secret-token", "sk-private-key"))
    record = logging.LogRecord("test", 40, "", 0, "%s sk-private-key", ("secret-token",), None)
    output = formatter.format(record)
    assert "secret-token" not in output and "sk-private-key" not in output
    assert output.count("[REDACTED]") == 2


def test_invite_omits_administrator_and_uses_both_scopes():
    from urllib.parse import parse_qs, urlsplit

    import discord

    query = parse_qs(urlsplit(invite_url(123)).query)
    permissions = discord.Permissions(int(query["permissions"][0]))
    assert not permissions.administrator and permissions.moderate_members
    assert set(query["scope"][0].split()) == {"bot", "applications.commands"}
