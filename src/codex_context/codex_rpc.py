from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import threading
from pathlib import Path
from typing import Iterable


class CodexRpcError(RuntimeError):
    pass


def _resolve_codex_bin() -> str:
    configured = os.getenv("CODEX_BIN", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)
        raise CodexRpcError(f"CODEX_BIN не найден: {path}")

    direct = shutil.which("codex")
    if direct:
        return direct

    # systemd --user may have a smaller PATH than an interactive terminal.
    try:
        result = subprocess.run(
            ["bash", "-lc", "command -v codex"],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
        candidate = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        if candidate and Path(candidate).is_file():
            return candidate
    except (OSError, subprocess.SubprocessError):
        pass

    for candidate in (
        Path("~/.local/bin/codex").expanduser(),
        Path("~/.npm-global/bin/codex").expanduser(),
        Path("/usr/local/bin/codex"),
        Path("/usr/bin/codex"),
    ):
        if candidate.is_file():
            return str(candidate)

    raise CodexRpcError(
        "Не найден бинарник codex. Если он установлен нестандартно, задай CODEX_BIN=/путь/к/codex."
    )


def _normalize_name(name: str) -> str:
    normalized = " ".join(name.split()).strip()
    if not normalized:
        raise CodexRpcError("Название не может быть пустым")
    return normalized


def set_thread_names(
    codex_home: Path,
    updates: Iterable[tuple[str, str]],
    timeout: float = 20.0,
) -> dict[str, str]:
    """Persist multiple names through one Codex app-server process.

    Returns a mapping of thread_id -> error only for failed updates. An empty
    mapping means every requested rename was accepted by Codex.
    """
    normalized_updates = [(thread_id, _normalize_name(name)) for thread_id, name in updates]
    if not normalized_updates:
        return {}

    codex_bin = _resolve_codex_bin()
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)

    init_id = "codex-context-init"
    messages: list[dict] = [
        {
            "id": init_id,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "codex-context",
                    "title": "Codex Context",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
        },
        {"method": "initialized"},
    ]
    request_to_thread: dict[str, str] = {}
    for index, (thread_id, name) in enumerate(normalized_updates):
        request_id = f"codex-context-rename-{index}"
        request_to_thread[request_id] = thread_id
        messages.append(
            {
                "id": request_id,
                "method": "thread/name/set",
                "params": {"threadId": thread_id, "name": name},
            }
        )

    payload = "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages)
    try:
        completed = subprocess.run(
            [codex_bin, "app-server", "--listen", "stdio://"],
            input=payload,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodexRpcError(f"Codex app-server не ответил за {timeout:g} с") from exc
    except OSError as exc:
        raise CodexRpcError(f"Не удалось запустить Codex app-server: {exc}") from exc

    responses: dict[str, dict] = {}
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        request_id = str(message.get("id", ""))
        if request_id in request_to_thread:
            responses[request_id] = message

    stderr_lines = completed.stderr.strip().splitlines()
    stderr_tail = stderr_lines[-1] if stderr_lines else f"exit code {completed.returncode}"
    failures: dict[str, str] = {}
    for request_id, thread_id in request_to_thread.items():
        response = responses.get(request_id)
        if response is None:
            failures[thread_id] = f"Codex не вернул ответ на thread/name/set: {stderr_tail}"
            continue
        error = response.get("error")
        if error:
            if isinstance(error, dict):
                detail = error.get("message") or json.dumps(error, ensure_ascii=False)
            else:
                detail = str(error)
            failures[thread_id] = f"Codex отклонил переименование: {detail}"
    return failures


def set_thread_name(codex_home: Path, thread_id: str, name: str, timeout: float = 12.0) -> None:
    """Persist one thread name through Codex's native `thread/name/set` RPC."""
    failures = set_thread_names(codex_home, [(thread_id, name)], timeout=timeout)
    if thread_id in failures:
        raise CodexRpcError(failures[thread_id])


def _migrate_saved_titles() -> None:
    """Best-effort migration for titles saved by older Codex Context versions."""
    data_dir = Path(
        os.getenv("CODEX_CONTEXT_DATA_DIR", "~/.local/share/codex-find-context")
    ).expanduser()
    db_path = data_dir / "index.sqlite3"
    if not db_path.is_file():
        return

    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            rows = conn.execute(
                """
                SELECT session_id, custom_title
                FROM sessions
                WHERE custom_title IS NOT NULL AND trim(custom_title) != ''
                ORDER BY session_id
                """
            ).fetchall()
    except sqlite3.Error as exc:
        print(f"[codex-context] saved-title migration could not read local DB: {exc}")
        return

    updates = [(str(session_id), str(title)) for session_id, title in rows]
    if not updates:
        return

    codex_home = Path(os.getenv("CODEX_HOME", "~/.codex")).expanduser()
    try:
        failures = set_thread_names(codex_home, updates)
    except CodexRpcError as exc:
        print(f"[codex-context] saved-title migration unavailable: {exc}")
        return

    if failures:
        print(
            "[codex-context] saved-title migration partial failure: "
            + "; ".join(f"{thread_id}: {error}" for thread_id, error in failures.items())
        )
    else:
        print(f"[codex-context] synced {len(updates)} saved title(s) into Codex metadata")


# Runs once per Codex Context process, in the background, so old custom titles
# survive the transition from local-only naming to Codex-native naming.
threading.Thread(target=_migrate_saved_titles, daemon=True).start()
