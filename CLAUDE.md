# TerminalGames -- notes for Claude

- A `.venv` created before the project directory was moved/renamed is broken
  (`.venv/bin/pytest: bad interpreter`, `No module named 'terminalgames'`
  outside the repo): it hardcodes its absolute path. `./setup.sh` detects
  this and recreates it; then `.venv/bin/pip install -e ".[test,lint]"` for
  the dev tools.
- CI (`.github/workflows/tests.yml`) runs pytest plus `ruff check .`,
  `ruff format --check .` and `mypy terminalgames` -- run all four before
  calling a change done.
