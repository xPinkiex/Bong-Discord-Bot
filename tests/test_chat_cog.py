"""Drive ChatCog.on_message with mock Discord objects (no network)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bong.bot.chat import ChatCog
from bong.bot.client import BongBot
from bong.config import Settings
from bong.conversation.bong import Bong

from conftest import FakeLLM

OWNER, FRIEND, STRANGER, BONG_ID = 1, 2, 3, 99
CHANNEL = 500


class _Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def make_channel(channel_id=CHANNEL):
    ch = MagicMock()
    ch.id = channel_id
    ch.send = AsyncMock()
    ch.typing = lambda: _Typing()

    async def history(**kw):
        for _ in ():
            yield
    ch.history = history
    return ch


_next_id = iter(range(1000, 10_000))


def make_message(content, author_id=FRIEND, channel=None, guild=True, mentions=()):
    author = SimpleNamespace(id=author_id, bot=False, display_name=f"user{author_id}")
    return SimpleNamespace(
        id=next(_next_id), author=author, content=content, clean_content=content,
        channel=channel or make_channel(), guild=SimpleNamespace(id=7) if guild else None,
        attachments=[], mentions=list(mentions), reference=None,
        created_at=datetime(2026, 11, 13, 13, 30, tzinfo=timezone.utc),
    )


@pytest.fixture
def setup(tmp_path):
    settings = Settings(discord_token="x", owner_id=OWNER, data_dir=tmp_path / "d", user_data_dir=tmp_path / "u")
    bot = BongBot(settings)
    llm = FakeLLM({"helper": ["NO"]}, default="*boop* :3")
    bot.bong = Bong(llm, chat_model="chat", helper_model="helper")
    bot.user_store.load()
    bot.active_channels.load()
    bot.user_store.add_tag(FRIEND, "llm")
    bot._connection.user = SimpleNamespace(id=BONG_ID)
    bot.get_context = AsyncMock(return_value=SimpleNamespace(valid=False))
    cog = ChatCog(bot)
    cog.approvals.request = AsyncMock()
    return bot, cog, llm


async def test_ignores_inactive_channels(setup):
    bot, cog, llm = setup
    msg = make_message("bong hi")
    await cog.on_message(msg)
    assert llm.calls == [] and not msg.channel.send.called


async def test_replies_in_active_channel_and_counts_tokens(setup):
    bot, cog, llm = setup
    bot.active_channels.toggle(CHANNEL)
    msg = make_message("bong hi")
    await cog.on_message(msg)
    msg.channel.send.assert_awaited_once_with("*boop* :3")
    assert bot.user_store._users[FRIEND]["tokens"] == 15


async def test_unaddressed_message_is_context_only_and_no_cooldown_spam(setup):
    bot, cog, llm = setup
    bot.active_channels.toggle(CHANNEL)
    channel = make_channel()
    for text in ("lunch?", "pizza maybe"):
        await cog.on_message(make_message(text, channel=channel))
    assert not channel.send.called            # no replies, no "Slow down!"
    assert len(bot.bong.history.get(CHANNEL).entries) == 2


async def test_cooldown_applies_only_to_replies(setup):
    bot, cog, llm = setup
    bot.active_channels.toggle(CHANNEL)
    channel = make_channel()
    await cog.on_message(make_message("bong one", channel=channel))
    await cog.on_message(make_message("bong two", channel=channel))
    texts = [c.args[0] for c in channel.send.await_args_list]
    assert texts[0] == "*boop* :3"
    assert texts[1].startswith("Slow down!")
    assert len(llm.calls_to("chat")) == 1


async def test_owner_has_no_cooldown(setup):
    bot, cog, llm = setup
    bot.active_channels.toggle(CHANNEL)
    channel = make_channel()
    for _ in range(3):
        await cog.on_message(make_message("bong", author_id=OWNER, channel=channel))
    assert len(llm.calls_to("chat")) == 3


async def test_strangers_are_context_not_answered(setup):
    bot, cog, llm = setup
    bot.active_channels.toggle(CHANNEL)
    msg = make_message("bong hello", author_id=STRANGER)
    await cog.on_message(msg)
    assert not msg.channel.send.called
    assert bot.bong.history.get(CHANNEL).entries[-1].author_name == f"user{STRANGER}"


async def test_unknown_dm_goes_to_approval(setup):
    bot, cog, llm = setup
    msg = make_message("hi bong", author_id=STRANGER, guild=False)
    await cog.on_message(msg)
    cog.approvals.request.assert_awaited_once_with(msg)
    assert llm.calls == []


async def test_approved_dm_always_answered(setup):
    bot, cog, llm = setup
    msg = make_message("how are you", guild=False)
    await cog.on_message(msg)
    msg.channel.send.assert_awaited_once()
    assert llm.calls_to("helper") == []       # DMs skip the classifier


async def test_duplicate_events_are_ignored(setup):
    bot, cog, llm = setup
    bot.user_store.add_tag(FRIEND, "llm_fast")
    bot.active_channels.toggle(CHANNEL)
    msg = make_message("bong hi")
    await cog.on_message(msg)
    await cog.on_message(msg)
    assert msg.channel.send.await_count == 1


async def test_llm_failure_sends_error_message(setup):
    bot, cog, llm = setup
    bot.active_channels.toggle(CHANNEL)

    async def boom(*a, **k):
        raise RuntimeError("ollama down")
    llm.chat = boom
    msg = make_message("bong hi")
    await cog.on_message(msg)
    msg.channel.send.assert_awaited_once_with("Something went wrong processing that message. Try again?")
