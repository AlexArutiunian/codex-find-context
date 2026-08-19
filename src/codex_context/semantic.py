from __future__ import annotations

import heapq
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .store import SearchHit, Store


@dataclass(frozen=True)
class SearchResult:
    hits: list[SearchHit]
    mode: str
    detail: str | None = None


class SemanticSearch:
    def __init__(self, store: Store, model_name: str, cache_dir: Path):
        self.store = store
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None
        self._model_lock = threading.Lock()
        self._index_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    def _load_model(self):
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            from fastembed import TextEmbedding

            try:
                self._model = TextEmbedding(
                    model_name=self.model_name,
                    cache_dir=str(self.cache_dir),
                )
            except TypeError:
                self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def _prepare(self, text: str, kind: str) -> str:
        if "e5" in self.model_name.lower():
            return f"{kind}: {text}"
        return text

    @staticmethod
    def _normalized(vector) -> np.ndarray:
        array = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(array))
        if norm > 0:
            array = array / norm
        return array

    def _embed(self, model, texts: list[str], batch_size: int) -> list[np.ndarray]:
        with self._inference_lock:
            return list(model.embed(texts, batch_size=batch_size))

    def _save_batch_resilient(self, model, pending: list[tuple[int, str]]) -> None:
        """Embed one batch, isolating a bad text instead of killing the whole indexer."""
        texts = [self._prepare(text, "passage") for _, text in pending]
        batch_error: Exception | None = None
        try:
            vectors = self._embed(model, texts, batch_size=len(texts))
            if len(vectors) != len(pending):
                raise RuntimeError(
                    f"embedding model returned {len(vectors)} vectors for {len(pending)} texts"
                )
            serialized = [
                (chunk_id, self._normalized(vector).tobytes())
                for (chunk_id, _), vector in zip(pending, vectors, strict=True)
            ]
            self.store.save_embeddings(self.model_name, serialized)
            return
        except Exception as exc:
            batch_error = exc
            print(
                "[codex-context] embedding batch failed; retrying chunks one-by-one: "
                f"{exc}"
            )

        serialized: list[tuple[int, bytes]] = []
        failures: list[tuple[int, str]] = []
        successes = 0
        for chunk_id, text in pending:
            try:
                vector = self._embed(
                    model,
                    [self._prepare(text, "passage")],
                    batch_size=1,
                )[0]
                serialized.append((chunk_id, self._normalized(vector).tobytes()))
                successes += 1
            except Exception as exc:
                # Empty BLOB is an explicit local marker: this chunk was processed but
                # cannot be embedded. Search ignores it because its vector shape is 0.
                serialized.append((chunk_id, b""))
                failures.append((chunk_id, str(exc)))

        # If absolutely everything failed, this is probably a model/runtime problem,
        # not 64 independently malformed messages. Retry later instead of marking the
        # whole corpus as bad.
        if successes == 0:
            raise RuntimeError(
                "embedding runtime failed for the whole batch and for every single-item retry"
            ) from batch_error

        self.store.save_embeddings(self.model_name, serialized)
        for chunk_id, error in failures:
            print(f"[codex-context] skipped unembeddable chunk {chunk_id}: {error}")

    def ensure_embeddings(self) -> tuple[int, int]:
        """Build missing embeddings in bounded batches without blocking user search."""
        with self._index_lock:
            model = self._load_model()
            batch_size = 64

            while True:
                pending = self.store.chunks_needing_embeddings(
                    self.model_name,
                    limit=batch_size,
                )
                if not pending:
                    break

                self._save_batch_resilient(model, pending)

            return self.store.chunk_counts(self.model_name)

    def semantic_search(self, query: str, limit: int = 10) -> list[SearchHit]:
        """Search only embeddings that are already available.

        Missing embeddings continue to be produced by the background indexer. A
        user search never synchronously finishes the whole corpus first.
        """
        query = query.strip()
        if not query:
            return []

        model = self._load_model()
        query_vector = self._normalized(
            self._embed(model, [self._prepare(query, "query")], batch_size=1)[0]
        )

        heap: list[tuple[float, int, SearchHit]] = []
        serial = 0
        for row in self.store.iter_embeddings(self.model_name):
            vector = np.frombuffer(row["embedding"], dtype=np.float32)
            if vector.shape != query_vector.shape:
                continue
            score = float(np.dot(query_vector, vector))
            hit = SearchHit(
                session_id=str(row["session_id"]),
                title=str(row["title"]),
                role=str(row["role"]),
                text=str(row["text"]),
                score=score,
            )
            item = (score, serial, hit)
            serial += 1
            if len(heap) < limit:
                heapq.heappush(heap, item)
            elif score > heap[0][0]:
                heapq.heapreplace(heap, item)

        return [item[2] for item in sorted(heap, reverse=True)]

    def search(self, query: str, limit: int = 10) -> SearchResult:
        total, embedded = self.store.chunk_counts(self.model_name)
        if embedded == 0:
            hits = self.store.lexical_search(query, limit)
            return SearchResult(
                hits=hits,
                mode="lexical fallback",
                detail=f"semantic index ещё прогревается: {embedded}/{total} chunks",
            )

        try:
            hits = self.semantic_search(query, limit)
            detail = None
            if embedded < total:
                detail = f"поиск по уже готовым {embedded}/{total} semantic chunks"
            return SearchResult(hits=hits, mode="semantic", detail=detail)
        except Exception as exc:
            hits = self.store.lexical_search(query, limit)
            return SearchResult(hits=hits, mode="lexical fallback", detail=str(exc))
