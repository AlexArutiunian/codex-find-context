#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT"
if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
chmod +x "$ROOT/setup.sh" "$ROOT/run.sh"

echo
echo "Готово. Ручной запуск: ./run.sh"
echo "Открыть: http://127.0.0.1:${CODEX_CONTEXT_PORT:-7860}"

if [[ "${1:-}" == "--autostart" ]]; then
  SERVICE_DIR="$HOME/.config/systemd/user"
  SERVICE_FILE="$SERVICE_DIR/codex-context.service"
  mkdir -p "$SERVICE_DIR"
  cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=Codex Context local dashboard
After=default.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PYTHONPATH=$ROOT/src
Environment=CODEX_CONTEXT_HOST=127.0.0.1
Environment=CODEX_CONTEXT_PORT=${CODEX_CONTEXT_PORT:-7860}
Environment=CODEX_CONTEXT_EMBED_THREADS=${CODEX_CONTEXT_EMBED_THREADS:-2}
Environment=CODEX_CONTEXT_INDEX_BATCH=${CODEX_CONTEXT_INDEX_BATCH:-32}
Environment=CODEX_CONTEXT_INDEX_PAUSE=${CODEX_CONTEXT_INDEX_PAUSE:-0.08}
Environment=OMP_NUM_THREADS=${OMP_NUM_THREADS:-2}
Environment=OPENBLAS_NUM_THREADS=${OPENBLAS_NUM_THREADS:-1}
Environment=MKL_NUM_THREADS=${MKL_NUM_THREADS:-1}
Environment=NUMEXPR_NUM_THREADS=${NUMEXPR_NUM_THREADS:-1}
# This is a background desktop helper. Let interactive Codex/browser work win
# immediately when the machine is busy.
Nice=10
CPUWeight=20
ExecStart=$ROOT/.venv/bin/python -m codex_context.app
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
SERVICE

  systemctl --user daemon-reload
  systemctl --user enable --now codex-context.service
  echo "Автозапуск включён: codex-context.service"
  echo "Статус: systemctl --user status codex-context.service"
fi
