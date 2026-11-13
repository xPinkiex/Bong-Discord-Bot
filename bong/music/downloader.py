"""Download a song's audio from YouTube as mp3 (yt-dlp + ffmpeg). Blocking — run in a thread."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .library import MusicLibrary, clean_title

log = logging.getLogger(__name__)

MAX_DURATION = 20 * 60  # seconds; refuse hour-long mixes and streams


class DownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class Downloaded:
    path: Path
    title: str
    already_had: bool = False


def is_youtube_url(text: str) -> bool:
    host = urlparse(text.strip()).netloc.lower()
    return host.endswith("youtube.com") or host.endswith("youtu.be")


def strip_playlist(url: str) -> str:
    """Keep just the video from a 'watch?v=...&list=...' link."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    qs.pop("list", None)
    qs.pop("index", None)
    return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))


def download(query: str, library: MusicLibrary) -> Downloaded:
    """Download a YouTube URL, or the top YouTube search result for a song name."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError as YtError

    target = strip_playlist(query.strip()) if is_youtube_url(query) else f"ytsearch1:{query} official audio"
    base = {"quiet": True, "no_warnings": True, "noplaylist": True}
    try:
        with YoutubeDL(base) as ydl:
            info = ydl.extract_info(target, download=False)
        if info and info.get("entries") is not None:
            entries = [e for e in info["entries"] if e]
            if not entries:
                raise DownloadError(f"No YouTube results for {query!r}.")
            info = entries[0]
        title = str(info.get("title") or "untitled")
        if info.get("is_live"):
            raise DownloadError(f"'{title}' is a live stream, not a song.")
        if (info.get("duration") or 0) > MAX_DURATION:
            raise DownloadError(f"'{title}' is over {MAX_DURATION // 60} minutes long.")

        path = library.path_for(title)
        if path.exists():
            return Downloaded(path, path.stem, already_had=True)
        library.folder.mkdir(parents=True, exist_ok=True)
        options = base | {
            "format": "bestaudio/best",
            "outtmpl": str(path.with_suffix(".%(ext)s")),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        }
        with YoutubeDL(options) as ydl:
            ydl.download([info.get("webpage_url") or info.get("url") or target])
    except YtError as e:
        raise DownloadError(str(e).removeprefix("ERROR: ")) from e
    if not path.exists():
        raise DownloadError(f"Downloaded '{title}' but the mp3 wasn't created (is ffmpeg installed?).")
    log.info("Downloaded %s", path.name)
    return Downloaded(path, clean_title(title))
