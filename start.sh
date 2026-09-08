#!/usr/bin/env bash
# Start TerminalGames. Any arguments are passed straight through to
# `terminalgames`, e.g. `./start.sh zero_day --new`.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -x .venv/bin/terminalgames ]; then
    echo "No virtual environment found. Run ./setup.sh first." >&2
    exit 1
fi

exec .venv/bin/terminalgames "$@"
