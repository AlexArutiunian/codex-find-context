import json
from pathlib import Path

import numpy as np

from codex_context.semantic import SemanticSearch
from codex_context.store import Store


class FakeEmbeddingModel:
    def embed(self, texts):
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
        {"timestamp": "2026-08-19T10:00:00Z", "type": "session_meta", "payload": {"id": session_id}},
        {"timestamp": "2026-08-19T10:00:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": text}},
    ]
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_semantic_search_ranks_relevant_session(tmp_path):
    codex_home = tmp_path / ".codex"
    write_session(
        codex_home / "sessions/2026/08/19/rollout-a-11111111-1111-1111-1111-111111111111.jsonl",
        "11111111-1111-1111-1111-111111111111",
        "Чинил RealSense depth и align камеры",
    )
    write_session(
        codex_home / "sessions/2026/08/19/rollout-b-22222222-2222-2222-2222-222222222222.jsonl",
        "22222222-2222-2222-2222-222222222222",
        "Создавал git branch и worktree",
    )
    store = Store(tmp_path / "data/index.sqlite3", codex_home)
    store.sync_sessions()
    search = FakeSemanticSearch(store, "fake-model", tmp_path / "models")

    hits = search.semantic_search("где была камера depth", limit=2)

    assert hits[0].session_id == "11111111-1111-1111-1111-111111111111"
    assert hits[0].score > hits[1].score
