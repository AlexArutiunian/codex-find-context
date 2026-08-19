from __future__ import annotations

import html
import threading
import time
from datetime import datetime
from pathlib import Path

import gradio as gr

from .config import Settings
from .semantic import SemanticSearch
from .store import SessionRow, Store


MESSAGE_WINDOW = 400
MESSAGE_STEP = 400


CSS = """
.gradio-container { max-width: 1480px !important; }
#hero { margin-bottom: 0.4rem; }
#hero h1 { font-size: 2rem; margin-bottom: .15rem; }
#hero p { opacity: .75; margin-top: 0; }
.session-shell { height: 620px; overflow-y: auto; padding: 10px 4px; }
.msg { border: 1px solid var(--border-color-primary); border-radius: 14px; padding: 12px 14px; margin: 9px 2px; }
.msg-user { background: color-mix(in srgb, var(--primary-500) 10%, transparent); }
.msg-assistant { background: color-mix(in srgb, var(--neutral-500) 9%, transparent); }
.msg-tool { background: color-mix(in srgb, var(--neutral-500) 5%, transparent); opacity: .88; }
.msg-role { font-size: 12px; text-transform: uppercase; opacity: .6; font-weight: 700; margin-bottom: 7px; }
.msg-text { white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; line-height: 1.5; }
.small-note { opacity: .72; font-size: 13px; }
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
    """Return a zero-based start for a fixed-size message window.

    The UI exposes one-based message numbers. ``None`` means the latest window.
    """
    if total_messages <= 0:
        return 0
    max_start = max(0, total_messages - MESSAGE_WINDOW)
    if first_message is None:
        return max_start
    requested = max(1, int(round(first_message))) - 1
    return min(requested, max_start)


def _window_max_first(total_messages: int) -> int:
    return max(1, total_messages - MESSAGE_WINDOW + 1)


def _normalize_match_text(text: str) -> str:
    return " ".join(text.split())


def _first_message_for_snippet(messages, snippet: str) -> int | None:
    """Return a one-based window start centered near a semantic-search snippet."""
    needle = _normalize_match_text(snippet).rstrip("…").strip()
    if not needle or not messages:
        return None

    # Search result previews are capped at ~420 chars. A stable prefix is enough to
    # relocate the source message even if the original contained newlines.
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
    blocks: list[str] = ['<div class="session-shell">']
    labels = {"user": "Ты", "assistant": "Codex", "tool": "Tool / command"}
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
    )

    if settings.auto_index:
        def warm_semantic_index() -> None:
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

        threading.Thread(target=warm_semantic_index, daemon=True).start()

    def choices_and_default(preferred: str | None = None):
        sessions = store.list_sessions()
        choices = [(_session_label(row), row.session_id) for row in sessions]
        ids = {row.session_id for row in sessions}
        value = preferred if preferred in ids else (sessions[0].session_id if sessions else None)
        return sessions, choices, value

    def status_markdown() -> str:
        sessions = store.list_sessions()
        total, embedded = store.chunk_counts(settings.embedding_model)
        semantic_status = (
            f"semantic index **{embedded}/{total}** chunks"
            if total
            else "semantic index пока пуст"
        )
        return (
            f"**{len(sessions)}** сессий · {semantic_status} · "
            f"`CODEX_HOME={settings.codex_home}`"
        )

    def render_window(session_id: str | None, first_message: int | float | None = None):
        if not session_id:
            return (
                "",
                gr.update(minimum=1, maximum=1, value=1, interactive=False),
                "Сессии не найдены.",
            )

        parsed = store.load_parsed_session(session_id)
        messages = parsed.messages if parsed else ()
        total = len(messages)
        start = _window_start(total, first_message)
        end = min(total, start + MESSAGE_WINDOW)
        conversation = _render_messages(messages, start)
        slider = gr.update(
            minimum=1,
            maximum=_window_max_first(total),
            value=start + 1,
            step=1,
            interactive=total > MESSAGE_WINDOW,
        )
        if total:
            note = (
                f"**Показаны сообщения {start + 1}–{end} из {total}.** "
                f"Окно — до {MESSAGE_WINDOW} сообщений."
            )
        else:
            note = "В этой сессии нет извлечённых сообщений."
        return conversation, slider, note

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
            f"**Обновлён:** {_format_time(row.updated_at)} · "
            f"**сообщений:** {row.message_count}  \n"
            f"**Файл:** `{row.path}`"
        )
        conversation, slider, window_note = render_window(session_id, first_message)
        resume = f"codex resume {row.session_id}"
        return row.title, meta, conversation, resume, slider, window_note

    def refresh(preferred: str | None):
        sync = store.sync_sessions()
        sessions, choices, value = choices_and_default(preferred)
        title, meta, conversation, resume, slider, window_note = view_session(value)
        note = (
            f"Обновлено: найдено {sync.discovered}, переиндексировано {sync.reindexed}, "
            f"без изменений {sync.unchanged}, ошибок {sync.failed}."
        )
        return (
            gr.update(choices=choices, value=value),
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

    def on_session_change(session_id: str | None):
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

    def first_window(session_id: str | None):
        return render_window(session_id, 1)

    def last_window(session_id: str | None):
        return render_window(session_id, None)

    def save_title(session_id: str | None, title: str):
        if not session_id:
            return gr.update(), "Сначала выбери сессию."
        try:
            store.rename_session(session_id, title)
        except (ValueError, KeyError) as exc:
            return gr.update(), f"⚠️ {exc}"
        _, choices, _ = choices_and_default(session_id)
        return gr.update(choices=choices, value=session_id), "✅ Название сохранено локально."

    def reset_title(session_id: str | None):
        if not session_id:
            return gr.update(), "", "Сначала выбери сессию."
        store.clear_custom_title(session_id)
        row = store.get_session(session_id)
        _, choices, _ = choices_and_default(session_id)
        return (
            gr.update(choices=choices, value=session_id),
            row.title if row else "",
            "Вернул исходное название.",
        )

    def do_search(query: str, top_k: int):
        query = query.strip()
        if not query:
            return [], "Введи, что ты помнишь о старой работе."
        store.sync_sessions()
        result = semantic.search(query, int(top_k))
        rows = []
        for hit in result.hits:
            snippet = " ".join(hit.text.split())
            if len(snippet) > 420:
                snippet = snippet[:419].rstrip() + "…"
            rows.append([hit.title, hit.role, round(hit.score, 4), snippet, hit.session_id])
        if result.mode == "semantic":
            note = f"Нашёл {len(rows)} совпадений локальным semantic search."
            if result.detail:
                note += f" {result.detail}."
        else:
            note = (
                "⚠️ Semantic model сейчас недоступна; показан локальный FTS fallback. "
                f"Причина: `{result.detail}`"
            )
        return rows, note

    def open_result(evt: gr.SelectData):
        row_value = evt.row_value
        if not isinstance(row_value, (list, tuple)) or len(row_value) < 5:
            return (
                gr.update(),
                gr.update(),
                None,
                "",
                "",
                "",
                "",
                gr.update(),
                "",
                "Не удалось определить выбранную строку.",
            )

        session_id = str(row_value[4])
        snippet = str(row_value[3])
        parsed = store.load_parsed_session(session_id)
        first_message = (
            _first_message_for_snippet(parsed.messages, snippet)
            if parsed is not None
            else None
        )
        title, meta, conversation, resume, slider, window_note = view_session(
            session_id,
            first_message,
        )
        if first_message is not None:
            open_note = "Открыт чат прямо около найденного фрагмента."
        else:
            open_note = "Открыт найденный чат."
        return (
            gr.Tabs(selected="chats"),
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

    sessions, initial_choices, initial_id = choices_and_default()
    (
        initial_title,
        initial_meta,
        initial_conversation,
        initial_resume,
        initial_slider,
        initial_window_note,
    ) = view_session(initial_id)

    initial_row = store.get_session(initial_id) if initial_id else None
    initial_total = initial_row.message_count if initial_row else 0
    initial_start = _window_start(initial_total)

    with gr.Blocks(title="Codex Context") as demo:
        selected_session = gr.State(initial_id)

        gr.Markdown(
            "# Codex Context\n"
            "Локальная карта твоих Codex-сессий: нормальные названия, просмотр истории и поиск по смыслу.",
            elem_id="hero",
        )
        status = gr.Markdown(status_markdown())
        status_timer = gr.Timer(2.0, active=True)

        with gr.Tabs(selected="chats") as main_tabs:
            with gr.Tab("Чаты", id="chats"):
                with gr.Row():
                    with gr.Column(scale=5):
                        session_selector = gr.Dropdown(
                            choices=initial_choices,
                            value=initial_id,
                            label="Сессия",
                            filterable=True,
                        )
                    with gr.Column(scale=1, min_width=130):
                        refresh_btn = gr.Button("↻ Обновить", variant="secondary")

                with gr.Row():
                    title_box = gr.Textbox(
                        value=initial_title,
                        label="Моё название",
                        placeholder="Например: RWB — TensorRT и RealSense align",
                        scale=6,
                    )
                    save_title_btn = gr.Button("Сохранить", variant="primary", scale=1)
                    reset_title_btn = gr.Button("Сбросить", scale=1)
                rename_status = gr.Markdown()

                with gr.Row():
                    with gr.Column(scale=3):
                        meta = gr.Markdown(initial_meta)
                    with gr.Column(scale=2):
                        resume_cmd = gr.Code(
                            value=initial_resume,
                            language="shell",
                            label="Продолжить в Codex",
                            interactive=False,
                        )

                window_status = gr.Markdown(initial_window_note)
                with gr.Row():
                    first_btn = gr.Button("⏮ Начало", scale=1)
                    prev_btn = gr.Button("← 400", scale=1)
                    history_slider = gr.Slider(
                        minimum=1,
                        maximum=_window_max_first(initial_total),
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
                    "Поиск идёт локально по user/assistant сообщениям и важным tool/command фрагментам."
                )
                with gr.Row():
                    query = gr.Textbox(
                        label="Что ищем",
                        placeholder="например: где я чинил align depth у RealSense и переходил на 640x480",
                        scale=6,
                    )
                    top_k = gr.Slider(3, 30, value=10, step=1, label="Top-k", scale=1)
                    search_btn = gr.Button("Найти", variant="primary", scale=1)
                search_note = gr.Markdown()
                results = gr.Dataframe(
                    headers=["Чат", "Роль", "Score", "Релевантный фрагмент", "Session ID"],
                    datatype=["str", "str", "number", "str", "str"],
                    interactive=False,
                    wrap=True,
                    label="Кликни по строке, чтобы открыть чат",
                )

        status_timer.tick(
            status_markdown,
            inputs=[],
            outputs=[status],
            show_progress="hidden",
        )
        refresh_btn.click(
            refresh,
            inputs=[selected_session],
            outputs=[
                session_selector,
                selected_session,
                title_box,
                meta,
                conversation,
                resume_cmd,
                history_slider,
                window_status,
                status,
                rename_status,
            ],
        )
        session_selector.change(
            on_session_change,
            inputs=[session_selector],
            outputs=[
                selected_session,
                title_box,
                meta,
                conversation,
                resume_cmd,
                history_slider,
                window_status,
                rename_status,
            ],
        )
        history_slider.release(
            render_window,
            inputs=[selected_session, history_slider],
            outputs=[conversation, history_slider, window_status],
        )
        first_btn.click(
            first_window,
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
            last_window,
            inputs=[selected_session],
            outputs=[conversation, history_slider, window_status],
        )
        save_title_btn.click(
            save_title,
            inputs=[selected_session, title_box],
            outputs=[session_selector, rename_status],
        )
        reset_title_btn.click(
            reset_title,
            inputs=[selected_session],
            outputs=[session_selector, title_box, rename_status],
        )
        search_btn.click(
            do_search,
            inputs=[query, top_k],
            outputs=[results, search_note],
        )
        query.submit(
            do_search,
            inputs=[query, top_k],
            outputs=[results, search_note],
        )
        results.select(
            open_result,
            inputs=None,
            outputs=[
                main_tabs,
                session_selector,
                selected_session,
                title_box,
                meta,
                conversation,
                resume_cmd,
                history_slider,
                window_status,
                rename_status,
            ],
        )

    return demo


def main() -> None:
    settings = Settings.from_env()
    app = create_app(settings)
    app.queue(default_concurrency_limit=4).launch(
        server_name=settings.host,
        server_port=settings.port,
        inbrowser=False,
        show_error=True,
        theme=gr.themes.Soft(),
        css=CSS,
    )


if __name__ == "__main__":
    main()
