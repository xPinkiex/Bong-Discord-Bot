from __future__ import annotations

import pytest
from aiohttp import web

from bong.config import DEFAULT_MODELS, DIRECT_HOST, Settings
from bong.llm import LLMError, OllamaClient

ENV_VARS = ("DISCORD_TOKEN", "OLLAMA_HOST", "OLLAMA_API_KEY", "BONG_CHAT_MODEL", "BONG_HELPER_MODEL", "OWNER_ID")


@pytest.fixture
def env(monkeypatch, tmp_path):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    empty_env = tmp_path / ".env"
    empty_env.write_text("")
    return monkeypatch, empty_env


def test_local_is_default_and_uses_ollama_host(env):
    mp, f = env
    mp.setenv("OLLAMA_HOST", "192.168.1.20:11434")
    mp.setenv("OLLAMA_API_KEY", "should-be-ignored")
    s = Settings.from_env(env_file=f)
    assert s.ollama_mode == "local"
    assert s.ollama_host == "http://192.168.1.20:11434"
    assert s.ollama_api_key == ""
    assert s.chat_model == DEFAULT_MODELS["local"]["chat"]


def test_direct_uses_key_and_direct_models(env):
    mp, f = env
    mp.setenv("OLLAMA_HOST", "192.168.1.20:11434")
    mp.setenv("OLLAMA_API_KEY", "secret")
    s = Settings.from_env(direct=True, env_file=f)
    assert s.ollama_mode == "direct"
    assert s.ollama_host == DIRECT_HOST
    assert s.ollama_api_key == "secret"
    assert s.chat_model == DEFAULT_MODELS["direct"]["chat"]
    assert "secret" not in repr(s)


def test_direct_without_key_refuses_to_start(env):
    _, f = env
    with pytest.raises(SystemExit, match="OLLAMA_API_KEY"):
        Settings.from_env(direct=True, env_file=f)


def test_model_override_applies_in_both_modes(env):
    mp, f = env
    mp.setenv("OLLAMA_API_KEY", "k")
    mp.setenv("BONG_CHAT_MODEL", "my-model")
    assert Settings.from_env(env_file=f).chat_model == "my-model"
    assert Settings.from_env(direct=True, env_file=f).chat_model == "my-model"


# --- client against a tiny fake Ollama server --------------------------------

@pytest.fixture
async def fake_ollama():
    seen = []

    async def chat(request):
        seen.append((request.headers.get("Authorization"), await request.json()))
        if request.headers.get("Authorization") == "Bearer bad":
            return web.Response(status=401, text="unauthorized")
        return web.json_response({"message": {"content": "hi :3"}, "prompt_eval_count": 3, "eval_count": 2})

    async def tags(request):
        return web.json_response({"models": [{"name": "glm-5.2"}]})

    app = web.Application()
    app.router.add_post("/api/chat", chat)
    app.router.add_get("/api/tags", tags)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", seen
    await runner.cleanup()


async def test_client_sends_key_and_skips_keep_alive_in_direct_mode(fake_ollama):
    url, seen = fake_ollama
    client = OllamaClient(url, api_key="secret")
    result = await client.chat("glm-5.2", [{"role": "user", "content": "hi"}])
    assert await client.list_models() == ["glm-5.2"]
    await client.close()
    auth, body = seen[0]
    assert auth == "Bearer secret"
    assert "keep_alive" not in body
    assert result.text == "hi :3" and result.total_tokens == 5


async def test_local_client_sends_no_auth_and_keeps_model_loaded(fake_ollama):
    url, seen = fake_ollama
    client = OllamaClient(url)
    await client.chat("m", [{"role": "user", "content": "hi"}])
    await client.close()
    auth, body = seen[0]
    assert auth is None and body["keep_alive"] == -1


async def test_bad_key_gives_clear_error(fake_ollama):
    url, _ = fake_ollama
    client = OllamaClient(url, api_key="bad")
    with pytest.raises(LLMError, match="OLLAMA_API_KEY"):
        await client.chat("m", [{"role": "user", "content": "hi"}])
    await client.close()


# --- first run: .env generation ---------------------------------------------

def test_missing_env_file_is_created_with_placeholders(monkeypatch, tmp_path):
    import os, stat
    from bong.config import TOKEN_PLACEHOLDER
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / ".env"
    with pytest.raises(SystemExit, match="Created"):
        Settings.from_env(env_file=path)
    text = path.read_text()
    assert f"DISCORD_TOKEN={TOKEN_PLACEHOLDER}" in text and "OLLAMA_API_KEY=" in text
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    # Second run with placeholders still in place: refuses, doesn't overwrite.
    with pytest.raises(SystemExit, match="DISCORD_TOKEN is not set"):
        Settings.from_env(env_file=path)
    assert path.read_text() == text


def test_placeholder_api_key_is_rejected_in_direct_mode(monkeypatch, tmp_path):
    from bong.config import API_KEY_PLACEHOLDER
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / ".env"
    path.write_text(f"DISCORD_TOKEN=real\nOLLAMA_API_KEY={API_KEY_PLACEHOLDER}\n")
    assert Settings.from_env(env_file=path).ollama_mode == "local"   # local doesn't need it
    with pytest.raises(SystemExit, match="OLLAMA_API_KEY"):
        Settings.from_env(direct=True, env_file=path)


def test_no_env_file_needed_when_token_is_in_environment(env, tmp_path):
    path = tmp_path / "nothing-here" / ".env"
    assert Settings.from_env(env_file=path).discord_token == "tok"
    assert not path.exists()
