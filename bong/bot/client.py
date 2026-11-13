"""BongBot: builds every service once at startup and hands them to the cogs."""

from __future__ import annotations

import difflib
import logging

import discord
from discord.ext import commands

from ..channels import ActiveChannels
from ..config import Settings
from ..conversation.bong import Bong
from ..conversation.turn import Turn
from ..llm import OllamaClient
from ..memory import NullMemoryStore
from ..tools import Tool
from ..tools.time import current_time_tool
from ..users import UserStore
from .admin import AdminCog
from .chat import ChatCog

log = logging.getLogger(__name__)


class BongBot(commands.Bot):
    def __init__(self, settings: Settings):
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
        self.bong: Bong | None = None  # built in setup_hook, once memory is loaded

    async def setup_hook(self) -> None:
        # Runs once, before connecting — unlike on_ready, which repeats on reconnect.
        self.user_store.load()
        self.active_channels.load()
        await self._check_ollama()

        tools: list[Tool] = [current_time_tool(self.user_store.timezone)]
        memory = await self._start_memory()
        if memory is not None:
            from ..tools.memory import memory_tools
            tools += memory_tools(memory, self.resolve_name, self.user_store.is_admin)

        s = self.settings
        self.bong = Bong(
            self.llm,
            chat_model=s.chat_model,
            helper_model=s.helper_model,
            memory=memory or NullMemoryStore(),
            tools=tools,
            history_size=s.history_size,
            summarize_chunk=s.summarize_chunk,
            max_summaries=s.max_summaries,
            classifier_context=s.classifier_context,
        )
        log.info("Tools: %s", ", ".join(self.bong.tools.names()))
        await self.add_cog(ChatCog(self))
        await self.add_cog(AdminCog(self))

    async def _start_memory(self):
        if not self.settings.memory_enabled:
            log.info("Long-term memory is turned off (BONG_MEMORY=off)")
            return None
        try:
            from ..memory.chroma_store import ChromaMemoryStore
            from ..memory.embedder import PinnedMiniLM
            store = ChromaMemoryStore(self.settings.data_dir / "memory",
                                      PinnedMiniLM(self.settings.data_dir / "embedding_model"))
            await store.start()
            return store
        except Exception:
            log.exception("Long-term memory failed to start; running without it")
            return None

    def resolve_name(self, name: str, turn: Turn) -> tuple[int, str] | None:
        """Match a display name to a Discord user: this server's members (or every shared server in DMs)."""
        wanted = name.strip().lower()
        guild = self.get_guild(turn.guild_id) if turn.guild_id else None
        members = list(guild.members) if guild else list(self.get_all_members())
        by_name: dict[str, tuple[int, str]] = {}
        for m in members:
            if m.bot:
                continue
            for n in (m.display_name, m.global_name, m.name):
                if n:
                    by_name.setdefault(n.lower(), (m.id, m.display_name))
        for uid, entry in self.user_store.known_names().items():
            by_name.setdefault(entry.lower(), (uid, entry))
        if wanted in by_name:
            return by_name[wanted]
        close = difflib.get_close_matches(wanted, list(by_name), n=1, cutoff=0.85)
        return by_name[close[0]] if close else None

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
