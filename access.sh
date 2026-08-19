#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
PORT="${CODEX_CONTEXT_PORT:-7860}"
OVERRIDE_DIR="$HOME/.config/systemd/user/codex-context.service.d"
OVERRIDE_FILE="$OVERRIDE_DIR/network.conf"

case "$MODE" in
  lan)
    HOST="0.0.0.0"
    ;;
  local)
    HOST="127.0.0.1"
    ;;
  *)
    echo "Использование: ./access.sh lan | ./access.sh local" >&2
    exit 2
    ;;
esac

mkdir -p "$OVERRIDE_DIR"
cat > "$OVERRIDE_FILE" <<EOF
[Service]
Environment=CODEX_CONTEXT_HOST=$HOST
EOF

systemctl --user daemon-reload
systemctl --user restart codex-context.service

if [[ "$MODE" == "local" ]]; then
  echo "Codex Context доступен только на этом ПК:"
  echo "  http://127.0.0.1:$PORT"
  exit 0
fi

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "LAN-доступ включён. Открывай с устройства в той же локальной сети:"
if [[ -n "$LAN_IP" ]]; then
  echo "  http://$LAN_IP:$PORT"
else
  echo "  http://<IP-ЭТОГО-ПК>:$PORT"
fi
echo
echo "Важно: встроенной авторизации сейчас нет. Используй LAN-режим только в доверенной сети."
echo "Вернуть режим только для этого ПК: ./access.sh local"
