"""Run Bong:  python -m bong [-d]"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from .config import Settings


def setup_logging(settings: Settings, debug: bool) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    logfile = logging.FileHandler(settings.log_dir / f"{datetime.now():%Y%m%d_%H.%M.%S}-bong.log", encoding="utf-8")
    logfile.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(console)
    root.addHandler(logfile)
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("bong").setLevel(logging.DEBUG if debug else logging.INFO)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="bong", description="Bong Discord bot")
    parser.add_argument("-d", "--debug", action="store_true", help="enable debug logging")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    setup_logging(settings, args.debug)

    from .bot.client import BongBot
    BongBot(settings).run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
