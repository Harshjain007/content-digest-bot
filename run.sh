#!/usr/bin/env bash
# Run the knowledge-keeper bot as a single, clean instance.
#
# - activates the project venv
# - clears PYTHONPATH so the Hermes venv can't leak packages in
# - kills any already-running instance (Telegram allows only ONE getUpdates
#   per bot; a second copy causes a 409 Conflict and both die)
# - launches `python -m content_digest_bot.bot` (the package entry point)
#
# Usage:
#   ./run.sh            # foreground — Ctrl+C to stop
#   ./run.sh -d         # background (detached, logs to bot.log)
set -euo pipefail

cd "$(dirname "$0")"

# 1. Kill any existing bot instance so we never run two at once.
pkill -9 -f "content_digest_bot.bot" 2>/dev/null || true
sleep 1

# 2. Activate venv (create if missing).
if [ ! -d .venv ]; then
  echo "Creating venv…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# 3. Avoid Hermes venv package leakage.
unset PYTHONPATH

# 4. Make sure deps are present (cheap no-op if already installed).
python -m pip install -q -r requirements.txt 2>/dev/null || \
  echo "⚠️  pip install failed — bot may still run if deps are present."

if [ "${1:-}" = "-d" ]; then
  echo "Starting bot in background → bot.log"
  nohup python -m content_digest_bot.bot > bot.log 2>&1 &
  echo "PID $! — tail with: tail -f bot.log"
else
  echo "Starting bot (Ctrl+C to stop)…"
  exec python -m content_digest_bot.bot
fi
