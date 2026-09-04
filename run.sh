#!/usr/bin/env bash
# Run the knowledge-keeper bot as a single, clean instance.
#
# - uses the project venv's python directly (no `source activate`, which can
#   resolve the wrong VIRTUAL_ENV path after the project is moved)
# - clears PYTHONPATH so the Hermes venv can't leak packages in
# - kills any already-running instance (Telegram allows only ONE getUpdates
#   per bot; a second copy causes a 409 Conflict and both die)
# - launches `python -m content_digest_bot.bot` (the package entry point)
#
# Usage:
#   ./run.sh            # foreground — Ctrl+C to stop
#   ./run.sh -d         # background (detached, logs to bot.log)
set -euo pipefail

# Resolve this script's directory regardless of how it's invoked.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_PY="$SCRIPT_DIR/.venv/bin/python"

# 1. Kill any existing bot instance so we never run two at once.
pkill -9 -f "content_digest_bot.bot" 2>/dev/null || true
sleep 1

# 2. Ensure the venv exists; create + install deps if missing.
if [ ! -x "$VENV_PY" ]; then
  echo "Creating venv…"
  python3 -m venv "$SCRIPT_DIR/.venv"
fi
# Make sure deps are present (cheap no-op if already installed).
"$VENV_PY" -m pip install -q -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null || \
  echo "⚠️  pip install failed — bot may still run if deps are present."

# 3. Avoid Hermes venv package leakage.
unset PYTHONPATH

# 4. Launch.
if [ "${1:-}" = "-d" ]; then
  echo "Starting bot in background → bot.log"
  nohup "$VENV_PY" -m content_digest_bot.bot > bot.log 2>&1 &
  echo "PID $! — tail with: tail -f bot.log"
else
  echo "Starting bot (Ctrl+C to stop)…"
  exec "$VENV_PY" -m content_digest_bot.bot
fi
