from __future__ import annotations

import asyncio
from datetime import datetime

from bong.conversation.bong import Bong, Cooldown, clean_reply
from bong.conversation.history import HistoryEntry, HistoryStore
from bong.conversation.turn import ReplyInfo
from bong.memory import Memory
from bong.persona import fill

from conftest import FakeLLM, make_turn

CHAT, HELPER = "chat-model", "helper-model"


def make_bong(llm, **kw):
    return Bong(llm, chat_model=CHAT, helper_model=HELPER, **kw)


# --- detection --------------------------------------------------------------

async def test_name_mention_skips_classifier():
    llm = FakeLLM()
    bong = make_bong(llm)
    assert await bong.is_for_bong(make_turn("hey BONG what's up"))
    assert llm.calls == []


async def test_dm_and_ping_skip_classifier():
    llm = FakeLLM()
    bong = make_bong(llm)
    assert await bong.is_for_bong(make_turn("hello", guild_id=None))
    assert await bong.is_for_bong(make_turn("hello", mentions_bong=True))
    assert llm.calls == []


async def test_classifier_decides_ambiguous_messages():
    llm = FakeLLM({HELPER: ["YES", "no."]})
    bong = make_bong(llm)
    assert await bong.is_for_bong(make_turn("that was funny"))
    assert not await bong.is_for_bong(make_turn("anyone up for pizza"))
    prompt = llm.calls_to(HELPER)[0][1][0]["content"]
    assert "Eve: that was funny" in prompt


async def test_classifier_error_means_no():
    class Broken(FakeLLM):
        async def chat(self, *a, **k):
            raise RuntimeError("down")
    assert not await make_bong(Broken()).is_for_bong(make_turn("hmm"))


# --- replying ---------------------------------------------------------------

async def test_respond_builds_original_style_prompt_and_records_both_sides():
    llm = FakeLLM({CHAT: ["Bong at 14:30: *wags tail* hi Eve :3"]})
    bong = make_bong(llm)
    bong.record(make_turn("first", author_name="Rado", author_id=2))
    bong.record(make_turn("second", author_name="Rado", author_id=2))

    reply = await bong.respond(make_turn("bong say hi\nplease", reply_to=ReplyInfo("Rado", 2, "lol")))

    assert reply.text == "*wags tail* hi Eve :3"   # speaker prefix stripped
    assert reply.tokens == 15
    messages = llm.calls_to(CHAT)[0][1]
    assert len(messages) == 1 and messages[0]["role"] == "user"   # one user message, like the original
    prompt = messages[0]["content"]
    assert prompt.startswith("You are a female furry named Bong")
    assert "You're allowed to swear." in prompt
    assert "Here is the user's name: Eve and userID: 1" in prompt
    assert "User message:\nbong say hi. please" in prompt
    assert "The user replied to: Rado at" in prompt and ": lol" in prompt
    assert prompt.index("Rado at 14:30: second") < prompt.index("Rado at 14:30: first")  # newest first
    assert "{" not in prompt                         # every placeholder filled

    entries = bong.history.get(100).entries
    assert [e.author_name for e in entries] == ["Rado", "Rado", "Eve", "Bong"]
    assert entries[-1].is_bong


async def test_empty_reply_is_retried_once():
    llm = FakeLLM({CHAT: ["   ", "second try :3"]})
    reply = await make_bong(llm).respond(make_turn("bong?"))
    assert reply.text == "second try :3"
    assert len(llm.calls_to(CHAT)) == 2


async def test_user_text_cannot_inject_template_fields():
    assert fill("A {x} B {y}", x="{y}", y="Y") == "A {y} B Y"


async def test_channels_are_independent():
    llm = FakeLLM()
    bong = make_bong(llm)
    await asyncio.gather(
        bong.respond(make_turn("bong 1", channel_id=1, author_name="A", author_id=10)),
        bong.respond(make_turn("bong 2", channel_id=2, author_name="B", author_id=20)),
    )
    assert [e.author_name for e in bong.history.get(1).entries] == ["A", "Bong"]
    assert [e.author_name for e in bong.history.get(2).entries] == ["B", "Bong"]
    for _, messages, _ in llm.calls_to(CHAT):
        prompt = messages[0]["content"]
        speaker = prompt.split("Here is the user's name: ")[1].split(" ")[0]
        assert f"User message:\nbong {'1' if speaker == 'A' else '2'}" in prompt


# --- memory seam ------------------------------------------------------------

async def test_memory_store_is_queried_and_observed():
    class FakeMemory:
        def __init__(self):
            self.recalled_for, self.observed = [], []

        async def recall(self, turn, limit=8):
            self.recalled_for.append(turn.author_id)
            return [Memory("loves dubstep", about_user_id=turn.author_id, about_name=turn.author_name)]

        async def observe(self, turn, reply):
            self.observed.append((turn.author_id, reply))

    llm = FakeLLM({CHAT: ["noted :3"]})
    memory = FakeMemory()
    await make_bong(llm, memory=memory).respond(make_turn("bong remember"))

    assert memory.recalled_for == [1]
    assert memory.observed == [(1, "noted :3")]
    assert "- loves dubstep (about Eve)" in llm.calls_to(CHAT)[0][1][0]["content"]


async def test_memory_block_is_empty_when_store_is_empty():
    llm = FakeLLM()
    await make_bong(llm).respond(make_turn("bong"))
    prompt = llm.calls_to(CHAT)[0][1][0]["content"]
    after = prompt.split("Long-term memories (facts you've saved about people and things if any):\n")[1]
    assert after.startswith("\n\nHere is the user's name")


# --- history ----------------------------------------------------------------

def entry(i):
    return HistoryEntry(f"user{i}", f"msg {i}", datetime(2026, 1, 1, 12, 0))


async def test_history_folds_oldest_into_summary():
    seen = []

    async def summarize(lines):
        seen.append(lines)
        return "they talked"

    store = HistoryStore(summarize, max_entries=5, chunk=3, max_summaries=2)
    for i in range(5):
        store.add(1, entry(i))
    await store.wait_idle()

    ch = store.get(1)
    assert [e.text for e in ch.entries] == ["msg 3", "msg 4"]
    assert ch.summaries == ["they talked"]
    assert seen[0][0] == "user0 at 12:00: msg 0"


async def test_history_keeps_newest_summaries_and_survives_failures():
    calls = 0

    async def summarize(lines):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("model down")
        return f"summary {calls}"

    store = HistoryStore(summarize, max_entries=2, chunk=2, max_summaries=2)
    for i in range(8):
        store.add(1, entry(i))
        await store.wait_idle()
    ch = store.get(1)
    assert ch.summaries == ["summary 3", "summary 4"]
    assert len(ch.entries) <= 2


# --- small helpers ----------------------------------------------------------

def test_cooldown():
    cd = Cooldown(60)
    assert cd.hit(1, now=0) == 0
    assert cd.hit(1, now=10) == 50
    assert cd.hit(2, now=10) == 0
    assert cd.hit(1, now=61) == 0


def test_clean_reply():
    assert clean_reply("Bong: hi") == "hi"
    assert clean_reply("bong at 9:05:  *boop*") == "*boop*"
    assert clean_reply("Bong is happy :3") == "Bong is happy :3"
