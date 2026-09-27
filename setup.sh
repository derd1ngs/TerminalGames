#!/usr/bin/env bash
# One-time setup: create the virtual environment and install TerminalGames
# into it. Safe to re-run -- it reuses an existing .venv and just
# reinstalls the package.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# A venv hardcodes its own absolute path (script shebangs, the editable
# install's finder), so one created before the project directory was moved
# or renamed is silently broken -- recreate it instead of reusing it.
if [ -d .venv ] && ! grep -qF "$PWD/.venv" .venv/pyvenv.cfg 2>/dev/null; then
    echo "Existing .venv was created at a different path -- recreating it ..."
    rm -rf .venv
fi

if [ ! -d .venv ]; then
    echo "Creating virtual environment in .venv ..."
    python3 -m venv .venv
fi

echo "Installing TerminalGames ..."
.venv/bin/pip install --upgrade pip --quiet
# The distribution was renamed terminalgames -> terminalgames-hacker (1.0.0).
# Drop the old one first: both install the `terminalgames` command, and
# uninstalling the old name later would take the command with it.
.venv/bin/pip uninstall --yes --quiet terminalgames 2>/dev/null || true
.venv/bin/pip install -e .

echo
echo "Setup complete. Run ./start.sh to play."
