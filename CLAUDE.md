# TerminalGames -- notes for Claude

- A `.venv` created before the project directory was moved/renamed is broken
  (`.venv/bin/pytest: bad interpreter`, `No module named 'terminalgames'`
  outside the repo): it hardcodes its absolute path. `./setup.sh` detects
  this and recreates it; then `.venv/bin/pip install -e ".[test,lint]"` for
  the dev tools.
- CI (`.github/workflows/tests.yml`) runs pytest plus `ruff check .`,
  `ruff format --check .` and `mypy terminalgames` -- run all four before
  calling a change done.
- `main` is branch-protected and requires PR branches to be up to date: when
  a PR is "BEHIND" (e.g. after merging the lower PR of a stack), run
  `gh api -X PUT repos/derd1ngs/TerminalGames/pulls/<N>/update-branch`, wait
  for CI, then `gh pr merge <N> --merge`.
- `gh pr edit` fails on this machine's gh 2.45 ("Projects (classic) is being
  deprecated"); change a PR's base with
  `gh api -X PATCH repos/derd1ngs/TerminalGames/pulls/<N> -f base=main`.
- That gh also has no `--json` on `gh pr checks`, and its tab-separated
  output breaks `awk '{print $2}'` on names like `test (3.10)`: read results
  with `gh pr checks <N> | cut -f1,2`.
- `gh run view <run> --log` can come back empty; fetch a job's log via REST
  instead: `gh api repos/derd1ngs/TerminalGames/actions/jobs/<job-id>/logs`
  (job ids: `gh api .../actions/runs/<run>/jobs -q '.jobs[] | .name, .id'`).
- `terminalgames/engine/` and `web_bridge.py` must stay importable with only
  PyYAML (the browser build runs them in Pyodide) -- enforced by
  `tests/test_engine_is_ui_free.py`.
- Browser build: to check a change, run `python web/build.py`, then
  `python -m http.server -d _site 8765`, then `web/e2e/e2e.mjs` (see README;
  CI's `web-e2e` job runs the same script). It uses Playwright's Firefox
  because there's no Chrome on this machine. When
  running Pyodide under Node, pass `unpackArchive` a
  `new Uint8Array(buf).buffer` -- a Node `Buffer` fails with "Unknown typed
  array type".
- The editable install from `setup.sh` hides packaging mistakes: any new
  non-`.py` file under `terminalgames/` (story YAML, data) must be listed in
  `[tool.setuptools.package-data]` in `pyproject.toml`, or a real install
  silently lacks it. CI's `package` job (wheel -> clean venv -> run outside
  the repo) catches this.
- TUI tests: `App.run_test()` defaults to an 80x24 screen, so modals and
  forms must fit it (a click outside raises `OutOfBounds`). A pane that a
  scene change hid renders no lines, so assert on recorded
  `log_terminal`/`log_text` calls rather than reading that pane.
- To look at the TUI: `app.save_screenshot(filename=..., path=...)` inside
  `run_test(size=(80, 24))` writes an SVG; Playwright's Firefox can open it
  via `file://` and screenshot it to PNG.
- Offline e2e in Playwright's Firefox: `context.setOffline(true)` is
  unusable -- Firefox either serves its HTTP cache (the test passes even with
  a broken service worker) or refuses the reload (`NS_ERROR_OFFLINE`). Cut
  the network with `context.route("**/*", r => r.abort())` instead: routing
  also disables the HTTP cache, so only the service worker can serve.
