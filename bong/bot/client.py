"""BongBot: builds every service once at startup and hands them to the cogs."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from ..channels import ActiveChannels
from ..config import Settings
from ..conversation.bong import Bong
from ..llm import OllamaClient
from ..memory import MemoryStore, NullMemoryStore
from ..users import UserStore
from .admin import AdminCog
from .chat import ChatCog

log = logging.getLogger(__name__)


class BongBot(commands.Bot):
    def __init__(self, settings: Settings, memory: MemoryStore | None = None):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix=settings.command_prefix,
            case_insensitive=True,
            intents=intents,
            # Bong can ping people, but never @everyone/@here or roles.
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False),
        )
        self.settings = settings
        self.user_store = UserStore(settings.user_data_dir / "users.json", settings.owner_id)
        self.active_channels = ActiveChannels(settings.data_dir / "active_channels.json")
        self.llm = OllamaClient(settings.ollama_host, api_key=settings.ollama_api_key)
        self.bong = Bong(
            self.llm,
            chat_model=settings.chat_model,
            helper_model=settings.helper_model,
            memory=memory or NullMemoryStore(),
            history_size=settings.history_size,
            summarize_chunk=settings.summarize_chunk,
            max_summaries=settings.max_summaries,
            classifier_context=settings.classifier_context,
        )

    async def setup_hook(self) -> None:
        # Runs once, before connecting — unlike on_ready, which repeats on reconnect.
        self.user_store.load()
        self.active_channels.load()
        await self._check_ollama()
        await self.add_cog(ChatCog(self))
        await self.add_cog(AdminCog(self))

    async def _check_ollama(self) -> None:
        s = self.settings
        log.info("Ollama mode: %s (%s) — chat model %s, helper model %s",
                 s.ollama_mode, s.ollama_host, s.chat_model, s.helper_model)
        models = await self.llm.list_models()
        if models is None:
            hint = "start it with `ollama serve`" if s.ollama_mode == "local" else "check your connection"
            log.warning("Ollama is not reachable at %s — Bong will error until it is (%s).", s.ollama_host, hint)
            return
        # Local servers list cloud models only once pulled, so a miss there is just a hint.
        missing = [m for m in (s.chat_model, s.helper_model) if m not in models]
        if missing:
            log.warning("Model(s) %s not listed at %s. Available: %s",
                        ", ".join(missing), s.ollama_host, ", ".join(sorted(models)) or "none")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s), active in %d channel(s)",
                 self.user, self.user.id if self.user else "?", len(list(self.active_channels)))

    async def close(self) -> None:
        await self.llm.close()
        await super().close()
