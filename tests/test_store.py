import json
import os
from pathlib import Path

from codex_context.store import Store


def make_session(path: Path, session_id: str, user_text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "timestamp": "2026-08-19T10:00:00Z",
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": "/tmp/project"},
        },
        {
            "timestamp": "2026-08-19T10:00:01Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": user_text},
        },
        {
            "timestamp": "2026-08-19T10:00:02Z",
            "type": "event_msg",
            "payload": {"type": "agent_message", "message": "Готово"},
        },
    ]
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_sync_rename_and_incremental_index(tmp_path):
    codex_home = tmp_path / ".codex"
    session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    rollout = codex_home / "sessions" / "2026" / "08" / "19" / f"rollout-x-{session_id}.jsonl"
    make_session(rollout, session_id, "Ищу старый TensorRT чат")

    store = Store(tmp_path / "data" / "index.sqlite3", codex_home)
    first = store.sync_sessions()
    assert first.discovered == 1
    assert first.reindexed == 1

    store.rename_session(session_id, "TensorRT benchmark")
    assert store.get_session(session_id).title == "TensorRT benchmark"

    second = store.sync_sessions()
    assert second.unchanged == 1
    assert store.get_session(session_id).title == "TensorRT benchmark"

    hits = store.lexical_search("TensorRT", 5)
    assert hits
    assert hits[0].session_id == session_id

    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp": "2026-08-19T10:00:03Z",
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "Добавил RealSense"},
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    os.utime(rollout, None)
    third = store.sync_sessions()
    assert third.reindexed == 1
    assert store.get_session(session_id).title == "TensorRT benchmark"
    assert store.lexical_search("RealSense", 5)
