from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
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


class _CodexStdioClient:
    """Tiny synchronous JSON-RPC client for one explicit Codex metadata action.

    Important invariant for Codex Context: this client is never started by
    indexing, searching, refreshing, or application startup. It is created only
    from an explicit user rename/reset action, then terminated immediately after
    the RPC response. Normal dashboard operation is read-only with respect to
    CODEX_HOME.
    """

    def __init__(self, codex_bin: str, codex_home: Path, timeout: float):
        self.codex_bin = codex_bin
        self.codex_home = codex_home
        self.timeout = max(1.0, float(timeout))
        self.proc: subprocess.Popen[str] | None = None
        self.messages: queue.Queue[dict | BaseException | None] = queue.Queue()
        self.stderr_tail: deque[str] = deque(maxlen=80)
        self._write_lock = threading.Lock()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def __enter__(self) -> "_CodexStdioClient":
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.codex_home)
        try:
            self.proc = subprocess.Popen(
                [self.codex_bin, "app-server", "--listen", "stdio://"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
            )
        except OSError as exc:
            raise CodexRpcError(f"Не удалось запустить Codex app-server: {exc}") from exc

        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)

    def _read_stdout(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            self.messages.put(None)
            return
        try:
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self.messages.put(
                        CodexRpcError(f"Некорректный JSON от Codex app-server: {line[:300]!r}")
                    )
                    continue
                if isinstance(message, dict):
                    self.messages.put(message)
        except BaseException as exc:  # reader thread must wake the waiting request
            self.messages.put(exc)
        finally:
            self.messages.put(None)

    def _read_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            self.stderr_tail.append(line.rstrip("\n"))

    def _stderr_detail(self) -> str:
        detail = "\n".join(self.stderr_tail).strip()
        proc = self.proc
        if detail:
            return detail[-2000:]
        if proc is not None and proc.poll() is not None:
            return f"exit code {proc.returncode}"
        return "stdout закрыт без сообщения об ошибке"

    def send(self, payload: dict) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise CodexRpcError("Codex app-server не запущен")
        with self._write_lock:
            try:
                proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise CodexRpcError(
                    f"Codex app-server закрыл stdin: {self._stderr_detail()}"
                ) from exc

    def notify(self, method: str, params: dict | None = None) -> None:
        payload: dict = {"method": method}
        if params is not None:
            payload["params"] = params
        self.send(payload)

    def request(self, request_id: str, method: str, params: dict | None = None) -> dict:
        payload: dict = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.send(payload)

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexRpcError(
                    f"Codex app-server не ответил на {method} за {self.timeout:g} с. "
                    f"{self._stderr_detail()}"
                )
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise CodexRpcError(
                    f"Codex app-server не ответил на {method} за {self.timeout:g} с. "
                    f"{self._stderr_detail()}"
                ) from exc

            if message is None:
                raise CodexRpcError(
                    f"Codex app-server закрыл stdout до ответа на {method}: {self._stderr_detail()}"
                )
            if isinstance(message, BaseException):
                if isinstance(message, CodexRpcError):
                    raise message
                raise CodexRpcError(f"Ошибка чтения Codex app-server: {message}") from message

            # Renaming should not require approvals, but acknowledge unexpected
            # server requests so the short-lived RPC cannot deadlock.
            if "method" in message and "id" in message:
                self.send({"id": message["id"], "result": {}})
                continue

            if str(message.get("id", "")) != request_id:
                continue
            return message


def _response_error(response: dict) -> str | None:
    error = response.get("error")
    if not error:
        return None
    if isinstance(error, dict):
        detail = error.get("message") or json.dumps(error, ensure_ascii=False)
    else:
        detail = str(error)
    return str(detail)


def set_thread_names(
    codex_home: Path,
    updates: Iterable[tuple[str, str]],
    timeout: float = 20.0,
) -> dict[str, str]:
    """Persist names through a short-lived Codex app-server.

    This function must only be called in direct response to an explicit user
    rename/reset action. Search/indexing code must never call it.
    """
    normalized_updates = [(thread_id, _normalize_name(name)) for thread_id, name in updates]
    if not normalized_updates:
        return {}

    codex_bin = _resolve_codex_bin()
    failures: dict[str, str] = {}

    with _CodexStdioClient(codex_bin, codex_home, timeout) as client:
        init_response = client.request(
            "codex-context-init",
            "initialize",
            {
                "clientInfo": {
                    "name": "codex-context",
                    "title": "Codex Context",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        init_error = _response_error(init_response)
        if init_error:
            raise CodexRpcError(f"Codex отклонил initialize: {init_error}")
        client.notify("initialized")

        for index, (thread_id, name) in enumerate(normalized_updates):
            request_id = f"codex-context-rename-{index}"
            try:
                response = client.request(
                    request_id,
                    "thread/name/set",
                    {"threadId": thread_id, "name": name},
                )
            except CodexRpcError as exc:
                failures[thread_id] = str(exc)
                for remaining_thread_id, _ in normalized_updates[index + 1 :]:
                    failures.setdefault(remaining_thread_id, str(exc))
                break

            error = _response_error(response)
            if error:
                failures[thread_id] = f"Codex отклонил переименование: {error}"

    return failures


def set_thread_name(codex_home: Path, thread_id: str, name: str, timeout: float = 12.0) -> None:
    """Persist one native thread name after an explicit user action."""
    failures = set_thread_names(codex_home, [(thread_id, name)], timeout=timeout)
    if thread_id in failures:
        raise CodexRpcError(failures[thread_id])
