from __future__ import annotations

import json

import pytest

from bong.channels import ActiveChannels
from bong.storage import JsonFile
from bong.textutil import split_message
from bong.users import UserStore

OWNER = 273761843544064000


def test_json_file_roundtrip_and_backup(tmp_path):
    f = JsonFile(tmp_path / "x.json", default={})
    f.load()
    f.data["a"] = 1
    f.save()
    f.data["a"] = 2
    f.save()
    assert json.loads((tmp_path / "x.json").read_text()) == {"a": 2}
    assert json.loads((tmp_path / "x.json.bak").read_text()) == {"a": 1}


def test_json_file_falls_back_to_backup(tmp_path):
    (tmp_path / "x.json").write_text("{broken")
    (tmp_path / "x.json.bak").write_text('{"ok": true}')
    assert JsonFile(tmp_path / "x.json", default={}).load() == {"ok": True}


def test_user_store_reads_old_format_and_keeps_unknown_fields(tmp_path):
    path = tmp_path / "users.json"
    path.write_text(json.dumps({
        "42": {"allowed": ["llm", "music"], "timezone": 2, "e621_subs": ["x"]},
        str(OWNER): {"allowed": []},
    }))
    users = UserStore(path, OWNER)
    users.load()
    assert users.has(42, "llm") and not users.has(42, "llm_fast")
    assert users.is_admin(OWNER)

    users.add_tokens(42, 100, "Rado")
    saved = json.loads(path.read_text())
    assert saved["42"]["timezone"] == 2 and saved["42"]["e621_subs"] == ["x"]
    assert saved["42"]["tokens"] == 100 and saved[str(OWNER)]["allowed"] == ["admin"]


def test_tag_rules(tmp_path):
    users = UserStore(tmp_path / "u.json", OWNER)
    users.load()
    assert not users.has(1, "llm")
    users.add_tag(1, "llm_fast")
    assert users.has(1, "llm") and not users.is_admin(1)
    users.add_tag(2, "admin")
    assert users.has(2, "llm_fast")
    assert users.remove_tag(1, "llm_fast") and not users.has(1, "llm")
    assert not users.remove_tag(OWNER, "admin")
    with pytest.raises(ValueError):
        users.add_tag(1, "e621")


def test_active_channels_persist(tmp_path):
    ch = ActiveChannels(tmp_path / "c.json")
    ch.load()
    assert ch.toggle(5) is True and 5 in ch
    again = ActiveChannels(tmp_path / "c.json")
    again.load()
    assert 5 in again
    assert again.toggle(5) is False and 5 not in again


def test_split_message():
    assert split_message("short") == ["short"]
    text = ("word " * 900).strip()
    chunks = split_message(text, limit=2000)
    assert all(len(c) <= 2000 for c in chunks)
    assert " ".join(chunks) == text
    lines = "\n".join(["x" * 1500, "y" * 1500])
    assert split_message(lines) == ["x" * 1500, "y" * 1500]
    assert split_message("z" * 4500) == ["z" * 2000, "z" * 2000, "z" * 500]
