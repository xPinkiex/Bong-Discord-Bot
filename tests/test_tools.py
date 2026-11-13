from __future__ import annotations

from datetime import datetime, timezone

from bong.conversation.bong import MAX_TOOL_ROUNDS, Bong
from bong.llm import ChatResult, ToolCall
from bong.tools import Tool
from bong.tools.time import current_time_tool

from conftest import make_turn

FIXED = datetime(2026, 11, 13, 5, 41, tzinfo=timezone.utc)


class ScriptedLLM:
    """Returns the scripted results in order; records messages and offered tools."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def chat(self, model, messages, tools=None, **options):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return self.results.pop(0)


def call(name, **args):
    return ChatResult(text="", tool_calls=(ToolCall(name, args),),
                      raw_message={"role": "assistant", "content": "",
                                   "tool_calls": [{"function": {"name": name, "arguments": args}}]})


async def test_time_tool_uses_saved_timezone():
    tool = current_time_tool(lambda uid: 1.0 if uid == 1 else None, now=lambda: FIXED)
    out = await tool.handler(make_turn(author_id=1), {})
    assert out == "Current time for Eve (UTC+1): 06:41 on Friday, 13 November 2026"


async def test_time_tool_without_timezone_uses_bongs_clock():
    tool = current_time_tool(lambda uid: None, now=lambda: FIXED)
    out = await tool.handler(make_turn(author_id=2, author_name="Rado"), {})
    assert out.startswith("Current time where Bong is:") and "Rado hasn't set a timezone" in out


async def test_tool_loop_runs_tool_and_feeds_result_back():
    llm = ScriptedLLM([call("current_time"), ChatResult(text="it's 06:41 xP", prompt_tokens=4, output_tokens=2)])
    tool = current_time_tool(lambda uid: 1.0, now=lambda: FIXED)
    bong = Bong(llm, chat_model="c", helper_model="h", tools=[tool])

    reply = await bong.respond(make_turn("bong what time is it"))

    assert reply.text == "it's 06:41 xP"
    assert [t["function"]["name"] for t in llm.calls[0]["tools"]] == ["current_time"]
    second = llm.calls[1]["messages"]
    assert second[1]["role"] == "assistant" and second[1]["tool_calls"]
    assert second[2] == {"role": "tool", "content": "Current time for Eve (UTC+1): 06:41 on Friday, 13 November 2026",
                         "tool_name": "current_time"}
    # Tool traffic isn't stored in the channel history.
    assert [e.author_name for e in bong.history.get(100).entries] == ["Eve", "Bong"]


async def test_unknown_tool_and_tool_errors_are_reported_to_the_model():
    async def broken(turn, args):
        raise RuntimeError("kaboom")
    llm = ScriptedLLM([call("nope"), call("broken"), ChatResult(text="oops :3")])
    bong = Bong(llm, chat_model="c", helper_model="h", tools=[Tool("broken", "breaks", broken)])
    assert (await bong.respond(make_turn("bong"))).text == "oops :3"
    tool_msgs = [m["content"] for m in llm.calls[2]["messages"] if m["role"] == "tool"]
    assert "no tool called 'nope'" in tool_msgs[0]
    assert "kaboom" in tool_msgs[1]


async def test_tool_loop_is_capped():
    llm = ScriptedLLM([call("current_time")] * MAX_TOOL_ROUNDS + [ChatResult(text="fine, no more tools")])
    bong = Bong(llm, chat_model="c", helper_model="h", tools=[current_time_tool(lambda u: None)])
    assert (await bong.respond(make_turn("bong"))).text == "fine, no more tools"
    assert llm.calls[-1]["tools"] is None
    assert len(llm.calls) == MAX_TOOL_ROUNDS + 1


async def test_no_tools_means_none_offered():
    llm = ScriptedLLM([ChatResult(text="hi")])
    await Bong(llm, chat_model="c", helper_model="h").respond(make_turn("bong"))
    assert llm.calls[0]["tools"] is None


# --- set_alias -----------------------------------------------------------------

async def test_set_alias_for_self_others_and_clearing(tmp_path):
    from bong.tools.people import set_alias_tool
    from bong.users import UserStore

    users = UserStore(tmp_path / "users.json", owner_id=99)
    users.load()
    users.add_tag(5, "admin")
    names = {"rado": (2, "RadonFox"), "eve": (1, "Not Reed")}
    tool = set_alias_tool(users, lambda n, t: names.get(n.lower()))
    eve = make_turn(author_id=1, author_name="Not Reed")

    assert await tool.handler(eve, {"alias": "Eve"}) == 'From now on, call Not Reed "Eve".'
    assert users.alias(1) == "Eve"
    out = await tool.handler(eve, {"alias": "Stinky", "about": "Rado"})
    assert "can't choose what RadonFox is called" in out and users.alias(2) is None

    admin = make_turn(author_id=5, author_name="Boss")
    await tool.handler(admin, {"alias": "Radon", "about": "rado"})
    assert users.alias(2) == "Radon"
    assert "Couldn't find anyone" in await tool.handler(admin, {"alias": "X", "about": "nobody"})

    await tool.handler(make_turn(author_id=1, author_name="Eve"), {"alias": ""})
    assert users.alias(1) is None
    assert "too long" in await tool.handler(eve, {"alias": "x" * 40})
