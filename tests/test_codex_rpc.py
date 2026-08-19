import json
from pathlib import Path

from codex_context.codex_rpc import set_thread_name


def test_set_thread_name_uses_codex_native_rpc(tmp_path, monkeypatch):
    capture = tmp_path / "stdin.jsonl"
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "payload = sys.stdin.read()\n"
        "open(os.environ['CODEX_RPC_CAPTURE'], 'w', encoding='utf-8').write(payload)\n"
        "print(json.dumps({'id': 'codex-context-init', 'result': {}}))\n"
        "print(json.dumps({'id': 'codex-context-rename', 'result': {}}))\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    monkeypatch.setenv("CODEX_BIN", str(fake_codex))
    monkeypatch.setenv("CODEX_RPC_CAPTURE", str(capture))

    set_thread_name(tmp_path / ".codex", "thread-123", "  Мой   нормальный чат  ")

    messages = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
    rename = next(message for message in messages if message.get("id") == "codex-context-rename")
    assert rename["method"] == "thread/name/set"
    assert rename["params"] == {"threadId": "thread-123", "name": "Мой нормальный чат"}
