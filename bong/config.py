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

# Default models per mode. Local Ollama reaches cloud models via the ":cloud"/"-cloud"
# names; the direct API (ollama.com) has its own catalogue, so it gets its own defaults.
DEFAULT_MODELS = {
    LOCAL: {"chat": "glm-5.1:cloud", "helper": "gemma3:12b-cloud"},
    DIRECT: {"chat": "glm-5.2", "helper": "gemma4:31b"},
}


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
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file or PROJECT_ROOT / ".env")
        except ImportError:  # python-dotenv is optional
            pass

        token = os.getenv("DISCORD_TOKEN", "")
        if not token:
            raise SystemExit("DISCORD_TOKEN is not set (put it in .env or the environment).")

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
            if not key:
                raise SystemExit("--direct needs OLLAMA_API_KEY (create one at ollama.com/settings/keys).")
            kwargs["ollama_host"] = DIRECT_HOST
            kwargs["ollama_api_key"] = key
        elif v := os.getenv("OLLAMA_HOST"):
            kwargs["ollama_host"] = _normalize_host(v)
        if v := os.getenv("BONG_CHAT_MODEL"):
            kwargs["chat_model"] = v
        if v := os.getenv("BONG_HELPER_MODEL"):
            kwargs["helper_model"] = v
        if v := os.getenv("BONG_COOLDOWN_SECONDS"):
            kwargs["cooldown_seconds"] = float(v)
        return cls(**kwargs)
