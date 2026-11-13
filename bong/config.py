"""Runtime settings, read once at startup from the environment (and .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Eve — receives DM approval requests and is always admin.
DEFAULT_OWNER_ID = 273761843544064000

# Ollama connection modes (chosen at launch: `python -m bong` or `python -m bong --direct`)
LOCAL, DIRECT = "local", "direct"
DIRECT_HOST = "https://ollama.com"

# Default models per mode — the same models either way (glm-5.1 and gemma3 were
# retired by Ollama in 2026). Local Ollama reaches cloud models by their cloud tag;
# the direct API (ollama.com) uses the plain names.
DEFAULT_MODELS = {
    LOCAL: {"chat": "glm-5.2:cloud", "helper": "gemma4:31b-cloud"},
    DIRECT: {"chat": "glm-5.2", "helper": "gemma4:31b"},
}


TOKEN_PLACEHOLDER = "paste-your-discord-bot-token-here"
API_KEY_PLACEHOLDER = "paste-your-ollama-api-key-here"

ENV_TEMPLATE = f"""\
# Bong settings. Lines starting with # are ignored.

# Required: the bot's token
# (Discord developer portal -> your app -> Bot -> Reset Token)
DISCORD_TOKEN={TOKEN_PLACEHOLDER}

# Discord user ID that gets DM approval requests and is always admin
OWNER_ID={DEFAULT_OWNER_ID}

# Local mode (python -m bong): address of your Ollama server
OLLAMA_HOST=127.0.0.1:11434

# Direct mode (python -m bong --direct): API key from ollama.com/settings/keys
OLLAMA_API_KEY={API_KEY_PLACEHOLDER}

# Optional overrides (remove the # to use)
# BONG_CHAT_MODEL=glm-5.2:cloud          # direct mode: glm-5.2
# BONG_HELPER_MODEL=gemma4:31b-cloud     # direct mode: gemma4:31b
# BONG_COOLDOWN_SECONDS=60
# BONG_MEMORY=off                        # turn long-term memory off
"""


def create_env_file(path: Path) -> None:
    """Write a .env with placeholders, readable only by the owner (it will hold secrets)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(ENV_TEMPLATE)


def _normalize_host(host: str) -> str:
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host


@dataclass(frozen=True)
class Settings:
    discord_token: str
    owner_id: int = DEFAULT_OWNER_ID
    ollama_mode: str = LOCAL
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_api_key: str = field(default="", repr=False)   # direct mode only

    # Models
    chat_model: str = DEFAULT_MODELS[LOCAL]["chat"]       # Bong's replies
    helper_model: str = DEFAULT_MODELS[LOCAL]["helper"]   # classifier + history summaries

    # Behaviour
    command_prefix: str = "@"
    cooldown_seconds: float = 60.0             # for users without llm_fast
    history_size: int = 30                     # messages kept per channel before summarizing
    summarize_chunk: int = 10                  # oldest messages folded into one summary
    max_summaries: int = 5                     # summaries kept per channel
    classifier_context: int = 7                # recent messages shown to the classifier
    memory_enabled: bool = True                # long-term memory (BONG_MEMORY=off to disable)

    # Paths
    data_dir: Path = field(default=PROJECT_ROOT / "bong_data")
    user_data_dir: Path = field(default=PROJECT_ROOT / "bong_user_data")

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @classmethod
    def from_env(cls, direct: bool = False, env_file: Path | None = None) -> "Settings":
        """Read settings from the environment / .env.

        Local mode (default) talks to the Ollama server at OLLAMA_HOST.
        Direct mode talks to ollama.com's API and needs OLLAMA_API_KEY.
        BONG_CHAT_MODEL / BONG_HELPER_MODEL override the models in either mode.
        """
        env_path = Path(env_file or PROJECT_ROOT / ".env")
        if not env_path.exists() and not os.getenv("DISCORD_TOKEN"):
            create_env_file(env_path)
            raise SystemExit(f"Created {env_path} with placeholders. "
                             f"Fill in DISCORD_TOKEN (and OLLAMA_API_KEY for --direct), then run again.")
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
        except ImportError:  # python-dotenv is optional
            pass

        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token or token == TOKEN_PLACEHOLDER:
            raise SystemExit(f"DISCORD_TOKEN is not set — put the bot's token in {env_path}.")

        mode = DIRECT if direct else LOCAL
        kwargs: dict = {
            "discord_token": token,
            "ollama_mode": mode,
            "chat_model": DEFAULT_MODELS[mode]["chat"],
            "helper_model": DEFAULT_MODELS[mode]["helper"],
        }
        if v := os.getenv("OWNER_ID"):
            kwargs["owner_id"] = int(v)
        if direct:
            key = os.getenv("OLLAMA_API_KEY", "").strip()
            if not key or key == API_KEY_PLACEHOLDER:
                raise SystemExit("--direct needs OLLAMA_API_KEY (create one at ollama.com/settings/keys).")
            kwargs["ollama_host"] = DIRECT_HOST
            kwargs["ollama_api_key"] = key
        elif v := os.getenv("OLLAMA_HOST"):
            kwargs["ollama_host"] = _normalize_host(v)
        if v := os.getenv("BONG_CHAT_MODEL"):
            kwargs["chat_model"] = v
        if v := os.getenv("BONG_HELPER_MODEL"):
            kwargs["helper_model"] = v
        if (v := os.getenv("BONG_MEMORY")) and v.strip().lower() in ("0", "off", "false", "no"):
            kwargs["memory_enabled"] = False
        if v := os.getenv("BONG_COOLDOWN_SECONDS"):
            kwargs["cooldown_seconds"] = float(v)
        return cls(**kwargs)
