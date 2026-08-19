from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, value)


@dataclass(frozen=True)
class Settings:
    codex_home: Path
    data_dir: Path
    db_path: Path
    model_cache_dir: Path
    embedding_model: str
    embedding_threads: int
    index_batch_size: int
    index_pause_seconds: float
    status_refresh_seconds: float
    host: str
    port: int
    auto_index: bool

    @classmethod
    def from_env(cls) -> "Settings":
        codex_home = Path(os.getenv("CODEX_HOME", "~/.codex")).expanduser()
        data_dir = Path(
            os.getenv("CODEX_CONTEXT_DATA_DIR", "~/.local/share/codex-find-context")
        ).expanduser()
        return cls(
            codex_home=codex_home,
            data_dir=data_dir,
            db_path=data_dir / "index.sqlite3",
            model_cache_dir=data_dir / "models",
            embedding_model=os.getenv(
                "CODEX_CONTEXT_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
            ),
            # This is a background desktop service, not a benchmark. FastEmbed passes
            # this value to ONNX Runtime, preventing it from consuming every CPU core.
            embedding_threads=_env_int("CODEX_CONTEXT_EMBED_THREADS", 2),
            index_batch_size=_env_int("CODEX_CONTEXT_INDEX_BATCH", 32),
            index_pause_seconds=_env_float("CODEX_CONTEXT_INDEX_PAUSE", 0.08),
            status_refresh_seconds=_env_float("CODEX_CONTEXT_STATUS_REFRESH", 5.0, 1.0),
            host=os.getenv("CODEX_CONTEXT_HOST", "127.0.0.1"),
            port=int(os.getenv("CODEX_CONTEXT_PORT", "7860")),
            auto_index=_env_bool("CODEX_CONTEXT_AUTO_INDEX", True),
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.model_cache_dir.mkdir(parents=True, exist_ok=True)
