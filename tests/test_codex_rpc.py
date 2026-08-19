import json

from codex_context.codex_rpc import set_thread_name, set_thread_names


def make_fake_codex(tmp_path):
    capture = tmp_path / "stdin.jsonl"
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "payload = sys.stdin.read()\n"
        "open(os.environ['CODEX_RPC_CAPTURE'], 'w', encoding='utf-8').write(payload)\n"
        "for line in payload.splitlines():\n"
        "    message = json.loads(line)\n"
        "    request_id = message.get('id')\n"
        "    if request_id:\n"
        "        print(json.dumps({'id': request_id, 'result': {}}))\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    return fake_codex, capture


def test_set_thread_name_uses_codex_native_rpc(tmp_path, monkeypatch):
    fake_codex, capture = make_fake_codex(tmp_path)
    monkeypatch.setenv("CODEX_BIN", str(fake_codex))
    monkeypatch.setenv("CODEX_RPC_CAPTURE", str(capture))

    set_thread_name(tmp_path / ".codex", "thread-123", "  Мой   нормальный чат  ")

    messages = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
    rename = next(
        message
        for message in messages
        if str(message.get("id", "")).startswith("codex-context-rename-")
    )
    assert rename["method"] == "thread/name/set"
    assert rename["params"] == {"threadId": "thread-123", "name": "Мой нормальный чат"}


def test_set_thread_names_batches_existing_custom_titles(tmp_path, monkeypatch):
    fake_codex, capture = make_fake_codex(tmp_path)
    monkeypatch.setenv("CODEX_BIN", str(fake_codex))
    monkeypatch.setenv("CODEX_RPC_CAPTURE", str(capture))

    failures = set_thread_names(
        tmp_path / ".codex",
        [("thread-a", "Первый чат"), ("thread-b", "Второй чат")],
    )

    assert failures == {}
    messages = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
    renames = [message for message in messages if message.get("method") == "thread/name/set"]
    assert [message["params"] for message in renames] == [
        {"threadId": "thread-a", "name": "Первый чат"},
        {"threadId": "thread-b", "name": "Второй чат"},
    ]
