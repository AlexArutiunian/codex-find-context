import json
from pathlib import Path

from codex_context.parser import parse_session


def write_jsonl(path: Path, records, partial=False):
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if partial:
            handle.write('{"timestamp":"2026-08-19T12:00:03Z","type":"response_item"')


def test_parse_rollout_and_ignore_injected_context(tmp_path):
    session_id = "01a00a34-3611-7941-8d68-bf3320648983"
    path = tmp_path / f"rollout-2026-08-16T13-53-09-{session_id}.jsonl"
    records = [
        {
            "timestamp": "2026-08-16T13:53:09Z",
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": "/home/al/project"},
        },
        {
            "timestamp": "2026-08-16T13:53:10Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "<environment_context>noise</environment_context>"}],
            },
        },
        {
            "timestamp": "2026-08-16T13:53:11Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Почини align depth RealSense"},
        },
        {
            "timestamp": "2026-08-16T13:53:12Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Перехожу на 640x480."}],
            },
        },
        {
            "timestamp": "2026-08-16T13:53:13Z",
            "type": "response_item",
            "payload": {"type": "function_call", "name": "shell", "arguments": '{"cmd":"pytest -q"}'},
        },
    ]
    write_jsonl(path, records, partial=True)

    parsed = parse_session(path)

    assert parsed.session_id == session_id
    assert parsed.cwd == "/home/al/project"
    assert parsed.original_title == "Почини align depth RealSense"
    texts = [message.text for message in parsed.messages]
    assert all("environment_context" not in text for text in texts)
    assert "Перехожу на 640x480." in texts
    assert any("pytest -q" in text for text in texts)


def test_session_id_falls_back_to_filename(tmp_path):
    session_id = "12345678-1234-1234-1234-123456789abc"
    path = tmp_path / f"rollout-2026-01-01T00-00-00-{session_id}.jsonl"
    write_jsonl(
        path,
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "hello"},
            }
        ],
    )
    assert parse_session(path).session_id == session_id
