#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Нет .venv. Сначала запусти: bash setup.sh" >&2
  exit 1
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export CODEX_CONTEXT_EMBED_THREADS="${CODEX_CONTEXT_EMBED_THREADS:-2}"
export CODEX_CONTEXT_INDEX_BATCH="${CODEX_CONTEXT_INDEX_BATCH:-32}"
export CODEX_CONTEXT_INDEX_PAUSE="${CODEX_CONTEXT_INDEX_PAUSE:-0.08}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"

exec "$ROOT/.venv/bin/python" -m codex_context.app
