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

    def ensure_embeddings(self) -> tuple[int, int]:
        """Build missing embeddings in small batches without blocking search for the whole run."""
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

                texts = [self._prepare(text, "passage") for _, text in pending]
                # Hold the inference lock only for one small batch. Search requests can
                # run between batches instead of waiting for the entire corpus index.
                with self._inference_lock:
                    vectors = list(model.embed(texts, batch_size=batch_size))

                serialized: list[tuple[int, bytes]] = []
                for (chunk_id, _), vector in zip(pending, vectors, strict=True):
                    normalized = self._normalized(vector)
                    serialized.append((chunk_id, normalized.tobytes()))
                self.store.save_embeddings(self.model_name, serialized)

            return self.store.chunk_counts(self.model_name)

    def semantic_search(self, query: str, limit: int = 10) -> list[SearchHit]:
        """Search only embeddings that are already available.

        Missing embeddings continue to be produced by the background indexer. A
        user search must never synchronously finish the whole corpus first.
        """
        query = query.strip()
        if not query:
            return []

        model = self._load_model()
        with self._inference_lock:
            query_vector = self._normalized(
                next(iter(model.embed([self._prepare(query, "query")], batch_size=1)))
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
