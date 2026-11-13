"""The embedding model, pinned so memory can't silently break.

Bong computes embeddings herself and gives Chroma only the vectors, so a
Chroma upgrade can't swap the model out from under her. The model is
all-MiniLM-L6-v2 (ONNX, runs on the CPU, ~90 MB), kept in
``bong_data/embedding_model/``. Its files are checked against the hashes below
on every start; if they don't match, Bong refuses to use them rather than
producing incompatible vectors.

Should the model ever be changed on purpose, ``ChromaMemoryStore`` notices
(see ``fingerprint``) and re-embeds every memory from its saved text.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"
DIMENSIONS = 384

# SHA-256 of the files that determine the vectors.
PINNED_FILES = {
    "model.onnx": "4f148ba8ae9c2c7fbee4af2b132db8d06c6a6545b47fc83bbb98c3d22b8393e6",
    "tokenizer.json": "da0e79933b9ed51798a3ae27893d3c5fa4a201126cef75586296df9b4d2c62a0",
    "vocab.txt": "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
}
# SHA-256 of the archive Chroma downloads (Chroma checks this itself).
PINNED_ARCHIVE_SHA256 = "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"

# Embedded at startup and compared with the stored result to detect a model change.
PROBE_TEXT = "Bong is a green protogen with rainbow accents who loves her friends."


class EmbeddingModelError(RuntimeError):
    pass


class Embedder(Protocol):
    name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Blocking — call from a worker thread."""
        ...


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class PinnedMiniLM:
    """all-MiniLM-L6-v2 via Chroma's ONNX runner, with files stored and verified locally."""

    name = MODEL_NAME
    dimensions = DIMENSIONS

    def __init__(self, model_dir: Path):
        self.model_dir = Path(model_dir) / MODEL_NAME
        self._ef = None

    def load(self) -> None:
        """Download (first run only), verify and load the model. Blocking."""
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

        ef = ONNXMiniLM_L6_V2(preferred_providers=["CPUExecutionProvider"])
        ef.DOWNLOAD_PATH = self.model_dir
        ef._MODEL_SHA256 = PINNED_ARCHIVE_SHA256
        files_dir = self.model_dir / ef.EXTRACTED_FOLDER_NAME
        if not files_dir.exists():
            log.info("Downloading embedding model %s to %s (one time, ~80 MB)", MODEL_NAME, self.model_dir)
            ef._download_model_if_not_exists()
            (self.model_dir / ef.ARCHIVE_FILENAME).unlink(missing_ok=True)

        for filename, expected in PINNED_FILES.items():
            path = files_dir / filename
            if not path.exists() or _sha256(path) != expected:
                raise EmbeddingModelError(
                    f"Embedding model file {path} is missing or not the pinned version. "
                    f"Delete {self.model_dir} to download it again.")
        self._ef = ef
        self.embed([PROBE_TEXT])  # warm up
        log.info("Embedding model %s ready", MODEL_NAME)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._ef is None:
            raise EmbeddingModelError("Embedding model not loaded")
        return [list(map(float, v)) for v in self._ef(texts)]
