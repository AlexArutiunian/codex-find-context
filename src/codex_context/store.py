from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .parser import ParsedSession, chunk_text, parse_session


@dataclass(frozen=True)
class SyncStats:
    discovered: int
    reindexed: int
    unchanged: int
    failed: int


@dataclass(frozen=True)
class SessionRow:
    session_id: str
    path: str
    original_title: str
    custom_title: str | None
    cwd: str | None
    created_at: str | None
    updated_at: str | None
    message_count: int

    @property
    def title(self) -> str:
        return self.custom_title or self.original_title


@dataclass(frozen=True)
class SearchHit:
    session_id: str
    title: str
    role: str
    text: str
    score: float


class Store:
    def __init__(self, db_path: Path, codex_home: Path):
        self.db_path = db_path
        self.codex_home = codex_home
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    original_title TEXT NOT NULL,
                    custom_title TEXT,
                    cwd TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    message_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    timestamp TEXT,
                    embedding BLOB,
                    embedding_model TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_chunks_session ON chunks(session_id);
                CREATE INDEX IF NOT EXISTS idx_chunks_embedding_model ON chunks(embedding_model);
                CREATE INDEX IF NOT EXISTS idx_chunks_session_embedding_model
                    ON chunks(session_id, embedding_model);

                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    session_id UNINDEXED,
                    role UNINDEXED,
                    text,
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
            )

    def discover_session_paths(self) -> list[Path]:
        candidates: list[Path] = []
        for root in (self.codex_home / "sessions", self.codex_home / "archived_sessions"):
            if root.exists():
                candidates.extend(path for path in root.rglob("*.jsonl") if path.is_file())
        return sorted(set(candidates))

    def sync_sessions(self) -> SyncStats:
        paths = self.discover_session_paths()
        discovered = len(paths)
        reindexed = unchanged = failed = 0

        for path in paths:
            try:
                stat = path.stat()
                with self._connect() as conn:
                    existing = conn.execute(
                        "SELECT session_id, mtime_ns, size_bytes, path FROM sessions WHERE path = ?",
                        (str(path),),
                    ).fetchone()
                if (
                    existing
                    and int(existing["mtime_ns"]) == stat.st_mtime_ns
                    and int(existing["size_bytes"]) == stat.st_size
                ):
                    unchanged += 1
                    continue

                parsed = parse_session(path)
                self._upsert_parsed(parsed, stat.st_mtime_ns, stat.st_size)
                reindexed += 1
            except (OSError, sqlite3.Error, ValueError):
                failed += 1

        return SyncStats(discovered, reindexed, unchanged, failed)

    def _upsert_parsed(self, parsed: ParsedSession, mtime_ns: int, size_bytes: int) -> None:
        with self._connect() as conn:
            old = conn.execute(
                "SELECT custom_title FROM sessions WHERE session_id = ?", (parsed.session_id,)
            ).fetchone()
            custom_title = old["custom_title"] if old else None

            conn.execute(
                """
                INSERT INTO sessions(
                    session_id, path, mtime_ns, size_bytes, original_title, custom_title,
                    cwd, created_at, updated_at, message_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    path=excluded.path,
                    mtime_ns=excluded.mtime_ns,
                    size_bytes=excluded.size_bytes,
                    original_title=excluded.original_title,
                    cwd=excluded.cwd,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at,
                    message_count=excluded.message_count
                """,
                (
                    parsed.session_id,
                    str(parsed.path),
                    mtime_ns,
                    stat_size := size_bytes,
                    parsed.original_title,
                    custom_title,
                    parsed.cwd,
                    parsed.created_at,
                    parsed.updated_at,
                    len(parsed.messages),
                ),
            )

            old_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM chunks WHERE session_id = ?", (parsed.session_id,)
                )
            ]
            if old_ids:
                conn.execute("DELETE FROM chunks_fts WHERE session_id = ?", (parsed.session_id,))
                conn.execute("DELETE FROM chunks WHERE session_id = ?", (parsed.session_id,))

            ordinal = 0
            for message in parsed.messages:
                for chunk in chunk_text(message.text):
                    cursor = conn.execute(
                        """
                        INSERT INTO chunks(session_id, ordinal, role, text, timestamp)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            parsed.session_id,
                            ordinal,
                            message.role,
                            chunk,
                            message.timestamp,
                        ),
                    )
                    chunk_id = int(cursor.lastrowid)
                    conn.execute(
                        "INSERT INTO chunks_fts(chunk_id, session_id, role, text) VALUES (?, ?, ?, ?)",
                        (chunk_id, parsed.session_id, message.role, chunk),
                    )
                    ordinal += 1

    def list_sessions(self) -> list[SessionRow]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_id, path, original_title, custom_title, cwd,
                       created_at, updated_at, message_count
                FROM sessions
                ORDER BY COALESCE(updated_at, created_at) DESC, session_id DESC
                """
            ).fetchall()
        return [SessionRow(**dict(row)) for row in rows]

    def get_session(self, session_id: str) -> SessionRow | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT session_id, path, original_title, custom_title, cwd,
                       created_at, updated_at, message_count
                FROM sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return SessionRow(**dict(row)) if row else None

    def load_parsed_session(self, session_id: str) -> ParsedSession | None:
        row = self.get_session(session_id)
        if row is None:
            return None
        path = Path(row.path)
        if not path.exists():
            return None
        return parse_session(path)

    def rename_session(self, session_id: str, title: str) -> None:
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            raise ValueError("Название не может быть пустым")
        if len(title) > 160:
            raise ValueError("Название слишком длинное (максимум 160 символов)")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET custom_title = ? WHERE session_id = ?",
                (title, session_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(session_id)

    def clear_custom_title(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET custom_title = NULL WHERE session_id = ?",
                (session_id,),
            )

    def chunk_counts(
        self,
        embedding_model: str,
        session_id: str | None = None,
    ) -> tuple[int, int]:
        with self._connect() as conn:
            if session_id is None:
                total = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
                embedded = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM chunks WHERE embedding_model = ? AND embedding IS NOT NULL",
                        (embedding_model,),
                    ).fetchone()[0]
                )
            else:
                total = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM chunks WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()[0]
                )
                embedded = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) FROM chunks
                        WHERE session_id = ? AND embedding_model = ? AND embedding IS NOT NULL
                        """,
                        (session_id, embedding_model),
                    ).fetchone()[0]
                )
        return total, embedded

    def chunks_needing_embeddings(
        self,
        embedding_model: str,
        limit: int = 64,
    ) -> list[tuple[int, str]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, text FROM chunks
                WHERE embedding IS NULL OR embedding_model IS NULL OR embedding_model != ?
                ORDER BY id
                LIMIT ?
                """,
                (embedding_model, limit),
            ).fetchall()
        return [(int(row["id"]), str(row["text"])) for row in rows]

    def save_embeddings(
        self, embedding_model: str, rows: Iterable[tuple[int, bytes]]
    ) -> None:
        with self._connect() as conn:
            conn.executemany(
                "UPDATE chunks SET embedding = ?, embedding_model = ? WHERE id = ?",
                ((blob, embedding_model, chunk_id) for chunk_id, blob in rows),
            )

    def iter_embeddings(
        self,
        embedding_model: str,
        session_id: str | None = None,
    ):
        with self._connect() as conn:
            if session_id is None:
                cursor = conn.execute(
                    """
                    SELECT c.id, c.session_id, c.role, c.text, c.embedding,
                           COALESCE(s.custom_title, s.original_title) AS title
                    FROM chunks c
                    JOIN sessions s ON s.session_id = c.session_id
                    WHERE c.embedding_model = ? AND c.embedding IS NOT NULL
                    ORDER BY c.id
                    """,
                    (embedding_model,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT c.id, c.session_id, c.role, c.text, c.embedding,
                           COALESCE(s.custom_title, s.original_title) AS title
                    FROM chunks c
                    JOIN sessions s ON s.session_id = c.session_id
                    WHERE c.session_id = ?
                      AND c.embedding_model = ?
                      AND c.embedding IS NOT NULL
                    ORDER BY c.id
                    """,
                    (session_id, embedding_model),
                )
            for row in cursor:
                yield row

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = re.findall(r"[\w./:+#-]{2,}", query, flags=re.UNICODE)
        escaped = [token.replace('"', '""') for token in tokens[:20]]
        return " OR ".join(f'"{token}"' for token in escaped)

    def lexical_search(
        self,
        query: str,
        limit: int = 10,
        session_id: str | None = None,
    ) -> list[SearchHit]:
        fts_query = self._fts_query(query)
        if not fts_query:
            return []
        with self._connect() as conn:
            if session_id is None:
                rows = conn.execute(
                    """
                    SELECT c.session_id, c.role, c.text,
                           COALESCE(s.custom_title, s.original_title) AS title,
                           bm25(chunks_fts) AS rank
                    FROM chunks_fts
                    JOIN chunks c ON c.id = chunks_fts.chunk_id
                    JOIN sessions s ON s.session_id = c.session_id
                    WHERE chunks_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT c.session_id, c.role, c.text,
                           COALESCE(s.custom_title, s.original_title) AS title,
                           bm25(chunks_fts) AS rank
                    FROM chunks_fts
                    JOIN chunks c ON c.id = chunks_fts.chunk_id
                    JOIN sessions s ON s.session_id = c.session_id
                    WHERE chunks_fts MATCH ? AND c.session_id = ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, session_id, limit),
                ).fetchall()
        hits: list[SearchHit] = []
        for index, row in enumerate(rows):
            hits.append(
                SearchHit(
                    session_id=str(row["session_id"]),
                    title=str(row["title"]),
                    role=str(row["role"]),
                    text=str(row["text"]),
                    score=max(0.0, 1.0 - index * 0.04),
                )
            )
        return hits
