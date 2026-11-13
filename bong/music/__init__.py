"""Music: what plays next, the song library, downloading, and play counts.

    player.py      Player — queue / loop / shuffle state machine for one server (no Discord)
    library.py     MusicLibrary — the mp3 files in bong_data/saved_sounds, with name matching
    downloader.py  Download audio from YouTube (yt-dlp + ffmpeg)
    stats.py       SongStats — play counts (bong_data/song_stats.json, same format as before)

The Discord side (voice connections, ffmpeg playback) is bong/bot/voice.py,
and Bong's music tools are bong/tools/music.py.
"""
