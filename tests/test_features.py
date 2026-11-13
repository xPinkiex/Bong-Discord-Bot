"""Timezones and time parsing, reminders, files/images, web safety, stats and shutdown."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from bong.conversation.turn import Attachment
from bong.llm import ChatResult
from bong.music.library import MusicLibrary
from bong.reminders import ReminderStore
from bong.timeparse import PastDateError, describe_timezone, format_duration, parse_duration, parse_time, parse_timezone
from bong.tools.admin import shutdown_tool, stats_tool
from bong.tools.files import file_tools
from bong.tools.reminders import reminder_tools
from bong.tools.time import timezone_tools
from bong.tools.web import html_to_text, is_public

from conftest import FakeLLM, make_turn

WARSAW = "Europe/Warsaw"
NOW = datetime(2026, 11, 13, 7, 5, tzinfo=timezone.utc)   # Friday 08:05 in Warsaw


def local(ts, tz=WARSAW):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ZoneInfo(tz)).strftime("%a %Y-%m-%d %H:%M")


# --- time parsing ------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("tomorrow at 3pm", "Sat 2026-11-14 15:00"),
    ("tomorrow 8am", "Sat 2026-11-14 08:00"),
    ("tomorrow at 18", "Sat 2026-11-14 18:00"),
    ("at 8am", "Sat 2026-11-14 08:00"),           # already past today → tomorrow (old bot crashed here)
    ("at 9:30", "Fri 2026-11-13 09:30"),
    ("friday at 12:00", "Fri 2026-11-13 12:00"),  # today is Friday and noon is still ahead
    ("friday at 7am", "Fri 2026-11-20 07:00"),    # ...but 7am has passed
    ("next friday at 12", "Fri 2026-11-20 12:00"),
    ("next monday at 9am", "Mon 2026-11-16 09:00"),
    ("monday", "Mon 2026-11-16 09:00"),           # a day alone means 09:00
    ("sunday at noon", "Sun 2026-11-15 12:00"),
    ("31.5 at 10:22", "Mon 2027-05-31 10:22"),    # day.month already past this year → next year
    ("24.12 at 20:00", "Thu 2026-12-24 20:00"),
    ("10.7.2027", "Sat 2027-07-10 09:00"),
    ("june 5 at 3pm", "Sat 2027-06-05 15:00"),
    ("5th of december at 17:00", "Sat 2026-12-05 17:00"),
    ("2026-12-24 18:00", "Thu 2026-12-24 18:00"),
    ("6/5/2027", "Sat 2027-06-05 09:00"),         # slashes: month/day, and summer time is respected
])
def test_parse_time(text, expected):
    assert local(parse_time(text, WARSAW, NOW)) == expected


def test_parse_time_fixed_offset_is_real_utc():
    # The old bot turned local times into timestamps using the *server's* timezone.
    ts = parse_time("tomorrow at 15:00", -5.0, NOW)
    assert datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M") == "2026-11-14 20:00"


def test_parse_time_rejects_nonsense_and_the_past():
    assert parse_time("whenever", WARSAW, NOW) is None
    assert parse_time("31.2", WARSAW, NOW) is None
    with pytest.raises(PastDateError):
        parse_time("today at 7am", WARSAW, NOW)
    with pytest.raises(PastDateError):
        parse_time("2025-01-01", WARSAW, NOW)


def test_timezones_and_durations():
    assert parse_timezone("Warsaw") == WARSAW and parse_timezone("  new   york ") == "America/New_York"
    assert parse_timezone("Europe/Lisbon") == "Europe/Lisbon"
    assert parse_timezone("CET") == 1.0 and parse_timezone("UTC+5:30") == 5.5 and parse_timezone("-7") == -7.0
    assert parse_timezone("UTC+20") is None and parse_timezone("banana") is None
    assert describe_timezone(WARSAW, NOW) == "Europe/Warsaw (UTC+1)"
    assert describe_timezone(WARSAW, datetime(2026, 7, 1, tzinfo=timezone.utc)) == "Europe/Warsaw (UTC+2)"
    assert parse_duration("1h 30m") == 5400 and parse_duration("2 days") == 172800 and parse_duration("soon") is None
    assert format_duration(5400) == "1 hour, 30 minutes" and format_duration(42) == "42 seconds"


async def test_timezone_tools():
    saved = {}
    tools = {t.name: t.handler for t in timezone_tools(saved.get, saved.__setitem__)}
    eve = make_turn(author_id=1, author_name="Eve")
    assert "hasn't set a timezone" in await tools["get_timezone"](eve, {})
    assert (await tools["set_timezone"](eve, {"timezone": "Warsaw"})).startswith("Timezone for Eve set to Europe/Warsaw")
    assert saved[1] == WARSAW
    assert "Couldn't understand" in await tools["set_timezone"](eve, {"timezone": "Narnia"})


# --- reminders -------------------------------------------------------------------------------

def test_reminder_store_is_compatible_and_delivers_late(tmp_path):
    path = tmp_path / "reminders.json"
    path.write_text('[{"user_id": 1, "username": "Eve", "message": "old one", "due_at": 1000.0}]')
    store = ReminderStore(path)
    store.load()
    assert store.count() == 1                      # the old bot dropped overdue reminders on load
    store.add(1, "Eve", "feed the cat", 5000)
    store.add(2, "Rado", "walk", 3000)
    assert [r["message"] for r in store.pop_due(4000)] == ["old one", "walk"]
    assert [r["message"] for r in store.for_user(1)] == ["feed the cat"]
    again = ReminderStore(path)
    again.load()
    assert again.count() == 1


async def test_reminder_tools(tmp_path):
    store = ReminderStore(tmp_path / "r.json")
    store.load()
    zones = {1: WARSAW}
    tools = {t.name: t.handler for t in reminder_tools(store, zones.get)}
    eve, rado = make_turn(author_id=1, author_name="Eve"), make_turn(author_id=2, author_name="Rado")

    out = await tools["set_reminder"](eve, {"message": "feed the cat", "time": "tomorrow at 18:00"})
    assert out.startswith("Reminder set for Eve: 'feed the cat' at 18:00 on") and "Europe/Warsaw" in out
    assert "haven't set their timezone" in await tools["set_reminder"](rado, {"message": "x", "time": "tomorrow at 9"})
    assert "in 2 hours" in await tools["set_reminder"](rado, {"message": "walk", "time_delta": "2 hours"})
    assert "in the past" in await tools["set_reminder"](eve, {"message": "x", "time": "2020-01-01"})
    assert "Couldn't understand" in await tools["set_reminder"](eve, {"message": "x", "time_delta": "a while"})

    assert "feed the cat" in await tools["list_reminders_tool"](eve, {})
    assert "walk" not in await tools["list_reminders_tool"](eve, {})
    assert await tools["cancel_reminder"](eve, {"query": "dog"}) == "No reminders matching 'dog'."
    assert await tools["cancel_reminder"](eve, {"query": "cat"}) == "Cancelled: 'feed the cat'."
    assert "no pending reminders" in await tools["list_reminders_tool"](eve, {})


async def test_due_reminders_are_dmed():
    from bong.bot.background import BackgroundCog
    sent = []
    user = SimpleNamespace(send=AsyncMock(side_effect=sent.append))
    reminders = SimpleNamespace(pop_due=lambda now: [{"user_id": 1, "message": "feed the cat", "due_at": now - 1},
                                                     {"user_id": 1, "message": "old", "due_at": now - 3600}])
    bot = SimpleNamespace(reminders=reminders, get_user=lambda uid: user)
    cog = BackgroundCog.__new__(BackgroundCog)
    cog.bot = bot
    await BackgroundCog.deliver_reminders.coro(cog)
    assert sent[0] == "⏰ **Reminder**: feed the cat"
    assert sent[1].startswith("⏰ **Reminder**: old (late")


# --- images, texts, reactions ------------------------------------------------------------------

def image_bytes(fmt="PNG", size=(64, 48), frames=1):
    import io
    from PIL import Image
    imgs = [Image.new("RGB", size, (0, 200 - 40 * i, 0)) for i in range(frames)]
    out = io.BytesIO()
    if frames > 1:
        imgs[0].save(out, format=fmt, save_all=True, append_images=imgs[1:], duration=100, loop=0)
    else:
        imgs[0].save(out, format=fmt)
    return out.getvalue()


@pytest.fixture
def file_rig(tmp_path):
    images = MusicLibrary(tmp_path / "saved_images", (".png", ".jpg", ".webp"))
    texts = MusicLibrary(tmp_path / "saved_texts", (".txt", ".py"))
    llm = FakeLLM({"vision": ["A green square that says HELLO BONG.", "Green_square_hello_bong_text"]})
    blobs = {"https://cdn/x.png": image_bytes(), "https://cdn/notes.py": b"print('hi')\n" * 3,
             "https://cdn/anim.webp": image_bytes("WEBP", frames=3), "https://cdn/fake.png": b"<html>nope</html>"}

    async def fetch(att, limit):
        return blobs[att.url]
    tools = {t.name: t.handler for t in file_tools(llm, "vision", images, texts, fetch=fetch)}
    return tools, llm, images, texts


async def test_describe_image_uses_vision_and_saves(file_rig):
    tools, llm, images, _ = file_rig
    turn = make_turn(attachments=(Attachment("x.png", "https://cdn/x.png", "image/png", 9, 256, 256),))
    out = await tools["describe_image"](turn, {"question": "What is this?"})
    assert out == "Description of 'x.png': A green square that says HELLO BONG."
    sent = llm.calls[0][1][0]
    assert sent["content"] == "What is this?" and sent["images"]
    assert [p.name for p in images.files()] == ["Green_square_hello_bong_text.png"]
    assert "no image attached" in await tools["describe_image"](make_turn(), {})


async def test_describe_image_handles_animated_webp_and_non_images(file_rig):
    import base64, io
    from PIL import Image
    tools, llm, images, _ = file_rig
    anim = make_turn(attachments=(Attachment("dbb5de08.webp", "https://cdn/anim.webp", "image/webp"),))
    assert (await tools["describe_image"](anim, {})).startswith("Description of 'dbb5de08.webp'")
    sent = base64.b64decode(llm.calls[0][1][0]["images"][0])
    assert Image.open(io.BytesIO(sent)).format == "PNG"          # converted, first frame
    fake = make_turn(attachments=(Attachment("x.png", "https://cdn/fake.png", "image/png"),))
    assert await tools["describe_image"](fake, {}) == "Couldn't look at 'x.png': that file isn't an image I can open."


def test_prepare_image_shrinks_and_flattens():
    import io
    from PIL import Image
    from bong.tools.files import MAX_SIDE, prepare_image
    big = Image.open(io.BytesIO(prepare_image(image_bytes("JPEG", size=(4000, 3000)))))
    assert big.format == "PNG" and max(big.size) == MAX_SIDE and big.size == (1600, 1200)
    gif = Image.open(io.BytesIO(prepare_image(image_bytes("GIF", frames=4))))
    assert getattr(gif, "n_frames", 1) == 1


async def test_fetch_attachment_reads_the_whole_file():
    """Regression: a single content.read(n) returned only the first chunk, corrupting bigger images."""
    import asyncio, os
    from aiohttp import web
    from bong.tools.files import fetch_attachment
    payload = os.urandom(3 * 1024 * 1024)

    async def slow(request):
        resp = web.StreamResponse()
        await resp.prepare(request)
        for i in range(0, len(payload), 100_000):
            await resp.write(payload[i:i + 100_000])
            await asyncio.sleep(0.001)
        return resp
    app = web.Application()
    app.router.add_get("/img.webp", slow)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        got = await fetch_attachment(Attachment("img.webp", f"http://127.0.0.1:{port}/img.webp"), 10 * 1024 * 1024)
        assert got == payload
        with pytest.raises(ValueError, match="too big"):
            await fetch_attachment(Attachment("img.webp", f"http://127.0.0.1:{port}/img.webp"), 1024 * 1024)
    finally:
        await runner.cleanup()


async def test_read_text_file_saves_and_returns_content(file_rig):
    tools, _, _, texts = file_rig
    turn = make_turn(attachments=(Attachment("notes.py", "https://cdn/notes.py", "text/x-python", 30),))
    out = await tools["read_text_file"](turn, {})
    assert out.startswith("Contents of 'notes.py':\nprint('hi')")
    assert [p.name for p in texts.files()] == ["notes.py"]
    assert "doesn't exist" in await tools["read_text_file"](turn, {"index": 3})


async def test_send_saved_files_and_react_use_the_outbox(file_rig):
    tools, _, images, _ = file_rig
    images.folder.mkdir(parents=True)
    (images.folder / "Orange_cat_sleeping.png").write_bytes(b"x")
    turn = make_turn()
    assert "1 saved images" in await tools["list_images"](turn, {})
    assert await tools["send_image"](turn, {"name": "orange cat"}) == "Sending 'Orange_cat_sleeping' with your reply."
    assert "No saved text file" in await tools["send_text"](turn, {"name": "nope"})
    await tools["react"](turn, {"emojis": "❤️ 😂"})
    assert [p.name for p in turn.outbox.files] == ["Orange_cat_sleeping.png"]
    assert turn.outbox.reactions == ["❤️", "😂"]


def test_attachment_kinds_and_prompt_block():
    import asyncio
    from bong.conversation.history import ChannelHistory
    from bong.conversation.prompt import MessageSection, PromptContext
    atts = (Attachment("a.png", content_type="image/png", width=10, height=20), Attachment("b.txt"),
            Attachment("c.mp4", content_type="video/mp4"), Attachment("d.zip"))
    assert [a.kind for a in atts] == ["image", "text", "video", "file"]
    block = asyncio.run(MessageSection().render(PromptContext(make_turn(attachments=atts), ChannelHistory())))["attachments"]
    assert "[Image 0: a.png (10x20)], [Text file 0: b.txt], [Video: c.mp4], [File: d.zip]" in block
    assert "ALWAYS call describe_image" in block and "ALWAYS call read_text_file" in block


# --- web safety ---------------------------------------------------------------------------------

@pytest.mark.parametrize("ip, public", [
    ("93.184.215.14", True), ("1.1.1.1", True), ("127.0.0.1", False), ("10.0.0.5", False),
    ("192.168.1.1", False), ("172.16.0.1", False), ("169.254.169.254", False), ("100.64.0.1", False),
    ("0.0.0.0", False), ("::1", False), ("fd00::1", False), ("::ffff:127.0.0.1", False), ("224.0.0.1", False),
])
def test_only_public_addresses_are_fetched(ip, public):
    assert is_public(ip) is public


def test_html_to_text_drops_scripts():
    html = "<html><head><title>Hi</title><style>p{}</style></head><body><p>One</p><script>evil()</script><p>Two</p></body></html>"
    assert html_to_text(html) == "Title: Hi\n\nOne\nTwo"


async def test_summarize_url_refuses_local_addresses():
    from bong.tools.web import web_tools
    tools = {t.name: t.handler for t in web_tools(FakeLLM(), "helper")}
    for url in ("http://127.0.0.1:11434/api/tags", "http://localhost/", "http://192.168.0.1/admin"):
        assert (await tools["summarize_url"](make_turn(), {"url": url})).startswith("Not fetching that")


# --- stats and shutdown ----------------------------------------------------------------------------

async def test_stats_and_shutdown():
    async def collect():
        return {"started_at": time.time() - 90061, "memories": 12, "known_users": 4, "reminders": 2, "songs": 30,
                "total_plays": 99, "top_songs": [("Jersey", 40)], "total_tokens": 123456,
                "top_users": [("Eve", 100000)]}
    out = await stats_tool(collect).handler(make_turn(), {})
    assert "Uptime: 1d 1h 1m" in out and "1. Jersey (40 plays)" in out and "1. Eve (100,000 tokens)" in out

    tool = shutdown_tool(lambda uid: uid == 1)
    nope = make_turn(author_id=2, author_name="Rado")
    assert "isn't an admin" in await tool.handler(nope, {}) and not nope.outbox.shutdown
    yes = make_turn(author_id=1)
    await tool.handler(yes, {})
    assert yes.outbox.shutdown
