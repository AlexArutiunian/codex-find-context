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


@dataclass(frozen=True)
class Settings:
    codex_home: Path
    data_dir: Path
    db_path: Path
    model_cache_dir: Path
    embedding_model: str
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
            host=os.getenv("CODEX_CONTEXT_HOST", "127.0.0.1"),
            port=int(os.getenv("CODEX_CONTEXT_PORT", "7860")),
            auto_index=_env_bool("CODEX_CONTEXT_AUTO_INDEX", True),
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.model_cache_dir.mkdir(parents=True, exist_ok=True)
