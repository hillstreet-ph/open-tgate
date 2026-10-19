#!/usr/bin/env bash
# App-focused Codex bootstrap; TDLib native compilation remains a separate CI gate.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/open-tgate"
MODE="${1:-install}"
case "$MODE" in install|check) ;; *) echo 'Usage: bash scripts/codex-setup.sh [install|check]' >&2; exit 2;; esac
if [[ "$MODE" == install ]]; then
  PYTHON_BIN="${PYTHON_BIN:-python3.13}"
  if command -v uv >/dev/null 2>&1; then
    uv venv --python "$PYTHON_BIN" --allow-existing .venv
    uv pip install --python .venv/bin/python -r requirements-dev.txt
  else
    "$PYTHON_BIN" -m venv .venv
    .venv/bin/python -m pip install -r requirements-dev.txt
  fi
  npm ci --prefix dashboard
  echo 'App dependencies installed. Native TDLib, provider access and persistent sessions are not verified.'
else
  [[ -x .venv/bin/python ]] || { echo 'Run install mode first' >&2; exit 1; }
  .venv/bin/ruff check app tests
  PYTHONPATH=. .venv/bin/python -m pytest -q
  npm test --prefix dashboard
  npm run check --prefix dashboard
fi
