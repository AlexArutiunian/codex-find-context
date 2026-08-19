# Codex Context

Локальный web-интерфейс для истории **Codex CLI**: чтобы не вспоминать, какой чат назывался первыми словами старого промта и где именно ты месяц назад правил нужную вещь.

## Что уже умеет

- автоматически находит `~/.codex/sessions/**/*.jsonl` и `~/.codex/archived_sessions/**/*.jsonl`;
- показывает сессии нормальным списком: название, проект/cwd, дата;
- позволяет **переименовать чат вручную** — имя сохраняется локально и не ломает Codex rollout;
- показывает саму историю user / Codex / tool-command;
- даёт готовую команду `codex resume <session_id>`;
- делает **локальный semantic search** по смыслу через лёгкий embedding runtime FastEmbed/ONNX;
- понимает русский + английский/код благодаря multilingual embedding model;
- при проблеме с embedding model автоматически остаётся рабочий SQLite FTS5 fallback;
- индексирует только изменившиеся rollout-файлы;
- умеет жить как `systemd --user` сервис и всегда быть доступным в закреплённой вкладке браузера.

Codex Context **не пишет в `~/.codex`**. Пользовательские названия и индекс лежат в `~/.local/share/codex-find-context/`.

## Запуск на Ubuntu

```bash
git clone https://github.com/AlexArutiunian/codex-find-context.git
cd codex-find-context
bash setup.sh --autostart
```

После установки открой и закрепи в Chrome:

```text
http://127.0.0.1:7860
```

Если автозапуск не нужен:

```bash
bash setup.sh
bash run.sh
```

Проверить сервис:

```bash
systemctl --user status codex-context.service
journalctl --user -u codex-context.service -f
```

Остановить/отключить:

```bash
systemctl --user disable --now codex-context.service
```

## Первый semantic indexing

Web UI появляется сразу после быстрого scan локальных JSONL. Dense embeddings строятся в фоне. При первом запуске FastEmbed скачает multilingual embedding model; после этого веса кэшируются локально и дальнейший поиск работает без API и без отправки истории наружу.

Верхняя строка UI показывает прогресс вида `semantic index 1840/1840 chunks`.

Если искать до окончания индексации, поиск дождётся локального индекса. Если model runtime не загрузился, UI покажет причину и выполнит FTS5 fallback вместо падения.

## Как искать

Хороший запрос — не обязательно точная цитата. Например:

```text
где я чинил align depth realSense и решил перейти на 640x480
```

или:

```text
чат где codex делал git worktree для face recognition и потом не был настроен user.email
```

В выдаче виден **релевантный участок**, score, чат и Session ID. Клик по строке открывает найденную сессию во вкладке «Чаты».

## Где хранятся данные

| Что | Default |
|---|---|
| Codex history | `~/.codex` |
| SQLite index + custom titles | `~/.local/share/codex-find-context/index.sqlite3` |
| Embedding cache | `~/.local/share/codex-find-context/models` |
| Web UI | `127.0.0.1:7860` |

Настройки можно переопределить environment variables из `.env.example`. Сам `.env` приложение специально не требует.

## Тесты

```bash
source .venv/bin/activate
PYTHONPATH=src pytest -q
```

Тесты покрывают parser реального rollout-подобного JSONL, незавершённую последнюю строку активного writer, rename persistence, incremental reindex и semantic ranking.

## Архитектура и trade-offs

См. [`docs/architecture.md`](docs/architecture.md).

Главное решение: **не редактировать внутренние Codex session files ради названий**. Это UI metadata. Так `codex resume` остаётся единственным владельцем своей истории, а приложение можно удалить без последствий для Codex.
