#!/usr/bin/env python3
"""Pre-run setup script — downloads the whisper model if not already cached."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BONG_DATA = PROJECT_ROOT / "bong_data"

MODEL_DIR = BONG_DATA / "whisper_models"
MODEL_SIZE = "small"

def main():
    if (MODEL_DIR / f"models--Systran--faster-whisper-{MODEL_SIZE}").exists():
        print(f"Whisper {MODEL_SIZE} model already cached, skipping download.")
        return

    print(f"Downloading whisper {MODEL_SIZE} model (first run only)...")
    print("This caches locally in whisper_models/.")
    try:
        from faster_whisper import WhisperModel
        WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8", download_root=str(MODEL_DIR))
        print("Done! Model cached in whisper_models/")
    except Exception as e:
        print(f"Error downloading model: {e}")
        print("If you're behind a firewall/Pi-hole, whitelist: cdn-lfs.huggingface.co")
        print("Or set HF_ENDPOINT=https://hf-mirror.com and try again.")
        sys.exit(1)

if __name__ == "__main__":
    main()