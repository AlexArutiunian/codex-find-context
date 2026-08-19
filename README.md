# Codex Context

Локальный web-интерфейс для истории **Codex CLI**: чтобы не вспоминать, какой чат назывался первыми словами старого промта и где именно ты месяц назад правил нужную вещь.

## Что уже умеет

- автоматически находит `~/.codex/sessions/**/*.jsonl` и `~/.codex/archived_sessions/**/*.jsonl`;
- показывает сессии нормальным списком: название, проект/cwd, дата;
- позволяет **переименовать чат вручную** и синхронизирует имя с самим Codex через его локальный `thread/name/set` API;
- показывает историю user / Codex / tool-command окном по 400 сообщений с навигацией начало/назад/ползунок/вперёд/конец;
- даёт готовую команду `codex resume <session_id>`;
- делает **локальный semantic search** по смыслу через лёгкий embedding runtime FastEmbed/ONNX;
- умеет искать либо по всей истории, либо **только внутри одного выбранного чата**;
- понимает русский + английский/код благодаря multilingual embedding model;
- при проблеме с embedding model автоматически остаётся рабочий SQLite FTS5 fallback;
- индексирует только изменившиеся rollout-файлы;
- умеет жить как `systemd --user` сервис и всегда быть доступным в закреплённой вкладке браузера;
- адаптируется под desktop / tablet / phone;
- по умолчанию ограничивает embedding runtime двумя CPU-потоками, чтобы фоновый индексатор не забирал весь компьютер.

Историю rollout Codex Context **не переписывает**. Собственный индекс и копия пользовательских названий лежат в `~/.local/share/codex-find-context/`. Для нативного имени приложение обращается к локальному `codex app-server`, который сам обновляет свои метаданные.

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

## Планшет / телефон в локальной сети

По умолчанию сервис слушает только `127.0.0.1`, поэтому другие устройства его не видят. Это специально: в Codex-истории могут быть код, локальные пути и команды.

Чтобы временно открыть интерфейс устройствам в той же доверенной локальной сети:

```bash
cd ~/codex-find-context
bash access.sh lan
```

Скрипт покажет адрес вида:

```text
http://192.168.1.25:7860
```

Его и открывай на планшете/телефоне, подключённом к тому же Wi‑Fi/LAN.

Вернуть доступ только с Ubuntu-ПК:

```bash
bash access.sh local
```

**Важно:** встроенной авторизации в LAN-режиме сейчас нет. Не включай его в публичной/недоверенной сети и не пробрасывай порт `7860` наружу через роутер.

Интерфейс имеет отдельные breakpoints для широкого экрана, планшета и телефона: панели складываются вертикально, история остаётся прокручиваемой, кнопки имеют touch-friendly размер, а широкая таблица результатов получает собственный горизонтальный scroll вместо растягивания всей страницы.

## Первый semantic indexing

Web UI появляется после быстрого scan локальных JSONL, а dense embeddings строятся в фоне. При первом запуске FastEmbed скачает multilingual embedding model; после этого веса кэшируются локально и дальнейший поиск работает без API и без отправки истории наружу.

Верхняя строка UI показывает прогресс вида `semantic index 37696/150824 chunks` и текущий лимит CPU-потоков.

**Поиск не ждёт окончания индексации:** пока готова только часть embeddings, semantic search работает по уже готовой части. Остальное спокойно достраивается в фоне. Если model runtime недоступен, UI показывает причину и выполняет SQLite FTS5 fallback.

По умолчанию фон настроен как desktop-friendly:

| Переменная | Default | Что делает |
|---|---:|---|
| `CODEX_CONTEXT_EMBED_THREADS` | `2` | CPU-потоки ONNX/FastEmbed |
| `CODEX_CONTEXT_INDEX_BATCH` | `32` | chunks за один embedding batch |
| `CODEX_CONTEXT_INDEX_PAUSE` | `0.08` | пауза между batch, секунды |
| `CODEX_CONTEXT_STATUS_REFRESH` | `5` | обновление прогресса UI, секунды |

Если однажды захочется быстрее закончить первоначальную индексацию, можно временно поставить `CODEX_CONTEXT_EMBED_THREADS=4`. Для постоянного фонового сервиса `2` — более спокойный default.

## Как искать

Хороший запрос — не обязательно точная цитата. Например:

```text
где я чинил align depth RealSense и решил перейти на 640x480
```

или:

```text
чат где codex делал git worktree для face recognition и потом не был настроен user.email
```

Во вкладке поиска можно выбрать `🌐 Все чаты` или конкретную сессию. Кнопка **«Текущий чат»** быстро ограничивает поиск тем чатом, который сейчас открыт.

В выдаче виден **релевантный участок**, score, чат и Session ID. Клик по строке открывает найденную сессию во вкладке «Чаты» примерно около найденного места.

## Где хранятся данные

| Что | Default |
|---|---|
| Codex history | `~/.codex` |
| SQLite index + copy of custom titles | `~/.local/share/codex-find-context/index.sqlite3` |
| Embedding cache | `~/.local/share/codex-find-context/models` |
| Web UI | `127.0.0.1:7860` |

Настройки можно переопределить environment variables. Сам `.env` приложение специально не требует.

## Тесты

```bash
source .venv/bin/activate
PYTHONPATH=src pytest -q
```

Тесты покрывают parser rollout-подобного JSONL, незавершённую последнюю строку активного writer, rename persistence, нативный rename RPC payload, incremental reindex, semantic ranking, поиск внутри конкретного чата и навигацию по истории.

## Архитектура и trade-offs

См. [`docs/architecture.md`](docs/architecture.md).

Главное решение: **не править JSONL rollout вручную ради названий**. Нативное имя задаётся через Codex app-server, а собственная SQLite остаётся индексом/кэшем Codex Context. Так `codex resume` остаётся владельцем своей истории, а приложение не ломает формат сессий.
