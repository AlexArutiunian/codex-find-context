import json
from pathlib import Path

import numpy as np

from codex_context.semantic import SemanticSearch
from codex_context.store import Store


CAMERA_ID = "11111111-1111-1111-1111-111111111111"
GIT_ID = "22222222-2222-2222-2222-222222222222"


class FakeEmbeddingModel:
    def embed(self, texts, batch_size=None):
        for text in texts:
            lowered = text.lower()
            if "realsense" in lowered or "depth" in lowered or "камера" in lowered:
                yield np.array([1.0, 0.0, 0.0], dtype=np.float32)
            elif "git" in lowered or "branch" in lowered:
                yield np.array([0.0, 1.0, 0.0], dtype=np.float32)
            else:
                yield np.array([0.0, 0.0, 1.0], dtype=np.float32)


class FakeSemanticSearch(SemanticSearch):
    def _load_model(self):
        if self._model is None:
            self._model = FakeEmbeddingModel()
        return self._model


def write_session(path: Path, session_id: str, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "timestamp": "2026-08-19T10:00:00Z",
            "type": "session_meta",
            "payload": {"id": session_id},
        },
        {
            "timestamp": "2026-08-19T10:00:01Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": text},
        },
    ]
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_store(tmp_path):
    codex_home = tmp_path / ".codex"
    write_session(
        codex_home / "sessions/2026/08/19/rollout-camera.jsonl",
        CAMERA_ID,
        "Чинил RealSense depth align камеры и потом проверял глубину",
    )
    write_session(
        codex_home / "sessions/2026/08/19/rollout-git.jsonl",
        GIT_ID,
        "Создавал git branch и worktree для локальной ветки",
    )
    store = Store(tmp_path / "data/index.sqlite3", codex_home)
    store.sync_sessions()
    return store


def test_semantic_search_can_be_restricted_to_one_chat(tmp_path):
    store = build_store(tmp_path)
    search = FakeSemanticSearch(store, "fake-model", tmp_path / "models")
    search.ensure_embeddings()

    global_hits = search.semantic_search("камера depth", limit=10)
    scoped_hits = search.semantic_search("камера depth", limit=10, session_id=GIT_ID)

    assert global_hits[0].session_id == CAMERA_ID
    assert scoped_hits
    assert {hit.session_id for hit in scoped_hits} == {GIT_ID}


def test_scoped_search_counts_only_selected_chat(tmp_path):
    store = build_store(tmp_path)
    search = FakeSemanticSearch(store, "fake-model", tmp_path / "models")
    search.ensure_embeddings()

    total_all, embedded_all = store.chunk_counts("fake-model")
    total_chat, embedded_chat = store.chunk_counts("fake-model", session_id=GIT_ID)

    assert total_chat < total_all
    assert embedded_chat == total_chat
    assert embedded_all == total_all


def test_lexical_fallback_is_also_restricted_to_one_chat(tmp_path):
    store = build_store(tmp_path)

    assert store.lexical_search("RealSense", limit=10, session_id=GIT_ID) == []
    hits = store.lexical_search("git branch", limit=10, session_id=GIT_ID)

    assert hits
    assert {hit.session_id for hit in hits} == {GIT_ID}
