import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image

from aegis.cards import rank_card
from aegis.cogs.automod import AutoModeration
from aegis.cogs.leveling import Leveling
from aegis.cogs.utility import Utility
from aegis.config import GuildPolicy


async def test_worker_only_awards_xp_after_a_clean_moderation_result(settings, message):
    service = SimpleNamespace(
        process=AsyncMock(side_effect=[False, True]), metrics=__import__("collections").Counter()
    )
    leveling = SimpleNamespace(on_message=AsyncMock())
    bot = SimpleNamespace(
        settings=settings, service=service, leveling=leveling, get_guild=lambda _: message.guild
    )
    cog = AutoModeration(bot)
    task = asyncio.create_task(cog.worker())
    cog.enqueue((message, 1.0, False))
    cog.enqueue((message, 2.0, False))
    await asyncio.wait_for(cog.queue.join(), timeout=2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    leveling.on_message.assert_awaited_once_with(message, 2.0)


def test_rank_card_is_a_valid_png_with_a_progress_bar_and_bad_avatar_fallback():
    output = rank_card("Nova Rook", 1275, 3, avatar=b"not an image", server="Prime Gaming")
    with Image.open(BytesIO(output)) as image:
        assert image.format == "PNG" and image.size == (900, 400)
        assert image.getpixel((70, 300)) != image.getpixel((830, 300))


def test_rank_card_handles_max_level_and_unicode_names():
    output = rank_card("नमस्ते खिलाड़ी ✨", 9_000_000_000_000_000, 1)
    with Image.open(BytesIO(output)) as image:
        assert image.size == (900, 400)


async def test_voice_listener_delegates_eligibility_and_disconnect_to_service(settings):
    from unittest.mock import Mock

    service = SimpleNamespace(repo=None, voice_update=AsyncMock(), disconnect=Mock())
    cog = Leveling(SimpleNamespace(leveling=service))
    before = SimpleNamespace(
        channel=SimpleNamespace(id=50),
        self_mute=False,
        self_deaf=False,
        mute=False,
        deaf=False,
        suppress=False,
    )
    after = SimpleNamespace(
        channel=None, self_mute=False, self_deaf=False, mute=False, deaf=False, suppress=False
    )
    member = SimpleNamespace(guild=SimpleNamespace(id=1))
    await cog.on_voice_state_update(member, before, after)
    service.voice_update.assert_awaited_once_with(member, before, after)
    await cog.on_disconnect()
    service.disconnect.assert_called_once()


async def test_public_privacy_reply_fits_discord_message_limit_and_mentions_xp(settings):
    bot = SimpleNamespace(
        settings=settings, store=SimpleNamespace(policy=AsyncMock(return_value=GuildPolicy()))
    )
    interaction = SimpleNamespace(
        guild_id=1,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await Utility.privacy.callback(Utility(bot), interaction)
    text = interaction.followup.send.await_args.args[0]
    assert len(text) <= 2000
    assert "xp-optout" in text
