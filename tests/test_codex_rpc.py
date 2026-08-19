import json

from codex_context.codex_rpc import set_thread_name, set_thread_names


def make_fake_codex(tmp_path):
    capture = tmp_path / "stdin.jsonl"
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "capture = os.environ['CODEX_RPC_CAPTURE']\n"
        "initialized = False\n"
        "for raw in sys.stdin:\n"
        "    with open(capture, 'a', encoding='utf-8') as fh:\n"
        "        fh.write(raw)\n"
        "        fh.flush()\n"
        "    message = json.loads(raw)\n"
        "    method = message.get('method')\n"
        "    request_id = message.get('id')\n"
        "    if method == 'initialize' and request_id:\n"
        "        print(json.dumps({'id': request_id, 'result': {}}), flush=True)\n"
        "    elif method == 'initialized':\n"
        "        initialized = True\n"
        "    elif method == 'thread/name/set' and request_id:\n"
        "        if initialized:\n"
        "            print(json.dumps({'id': request_id, 'result': {}}), flush=True)\n"
        "        else:\n"
        "            print(json.dumps({'id': request_id, 'error': {'message': 'not initialized'}}), flush=True)\n",
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
    assert messages[0]["method"] == "initialize"
    assert messages[1]["method"] == "initialized"
    rename = next(
        message
        for message in messages
        if str(message.get("id", "")).startswith("codex-context-rename-")
    )
    assert rename["method"] == "thread/name/set"
    assert rename["params"] == {"threadId": "thread-123", "name": "Мой нормальный чат"}


def test_set_thread_names_keeps_one_live_app_server_session(tmp_path, monkeypatch):
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
    assert sum(message.get("method") == "initialize" for message in messages) == 1
