from __future__ import annotations

import html
import threading
from datetime import datetime
from pathlib import Path

import gradio as gr

from .config import Settings
from .semantic import SemanticSearch
from .store import SessionRow, Store


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


def _render_messages(messages) -> str:
    if not messages:
        return '<div class="small-note">В этой сессии не удалось извлечь видимые сообщения.</div>'
    blocks: list[str] = ['<div class="session-shell">']
    labels = {"user": "Ты", "assistant": "Codex", "tool": "Tool / command"}
    for message in messages[-400:]:
        role = message.role if message.role in labels else "tool"
        blocks.append(
            f'<div class="msg msg-{role}">'
            f'<div class="msg-role">{labels.get(role, role)}</div>'
            f'<div class="msg-text">{html.escape(message.text)}</div>'
            "</div>"
        )
    if len(messages) > 400:
        blocks.insert(
            1,
            f'<div class="small-note">Показаны последние 400 из {len(messages)} сообщений.</div>',
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
            try:
                semantic.ensure_embeddings()
            except Exception as exc:
                print(f"[codex-context] semantic warmup failed: {exc}")

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

    def view_session(session_id: str | None):
        if not session_id:
            return "", "Сессии не найдены.", "", ""
        row = store.get_session(session_id)
        parsed = store.load_parsed_session(session_id)
        if row is None:
            return "", "Сессия не найдена.", "", ""
        meta = (
            f"**ID:** `{row.session_id}`  \n"
            f"**Проект:** `{row.cwd or '—'}`  \n"
            f"**Обновлён:** {_format_time(row.updated_at)} · "
            f"**сообщений:** {row.message_count}  \n"
            f"**Файл:** `{row.path}`"
        )
        conversation = _render_messages(parsed.messages if parsed else ())
        resume = f"codex resume {row.session_id}"
        return row.title, meta, conversation, resume

    def refresh(preferred: str | None):
        sync = store.sync_sessions()
        sessions, choices, value = choices_and_default(preferred)
        title, meta, conversation, resume = view_session(value)
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
            status_markdown(),
            note,
        )

    def on_session_change(session_id: str | None):
        title, meta, conversation, resume = view_session(session_id)
        return session_id, title, meta, conversation, resume, ""

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
            return [], [], "Введи, что ты помнишь о старой работе."
        store.sync_sessions()
        result = semantic.search(query, int(top_k))
        rows = []
        state = []
        for hit in result.hits:
            snippet = " ".join(hit.text.split())
            if len(snippet) > 420:
                snippet = snippet[:419].rstrip() + "…"
            rows.append([hit.title, hit.role, round(hit.score, 4), snippet, hit.session_id])
            state.append({"session_id": hit.session_id})
        if result.mode == "semantic":
            note = f"Нашёл {len(rows)} совпадений локальным semantic search."
        else:
            note = (
                "⚠️ Semantic model сейчас недоступна; показан локальный FTS fallback. "
                f"Причина: `{result.detail}`"
            )
        return rows, state, note

    def open_result(result_state, evt: gr.SelectData):
        if not result_state:
            return (gr.update(), None, "", "", "", "", "")
        index = evt.index[0] if isinstance(evt.index, (tuple, list)) else evt.index
        try:
            session_id = result_state[int(index)]["session_id"]
        except (IndexError, KeyError, TypeError, ValueError):
            return (gr.update(), None, "", "", "", "", "")
        title, meta, conversation, resume = view_session(session_id)
        return (
            gr.update(value=session_id),
            session_id,
            title,
            meta,
            conversation,
            resume,
            "Открыта найденная сессия — можешь сразу переименовать её сверху.",
        )

    sessions, initial_choices, initial_id = choices_and_default()
    initial_title, initial_meta, initial_conversation, initial_resume = view_session(initial_id)

    with gr.Blocks(title="Codex Context") as demo:
        selected_session = gr.State(initial_id)
        result_state = gr.State([])

        gr.Markdown(
            "# Codex Context\n"
            "Локальная карта твоих Codex-сессий: нормальные названия, просмотр истории и поиск по смыслу.",
            elem_id="hero",
        )
        status = gr.Markdown(status_markdown())

        with gr.Tabs():
            with gr.Tab("Чаты"):
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
                conversation = gr.HTML(initial_conversation)

            with gr.Tab("Поиск по смыслу"):
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
                status,
                rename_status,
            ],
        )
        session_selector.change(
            on_session_change,
            inputs=[session_selector],
            outputs=[selected_session, title_box, meta, conversation, resume_cmd, rename_status],
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
            outputs=[results, result_state, search_note],
        )
        query.submit(
            do_search,
            inputs=[query, top_k],
            outputs=[results, result_state, search_note],
        )
        results.select(
            open_result,
            inputs=[result_state],
            outputs=[
                session_selector,
                selected_session,
                title_box,
                meta,
                conversation,
                resume_cmd,
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
