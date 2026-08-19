from __future__ import annotations

import html
import threading
import time
from datetime import datetime
from pathlib import Path

import gradio as gr

from .codex_rpc import CodexRpcError, set_thread_name
from .config import Settings
from .semantic import SemanticSearch
from .store import SessionRow, Store


MESSAGE_WINDOW = 400
MESSAGE_STEP = 400
ALL_SEARCH_SCOPE = "__all__"

CSS = """
html, body {
    width: 100%;
    max-width: 100%;
    overflow-x: hidden;
}
.gradio-container {
    max-width: none !important;
    width: 100% !important;
    margin: 0 !important;
    padding: 18px clamp(14px, 2vw, 36px) 30px !important;
    overflow-x: hidden;
}
.gradio-container * { box-sizing: border-box; }
#hero { margin-bottom: .35rem; }
#hero h1 { font-size: clamp(1.75rem, 2.3vw, 2.35rem); margin-bottom: .12rem; }
#hero p { opacity: .75; margin-top: 0; }

.responsive-row,
.history-controls {
    width: 100%;
    min-width: 0;
}
.responsive-row > *,
.history-controls > * {
    min-width: 0 !important;
}
.gradio-container button {
    min-height: 42px;
    touch-action: manipulation;
}

.session-shell {
    width: 100%;
    height: min(68vh, 780px);
    min-height: 480px;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 10px 4px;
    scrollbar-gutter: stable;
    overscroll-behavior: contain;
}
.msg {
    width: 100%;
    max-width: 100%;
    border: 1px solid var(--border-color-primary);
    border-radius: 14px;
    padding: 12px 14px;
    margin: 9px 2px;
    content-visibility: auto;
    contain-intrinsic-size: 120px;
}
.msg-user { background: color-mix(in srgb, var(--primary-500) 10%, transparent); }
.msg-assistant { background: color-mix(in srgb, var(--neutral-500) 9%, transparent); }
.msg-tool { background: color-mix(in srgb, var(--neutral-500) 5%, transparent); opacity: .88; }
.msg-role { font-size: 12px; text-transform: uppercase; opacity: .6; font-weight: 700; margin-bottom: 7px; }
.msg-text {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    word-break: break-word;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 13px;
    line-height: 1.5;
}
.small-note { opacity: .72; font-size: 13px; }

#search-results {
    width: 100%;
    max-width: 100%;
    overflow-x: auto !important;
    -webkit-overflow-scrolling: touch;
}
#search-results table { min-width: 760px; }

@media (max-width: 1100px) {
    .gradio-container {
        padding-left: 16px !important;
        padding-right: 16px !important;
    }
    .responsive-row {
        flex-wrap: wrap !important;
        gap: 10px !important;
    }
    .responsive-row > * {
        flex: 1 1 320px !important;
    }
    .history-controls {
        flex-wrap: wrap !important;
        gap: 8px !important;
    }
    .history-controls > :nth-child(3) {
        order: -1;
        flex: 1 1 100% !important;
        width: 100% !important;
    }
    .history-controls > :not(:nth-child(3)) {
        flex: 1 1 140px !important;
    }
    .session-shell { min-height: 430px; height: 64vh; }
}

@media (max-width: 720px) {
    .gradio-container {
        padding: 10px 9px 22px !important;
    }
    #hero h1 { font-size: 1.65rem; }
    #hero p { font-size: .92rem; }

    .responsive-row {
        flex-direction: column !important;
        flex-wrap: nowrap !important;
        gap: 8px !important;
    }
    .responsive-row > * {
        width: 100% !important;
        max-width: 100% !important;
        min-width: 0 !important;
        flex: 1 1 auto !important;
    }
    .title-actions-row > :not(:first-child),
    .search-scope-row > :last-child {
        width: 100% !important;
    }

    .history-controls {
        display: grid !important;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) !important;
        gap: 8px !important;
        align-items: stretch !important;
    }
    .history-controls > * {
        width: 100% !important;
        max-width: 100% !important;
        min-width: 0 !important;
        margin: 0 !important;
    }
    .history-controls > :nth-child(3) {
        grid-column: 1 / -1;
        grid-row: 1;
        order: initial;
    }

    .session-shell {
        min-height: 360px;
        height: 62dvh;
        padding: 5px 1px;
    }
    .msg {
        border-radius: 11px;
        padding: 10px 11px;
        margin: 7px 0;
    }
    .msg-text { font-size: 12px; line-height: 1.45; }
    .msg-role { font-size: 11px; }

    .gradio-container input,
    .gradio-container textarea,
    .gradio-container select {
        font-size: 16px !important;
    }
    .gradio-container button { min-height: 46px; }

    #search-results table { min-width: 680px; }
}

@media (max-width: 430px) {
    .gradio-container { padding-left: 7px !important; padding-right: 7px !important; }
    #hero h1 { font-size: 1.5rem; }
    .history-controls { gap: 6px !important; }
    .session-shell { min-height: 330px; height: 60dvh; }
    .msg-text { font-size: 11.5px; }
}
"""


def _format_time(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime(
            "%d.%m.%Y %H:%M"
        )
    except ValueError:
        return value


def _session_label(row: SessionRow) -> str:
    when = _format_time(row.updated_at or row.created_at)
    cwd = Path(row.cwd).name if row.cwd else "без cwd"
    return f"{row.title}  ·  {cwd}  ·  {when}"


def _window_start(total_messages: int, first_message: int | float | None = None) -> int:
    if total_messages <= 0:
        return 0
    max_start = max(0, total_messages - MESSAGE_WINDOW)
    if first_message is None:
        return max_start
    requested = max(1, int(round(first_message))) - 1
    return min(requested, max_start)


def _window_max_first(total_messages: int) -> int:
    return max(1, total_messages - MESSAGE_WINDOW + 1)


def _history_slider_max(total_messages: int) -> int:
    # Gradio rejects Slider(minimum == maximum), even when interactive=False.
    return max(2, _window_max_first(total_messages))


def _normalize_match_text(text: str) -> str:
    return " ".join(text.split())


def _first_message_for_snippet(messages, snippet: str) -> int | None:
    needle = _normalize_match_text(snippet).rstrip("…").strip()
    if not needle or not messages:
        return None
    probe = needle[:220]
    short_probe = needle[:120]
    for index, message in enumerate(messages):
        haystack = _normalize_match_text(message.text)
        if probe in haystack or (len(short_probe) >= 40 and short_probe in haystack):
            desired_start = max(0, index - MESSAGE_WINDOW // 2)
            max_start = max(0, len(messages) - MESSAGE_WINDOW)
            return min(desired_start, max_start) + 1
    return None


def _render_messages(messages, start: int = 0) -> str:
    if not messages:
        return '<div class="small-note">В этой сессии не удалось извлечь видимые сообщения.</div>'

    end = min(len(messages), start + MESSAGE_WINDOW)
    labels = {"user": "Ты", "assistant": "Codex", "tool": "Tool / command"}
    blocks: list[str] = ['<div class="session-shell">']
    for index, message in enumerate(messages[start:end], start=start + 1):
        role = message.role if message.role in labels else "tool"
        blocks.append(
            f'<div class="msg msg-{role}">'
            f'<div class="msg-role">{labels.get(role, role)} · #{index}</div>'
            f'<div class="msg-text">{html.escape(message.text)}</div>'
            "</div>"
        )
    blocks.append("</div>")
    return "".join(blocks)


def create_app(settings: Settings | None = None) -> gr.Blocks:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()
    store = Store(settings.db_path, settings.codex_home)
    store.sync_sessions()
    semantic = SemanticSearch(
        store=store,
        model_name=settings.embedding_model,
        cache_dir=settings.model_cache_dir,
        threads=settings.embedding_threads,
        index_batch_size=settings.index_batch_size,
        index_pause_seconds=settings.index_pause_seconds,
    )

    indexer_guard = threading.Lock()
    indexer_thread: threading.Thread | None = None

    def kick_indexer() -> None:
        nonlocal indexer_thread
        if not settings.auto_index:
            return
        with indexer_guard:
            if indexer_thread is not None and indexer_thread.is_alive():
                return

            def worker() -> None:
                failures = 0
                while True:
                    try:
                        semantic.ensure_embeddings()
                        return
                    except Exception as exc:
                        failures += 1
                        delay = min(10, 2 * failures)
                        print(
                            "[codex-context] semantic warmup failed; "
                            f"retrying in {delay}s: {exc}"
                        )
                        time.sleep(delay)

            indexer_thread = threading.Thread(
                target=worker,
                name="codex-context-indexer",
                daemon=True,
            )
            indexer_thread.start()

    kick_indexer()

    def choices_and_default(preferred: str | None = None):
        sessions = store.list_sessions()
        choices = [(_session_label(row), row.session_id) for row in sessions]
        ids = {row.session_id for row in sessions}
        value = preferred if preferred in ids else (sessions[0].session_id if sessions else None)
        return sessions, choices, value

    def search_scope_choices():
        sessions = store.list_sessions()
        return [("🌐 Все чаты", ALL_SEARCH_SCOPE)] + [
            (_session_label(row), row.session_id) for row in sessions
        ]

    def search_scope_update(current: str | None = None):
        choices = search_scope_choices()
        valid_values = {value for _, value in choices}
        value = current if current in valid_values else ALL_SEARCH_SCOPE
        return gr.update(choices=choices, value=value)

    def status_markdown() -> str:
        sessions = store.list_sessions()
        total, embedded = store.chunk_counts(settings.embedding_model)
        semantic_status = (
            f"semantic index **{embedded}/{total}** chunks"
            if total
            else "semantic index пока пуст"
        )
        access_status = (
            "LAN **включён**"
            if settings.host in {"0.0.0.0", "::"}
            else "доступ **только с этого ПК**"
        )
        return (
            f"**{len(sessions)}** сессий · {semantic_status} · "
            f"фон: **{settings.embedding_threads} CPU потока** · {access_status} · "
            f"`CODEX_HOME={settings.codex_home}`"
        )

    def slider_update(total: int, start: int):
        return gr.update(
            minimum=1,
            maximum=_history_slider_max(total),
            value=max(1, start + 1),
            step=1,
            interactive=total > MESSAGE_WINDOW,
        )

    def render_window(session_id: str | None, first_message: int | float | None = None):
        if not session_id:
            return "", slider_update(0, 0), "Сессии не найдены."

        parsed = store.load_parsed_session(session_id)
        messages = parsed.messages if parsed else ()
        total = len(messages)
        start = _window_start(total, first_message)
        end = min(total, start + MESSAGE_WINDOW)
        conversation = _render_messages(messages, start)
        if total:
            note = (
                f"**Показаны сообщения {start + 1}–{end} из {total}.** "
                f"Окно — до {MESSAGE_WINDOW} сообщений."
            )
        else:
            note = "В этой сессии нет извлечённых сообщений."
        return conversation, slider_update(total, start), note

    def view_session(session_id: str | None, first_message: int | float | None = None):
        if not session_id:
            conversation, slider, window_note = render_window(None)
            return "", "Сессии не найдены.", conversation, "", slider, window_note
        row = store.get_session(session_id)
        if row is None:
            conversation, slider, window_note = render_window(None)
            return "", "Сессия не найдена.", conversation, "", slider, window_note

        meta = (
            f"**ID:** `{row.session_id}`  \n"
            f"**Проект:** `{row.cwd or '—'}`  \n"
            f"**Обновлён:** {_format_time(row.updated_at)} · **сообщений:** {row.message_count}  \n"
            f"**Файл:** `{row.path}`"
        )
        conversation, slider, window_note = render_window(session_id, first_message)
        return (
            row.title,
            meta,
            conversation,
            f"codex resume {row.session_id}",
            slider,
            window_note,
        )

    def refresh(preferred: str | None, current_search_scope: str | None):
        sync = store.sync_sessions()
        kick_indexer()
        _, choices, value = choices_and_default(preferred)
        title, meta, conversation, resume, slider, window_note = view_session(value)
        note = (
            f"Обновлено: найдено {sync.discovered}, переиндексировано {sync.reindexed}, "
            f"без изменений {sync.unchanged}, ошибок {sync.failed}."
        )
        return (
            gr.update(choices=choices, value=value),
            search_scope_update(current_search_scope),
            value,
            title,
            meta,
            conversation,
            resume,
            slider,
            window_note,
            status_markdown(),
            note,
        )

    def on_session_input(session_id: str | None):
        title, meta, conversation, resume, slider, window_note = view_session(session_id)
        return session_id, title, meta, conversation, resume, slider, window_note, ""

    def previous_window(session_id: str | None, first_message: int | float):
        if not session_id:
            return render_window(None)
        row = store.get_session(session_id)
        total = row.message_count if row else 0
        current_start = _window_start(total, first_message)
        target_first = max(0, current_start - MESSAGE_STEP) + 1
        return render_window(session_id, target_first)

    def next_window(session_id: str | None, first_message: int | float):
        if not session_id:
            return render_window(None)
        row = store.get_session(session_id)
        total = row.message_count if row else 0
        current_start = _window_start(total, first_message)
        max_start = max(0, total - MESSAGE_WINDOW)
        target_first = min(max_start, current_start + MESSAGE_STEP) + 1
        return render_window(session_id, target_first)

    def save_title(session_id: str | None, title: str, current_search_scope: str | None):
        if not session_id:
            return gr.update(), gr.update(), "Сначала выбери сессию."
        try:
            store.rename_session(session_id, title)
        except (ValueError, KeyError) as exc:
            return gr.update(), gr.update(), f"⚠️ {exc}"

        row = store.get_session(session_id)
        normalized_title = row.title if row else " ".join(title.split()).strip()
        native_error: str | None = None
        try:
            set_thread_name(settings.codex_home, session_id, normalized_title)
        except CodexRpcError as exc:
            native_error = str(exc)

        _, choices, _ = choices_and_default(session_id)
        selector = gr.update(choices=choices, value=session_id)
        scope = search_scope_update(current_search_scope)
        if native_error:
            return (
                selector,
                scope,
                "⚠️ Название сохранено в Codex Context, но сам Codex не обновился: "
                f"`{native_error}`",
            )
        return selector, scope, "✅ Название сохранено и в Codex, и в Codex Context."

    def reset_title(session_id: str | None, current_search_scope: str | None):
        if not session_id:
            return gr.update(), gr.update(), "", "Сначала выбери сессию."
        row_before = store.get_session(session_id)
        if row_before is None:
            return gr.update(), gr.update(), "", "Сессия не найдена."

        store.clear_custom_title(session_id)
        row = store.get_session(session_id)
        restored = row.title if row else row_before.original_title
        native_error: str | None = None
        try:
            set_thread_name(settings.codex_home, session_id, restored)
        except CodexRpcError as exc:
            native_error = str(exc)

        _, choices, _ = choices_and_default(session_id)
        selector = gr.update(choices=choices, value=session_id)
        scope = search_scope_update(current_search_scope)
        if native_error:
            note = f"⚠️ Локальное название сброшено, но Codex не обновился: `{native_error}`"
        else:
            note = "Название сброшено и синхронизировано с Codex."
        return selector, scope, restored, note

    def do_search(query: str, top_k: int, scope_value: str | None):
        query = query.strip()
        if not query:
            return [], "Введи, что ты помнишь о старой работе."

        store.sync_sessions()
        kick_indexer()
        session_id = None if not scope_value or scope_value == ALL_SEARCH_SCOPE else scope_value
        scope_row = store.get_session(session_id) if session_id else None
        if session_id and scope_row is None:
            return [], "⚠️ Выбранный чат больше не найден. Нажми «Обновить»."

        result = semantic.search(query, int(top_k), session_id=session_id)
        rows = []
        for hit in result.hits:
            snippet = " ".join(hit.text.split())
            if len(snippet) > 420:
                snippet = snippet[:419].rstrip() + "…"
            rows.append([hit.title, hit.role, round(hit.score, 4), snippet, hit.session_id])

        scope_text = (
            f"в чате **{scope_row.title}**"
            if scope_row is not None
            else "по всем чатам"
        )
        if result.mode == "semantic":
            note = f"Нашёл {len(rows)} совпадений {scope_text} локальным semantic search."
            if result.detail:
                note += f" {result.detail}."
        else:
            note = (
                f"⚠️ Semantic model сейчас недоступна {scope_text}; "
                "показан локальный FTS fallback. "
                f"Причина: `{result.detail}`"
            )
        return rows, note

    def open_result(evt: gr.SelectData):
        row_value = evt.row_value
        if not isinstance(row_value, (list, tuple)) or len(row_value) < 5:
            return (
                gr.update(), gr.update(), None, "", "", "", "",
                slider_update(0, 0), "", "Не удалось определить выбранную строку."
            )

        session_id = str(row_value[4])
        snippet = str(row_value[3])
        parsed = store.load_parsed_session(session_id)
        first_message = (
            _first_message_for_snippet(parsed.messages, snippet) if parsed is not None else None
        )
        title, meta, conversation, resume, slider, window_note = view_session(
            session_id, first_message
        )
        open_note = (
            "Открыт чат прямо около найденного фрагмента."
            if first_message is not None
            else "Открыт найденный чат."
        )
        return (
            gr.update(selected="chats"),
            gr.update(value=session_id),
            session_id,
            title,
            meta,
            conversation,
            resume,
            slider,
            window_note,
            open_note,
        )

    _, initial_choices, initial_id = choices_and_default()
    initial_search_choices = [("🌐 Все чаты", ALL_SEARCH_SCOPE)] + initial_choices
    (
        initial_title,
        initial_meta,
        initial_conversation,
        initial_resume,
        _initial_slider,
        initial_window_note,
    ) = view_session(initial_id)
    initial_row = store.get_session(initial_id) if initial_id else None
    initial_total = initial_row.message_count if initial_row else 0
    initial_start = _window_start(initial_total)

    with gr.Blocks(title="Codex Context", fill_width=True) as demo:
        selected_session = gr.State(initial_id)

        gr.Markdown(
            "# Codex Context\n"
            "Локальная карта твоих Codex-сессий: нормальные названия, просмотр истории и поиск по смыслу.",
            elem_id="hero",
        )
        status = gr.Markdown(status_markdown())
        status_timer = gr.Timer(settings.status_refresh_seconds, active=True)

        with gr.Tabs(selected="chats") as main_tabs:
            with gr.Tab("Чаты", id="chats"):
                with gr.Row(elem_classes=["responsive-row", "session-picker-row"]):
                    with gr.Column(scale=8):
                        session_selector = gr.Dropdown(
                            choices=initial_choices,
                            value=initial_id,
                            label="Сессия",
                            filterable=True,
                        )
                    with gr.Column(scale=1, min_width=150):
                        refresh_btn = gr.Button("↻ Обновить", variant="secondary")

                with gr.Row(elem_classes=["responsive-row", "title-actions-row"]):
                    title_box = gr.Textbox(
                        value=initial_title,
                        label="Моё название",
                        placeholder="Например: RWB — TensorRT и RealSense align",
                        scale=8,
                    )
                    save_title_btn = gr.Button("Сохранить", variant="primary", scale=1)
                    reset_title_btn = gr.Button("Сбросить", scale=1)
                rename_status = gr.Markdown()

                with gr.Row(elem_classes=["responsive-row", "meta-row"]):
                    with gr.Column(scale=7):
                        meta = gr.Markdown(initial_meta)
                    with gr.Column(scale=3):
                        resume_cmd = gr.Code(
                            value=initial_resume,
                            language="shell",
                            label="Продолжить в Codex",
                            interactive=False,
                        )

                window_status = gr.Markdown(initial_window_note)
                with gr.Row(elem_classes=["history-controls"]):
                    first_btn = gr.Button("⏮ Начало", scale=1)
                    prev_btn = gr.Button("← 400", scale=1)
                    history_slider = gr.Slider(
                        minimum=1,
                        maximum=_history_slider_max(initial_total),
                        value=initial_start + 1,
                        step=1,
                        label="Первое сообщение в окне",
                        interactive=initial_total > MESSAGE_WINDOW,
                        scale=7,
                    )
                    next_btn = gr.Button("400 →", scale=1)
                    last_btn = gr.Button("Конец ⏭", scale=1)

                conversation = gr.HTML(initial_conversation)

            with gr.Tab("Поиск по смыслу", id="search"):
                gr.Markdown(
                    "Опиши не точную фразу, а **что ты тогда делал**. "
                    "Можно искать по всей истории или только внутри одного выбранного чата."
                )
                with gr.Row(elem_classes=["responsive-row", "search-scope-row"]):
                    search_scope = gr.Dropdown(
                        choices=initial_search_choices,
                        value=ALL_SEARCH_SCOPE,
                        label="Где искать",
                        filterable=True,
                        scale=8,
                    )
                    current_chat_btn = gr.Button("Текущий чат", variant="secondary", scale=1)
                with gr.Row(elem_classes=["responsive-row", "search-controls-row"]):
                    query = gr.Textbox(
                        label="Что ищем внутри выбранной области",
                        placeholder="например: где в этом чате я менял threshold и почему",
                        scale=8,
                    )
                    top_k = gr.Slider(3, 30, value=10, step=1, label="Top-k", scale=1)
                    search_btn = gr.Button("Найти", variant="primary", scale=1)
                search_note = gr.Markdown()
                results = gr.Dataframe(
                    headers=["Чат", "Роль", "Score", "Релевантный фрагмент", "Session ID"],
                    datatype=["str", "str", "number", "str", "str"],
                    interactive=False,
                    wrap=True,
                    label="Кликни по строке, чтобы открыть точное место в чате",
                    elem_id="search-results",
                )

        status_timer.tick(
            status_markdown,
            inputs=[],
            outputs=[status],
            show_progress="hidden",
        )
        refresh_btn.click(
            refresh,
            inputs=[selected_session, search_scope],
            outputs=[
                session_selector, search_scope, selected_session, title_box, meta,
                conversation, resume_cmd, history_slider, window_status, status, rename_status,
            ],
        )
        # .input fires only for user interaction. Programmatic dropdown updates from
        # search/rename must not trigger a second callback that resets the history window.
        session_selector.input(
            on_session_input,
            inputs=[session_selector],
            outputs=[
                selected_session, title_box, meta, conversation, resume_cmd,
                history_slider, window_status, rename_status,
            ],
        )
        history_slider.release(
            render_window,
            inputs=[selected_session, history_slider],
            outputs=[conversation, history_slider, window_status],
        )
        first_btn.click(
            lambda session_id: render_window(session_id, 1),
            inputs=[selected_session],
            outputs=[conversation, history_slider, window_status],
        )
        prev_btn.click(
            previous_window,
            inputs=[selected_session, history_slider],
            outputs=[conversation, history_slider, window_status],
        )
        next_btn.click(
            next_window,
            inputs=[selected_session, history_slider],
            outputs=[conversation, history_slider, window_status],
        )
        last_btn.click(
            lambda session_id: render_window(session_id, None),
            inputs=[selected_session],
            outputs=[conversation, history_slider, window_status],
        )
        save_title_btn.click(
            save_title,
            inputs=[selected_session, title_box, search_scope],
            outputs=[session_selector, search_scope, rename_status],
        )
        reset_title_btn.click(
            reset_title,
            inputs=[selected_session, search_scope],
            outputs=[session_selector, search_scope, title_box, rename_status],
        )
        current_chat_btn.click(
            lambda session_id: gr.update(value=session_id or ALL_SEARCH_SCOPE),
            inputs=[selected_session],
            outputs=[search_scope],
        )
        search_btn.click(
            do_search,
            inputs=[query, top_k, search_scope],
            outputs=[results, search_note],
        )
        query.submit(
            do_search,
            inputs=[query, top_k, search_scope],
            outputs=[results, search_note],
        )
        results.select(
            open_result,
            inputs=None,
            outputs=[
                main_tabs, session_selector, selected_session, title_box, meta,
                conversation, resume_cmd, history_slider, window_status, rename_status,
            ],
        )

    return demo


def main() -> None:
    settings = Settings.from_env()
    app = create_app(settings)
    app.queue(default_concurrency_limit=2).launch(
        server_name=settings.host,
        server_port=settings.port,
        inbrowser=False,
        show_error=True,
        theme=gr.themes.Soft(),
        css=CSS,
    )


if __name__ == "__main__":
    main()
