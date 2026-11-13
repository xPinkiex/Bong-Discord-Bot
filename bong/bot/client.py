"""BongBot: builds every service once at startup and hands them to the cogs."""

from __future__ import annotations

import difflib
import logging
import time

import discord
from discord.ext import commands

from ..channels import ActiveChannels
from ..config import Settings
from ..conversation.bong import Bong
from ..conversation.prompt import VoiceStatusSection
from ..conversation.turn import IMAGE_EXTS, TEXT_EXTS, Turn
from ..llm import OllamaClient
from ..memory import NullMemoryStore
from ..music.library import MusicLibrary
from ..music.stats import SongStats
from ..reminders import ReminderStore
from ..tools import Tool
from ..tools.admin import shutdown_tool, stats_tool
from ..tools.files import file_tools
from ..tools.music import music_tools
from ..tools.reminders import reminder_tools
from ..tools.web import web_tools
from ..tools.people import set_alias_tool
from ..tools.time import current_time_tool, timezone_tools
from ..users import UserStore
from .admin import AdminCog
from .background import BackgroundCog
from .chat import ChatCog
from .voice import VoiceController

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
        self.started_at = time.time()
        self.reminders = ReminderStore(settings.user_data_dir / "reminders.json")
        self.images = MusicLibrary(settings.data_dir / "saved_images", IMAGE_EXTS)
        self.texts = MusicLibrary(settings.data_dir / "saved_texts", TEXT_EXTS)
        self.library = MusicLibrary(settings.data_dir / "saved_sounds")
        self.song_stats = SongStats(settings.data_dir / "song_stats.json")
        self.voice = VoiceController(self, self.library, self.song_stats)
        self.add_listener(self.voice.on_voice_state_update)
        self.bong: Bong | None = None  # built in setup_hook, once memory is loaded

    async def setup_hook(self) -> None:
        # Runs once, before connecting — unlike on_ready, which repeats on reconnect.
        self.user_store.load()
        self.active_channels.load()
        self.song_stats.load()
        self.reminders.load()
        await self._check_ollama()

        s = self.settings
        tools: list[Tool] = [
            current_time_tool(self.user_store.timezone),
            *timezone_tools(self.user_store.timezone, self.user_store.set_timezone),
            *reminder_tools(self.reminders, self.user_store.timezone),
            set_alias_tool(self.user_store, self.resolve_name),
            *web_tools(self.llm, s.helper_model),
            *file_tools(self.llm, s.helper_model, self.images, self.texts),
            *music_tools(self.voice, self.library, lambda uid: self.user_store.has(uid, "music")),
            stats_tool(self.collect_stats),
            shutdown_tool(self.user_store.is_admin),
        ]
        memory = await self._start_memory()
        self.memory = memory
        if memory is not None:
            from ..tools.memory import memory_tools
            tools += memory_tools(memory, self.resolve_name, self.user_store.is_admin)

        self.bong = Bong(
            self.llm,
            chat_model=s.chat_model,
            helper_model=s.helper_model,
            memory=memory or NullMemoryStore(),
            tools=tools,
            extra_sections=[VoiceStatusSection(lambda turn: self.voice.status_for_prompt(turn.guild_id))],
            history_size=s.history_size,
            summarize_chunk=s.summarize_chunk,
            max_summaries=s.max_summaries,
            classifier_context=s.classifier_context,
        )
        log.info("Tools: %s", ", ".join(self.bong.tools.names()))
        await self.add_cog(ChatCog(self))
        await self.add_cog(AdminCog(self))
        await self.add_cog(BackgroundCog(self))

    async def collect_stats(self) -> dict:
        memory = getattr(self, "memory", None)
        return {
            "started_at": self.started_at,
            "memories": await memory.count() if memory is not None else None,
            "known_users": self.user_store.count(),
            "reminders": self.reminders.count(),
            "songs": len(self.library.songs()),
            "total_plays": self.song_stats.total(),
            "top_songs": self.song_stats.top(3),
            "total_tokens": self.user_store.total_tokens(),
            "top_users": self.user_store.top_by_tokens(3),
        }

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

    def name_of(self, user: discord.abc.User) -> str:
        """What Bong calls someone: their alias if they have one, else their display name."""
        return self.user_store.alias(user.id) or user.display_name

    def resolve_name(self, name: str, turn: Turn) -> tuple[int, str] | None:
        """Match a name to a Discord user: aliases first, then this server's members
        (or every shared server in DMs). Returns (user_id, the name Bong uses for them)."""
        wanted = name.strip().lstrip("@").lower()
        by_name: dict[str, tuple[int, str]] = {}
        for uid, alias in self.user_store.aliases().items():
            by_name.setdefault(alias.lower(), (uid, alias))
        guild = self.get_guild(turn.guild_id) if turn.guild_id else None
        members = list(guild.members) if guild else list(self.get_all_members())
        for m in members:
            if m.bot:
                continue
            for n in (m.display_name, m.global_name, m.name):
                if n:
                    by_name.setdefault(n.lower(), (m.id, self.name_of(m)))
        for uid, entry in self.user_store.known_names().items():
            by_name.setdefault(entry.lower(), (uid, self.user_store.alias(uid) or entry))
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
        try:
            await self.voice.shutdown()
        except Exception:
            log.exception("Error leaving voice channels")
        await self.llm.close()
        await super().close()
