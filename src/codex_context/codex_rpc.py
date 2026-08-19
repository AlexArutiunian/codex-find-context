from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


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
    # Ask the user's login shell as a fallback so npm/nvm/local installs still work.
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


def set_thread_name(codex_home: Path, thread_id: str, name: str, timeout: float = 12.0) -> None:
    """Persist a thread name through Codex's own local app-server API.

    Codex exposes the stable v2 method `thread/name/set`. We intentionally talk
    to the installed Codex binary instead of editing its SQLite/state files
    directly, so this follows whatever persistence format the installed version
    currently uses.
    """
    name = " ".join(name.split()).strip()
    if not name:
        raise CodexRpcError("Название не может быть пустым")

    codex_bin = _resolve_codex_bin()
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)

    init_id = "codex-context-init"
    rename_id = "codex-context-rename"
    messages = [
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
        {
            "id": rename_id,
            "method": "thread/name/set",
            "params": {"threadId": thread_id, "name": name},
        },
    ]
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

    rename_response = None
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and str(message.get("id")) == rename_id:
            rename_response = message
            break

    if rename_response is None:
        detail = completed.stderr.strip().splitlines()
        tail = detail[-1] if detail else f"exit code {completed.returncode}"
        raise CodexRpcError(f"Codex не вернул ответ на thread/name/set: {tail}")

    error = rename_response.get("error")
    if error:
        if isinstance(error, dict):
            message = error.get("message") or json.dumps(error, ensure_ascii=False)
        else:
            message = str(error)
        raise CodexRpcError(f"Codex отклонил переименование: {message}")
