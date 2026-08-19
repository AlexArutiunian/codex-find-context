from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
CONTEXT_PREFIXES = (
    "<environment_context>",
    "<user_instructions>",
    "<permissions instructions>",
    "# AGENTS.md instructions",
)


@dataclass(frozen=True)
class Message:
    role: str
    text: str
    timestamp: str | None = None


@dataclass(frozen=True)
class ParsedSession:
    session_id: str
    path: Path
    cwd: str | None
    created_at: str | None
    updated_at: str | None
    original_title: str
    messages: tuple[Message, ...]


def _stringify(value: Any, max_chars: int = 6000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except TypeError:
            text = str(value)
    return text if len(text) <= max_chars else text[:max_chars] + "\n…[truncated]"


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = _content_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    if not isinstance(content, dict):
        return ""

    for key in ("text", "input_text", "output_text", "message"):
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    nested = content.get("content")
    if nested is not None:
        return _content_text(nested)
    return ""


def _is_injected_context(text: str) -> bool:
    lowered = text.lstrip().lower()
    return any(lowered.startswith(prefix.lower()) for prefix in CONTEXT_PREFIXES)


def _clean_title(text: str, max_chars: int = 78) -> str:
    one_line = re.sub(r"\s+", " ", text).strip()
    one_line = re.sub(r"^#+\s*", "", one_line)
    if len(one_line) <= max_chars:
        return one_line or "Без названия"
    return one_line[: max_chars - 1].rstrip() + "…"


def _timestamp_from_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def session_id_from_path(path: Path) -> str:
    matches = UUID_RE.findall(path.name)
    if matches:
        return matches[-1].lower()
    return path.stem


def _tool_message(payload: dict[str, Any]) -> Message | None:
    item_type = str(payload.get("type", ""))
    name = str(payload.get("name") or payload.get("tool_name") or item_type or "tool")

    if item_type in {"function_call", "custom_tool_call"}:
        body = payload.get("arguments", payload.get("input", ""))
        text = _stringify(body)
        return Message("tool", f"[{name}]\n{text}".strip()) if text else None

    if item_type in {"function_call_output", "custom_tool_call_output"}:
        text = _stringify(payload.get("output", payload.get("content", "")), max_chars=5000)
        return Message("tool", f"[tool output]\n{text}".strip()) if text else None

    if item_type in {"local_shell_call", "shell_call"}:
        body = payload.get("action", payload.get("command", payload))
        text = _stringify(body, max_chars=5000)
        return Message("tool", f"[shell]\n{text}".strip()) if text else None

    return None


def _messages_from_record(record: dict[str, Any]) -> Iterable[Message]:
    record_type = str(record.get("type", ""))
    payload = record.get("payload")
    timestamp = record.get("timestamp")
    if not isinstance(payload, dict):
        return ()

    if record_type == "response_item":
        payload_type = str(payload.get("type", ""))
        if payload_type == "message":
            role = str(payload.get("role", "")).lower()
            if role in {"user", "assistant"}:
                text = _content_text(payload.get("content"))
                if text and not (role == "user" and _is_injected_context(text)):
                    return (Message(role, text, str(timestamp) if timestamp else None),)
        tool = _tool_message(payload)
        if tool is not None:
            return (Message(tool.role, tool.text, str(timestamp) if timestamp else None),)
        return ()

    if record_type == "event_msg":
        event_type = str(payload.get("type", ""))
        role = {"user_message": "user", "agent_message": "assistant"}.get(event_type)
        if role:
            text = _content_text(payload.get("message", payload.get("content", "")))
            if text and not (role == "user" and _is_injected_context(text)):
                return (Message(role, text, str(timestamp) if timestamp else None),)
        return ()

    role = str(payload.get("role", "")).lower()
    if role in {"user", "assistant"}:
        text = _content_text(payload.get("content", payload.get("message", "")))
        if text and not (role == "user" and _is_injected_context(text)):
            return (Message(role, text, str(timestamp) if timestamp else None),)
    return ()


def parse_session(path: Path) -> ParsedSession:
    session_id = session_id_from_path(path)
    cwd: str | None = None
    created_at: str | None = None
    seen_timestamps: list[str] = []
    messages: list[Message] = []

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # Active sessions can be read while Codex is still writing the last JSONL line.
                continue
            if not isinstance(record, dict):
                continue

            timestamp = record.get("timestamp")
            if isinstance(timestamp, str) and timestamp:
                seen_timestamps.append(timestamp)

            if record.get("type") == "session_meta":
                payload = record.get("payload")
                if isinstance(payload, dict):
                    candidate_id = payload.get("id") or payload.get("session_id")
                    if isinstance(candidate_id, str) and candidate_id.strip():
                        session_id = candidate_id.strip()
                    candidate_cwd = payload.get("cwd")
                    if isinstance(candidate_cwd, str) and candidate_cwd.strip():
                        cwd = candidate_cwd
                    candidate_created = payload.get("timestamp")
                    if isinstance(candidate_created, str) and candidate_created:
                        created_at = candidate_created

            for message in _messages_from_record(record):
                if messages and messages[-1].role == message.role and messages[-1].text == message.text:
                    continue
                messages.append(message)

    if created_at is None:
        created_at = seen_timestamps[0] if seen_timestamps else _timestamp_from_mtime(path)
    updated_at = seen_timestamps[-1] if seen_timestamps else _timestamp_from_mtime(path)

    first_user = next((m.text for m in messages if m.role == "user"), "")
    original_title = _clean_title(first_user) if first_user else _clean_title(path.stem)

    return ParsedSession(
        session_id=session_id,
        path=path,
        cwd=cwd,
        created_at=created_at,
        updated_at=updated_at,
        original_title=original_title,
        messages=tuple(messages),
    )


def chunk_text(text: str, max_chars: int = 1600, overlap: int = 180) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            split = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
            if split > start + max_chars // 2:
                end = split + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks
