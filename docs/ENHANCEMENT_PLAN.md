# Enhancement plan

Status as of 2026-09-26. Phases 0, 1, 3, 4 and 5 are done; Phase 2 was
skipped. Each phase is sized to be one PR and leaves the test
suite and CI green.

## Phase 0 -- bug fixes and the session refactor (done)

- `systemctl` service state now lives in `GameState.running_services`, so a
  fixed service no longer reports `failed` after save/continue.
- Commands are parsed with `shlex` (quoted arguments, `parse error:` on an
  unclosed quote); `help <command>` prints usage.
- TUI: Up/Down history, Tab completion (logic in `engine/shell.py:complete`,
  reusable by any frontend), `Ctrl+S` saves from any scene.
- `setup.sh` recreates a `.venv` that was created at another path.
- `engine/session.py:GameSession` owns the game loop; `tui.py` only renders.

## Phase 1 -- finish decoupling the engine from Textual (done)

Delivered as planned. `engine/loader.py` takes the saves root as a
parameter, and `GameSession.open(story, story_dir, slot_path, fresh=...)`
replaces the start/continue logic in `main()`. `main.new_or_continue` now
only makes the interactive fresh-or-continue decision. The engine turned
out to need only PyYAML (not even Rich), and
`tests/test_engine_is_ui_free.py` guards that. The original plan follows.

**Why:** `main.py` holds the story-loading and save-path helpers
(`discover_stories`, `load_network`, `load_npc_roster`, `save_slot_path`,
...) but also imports `tui` and therefore Textual. `tools/check_story.py`
imports those helpers from `main.py`, and so would a browser build -- which
can't install Textual.

1. Move the loading/save-path helpers into a new `engine/loader.py`. Make
   the saves root a parameter (default: today's `SAVES_DIR`), since in the
   browser it will be a virtual-FS path such as `/saves`.
   `main.py` and `check_story.py` import from there; `main.py` keeps only
   the CLI and pre-flight picker.
2. Add `GameSession.open(story_dir, slot, saves_root, new: bool)`: a
   classmethod that loads the story, network and NPCs, loads or creates the
   state, and materializes the sandbox (today split across `main.py`).
3. Port `tests/test_playthrough_zero_day.py` from its hand-copied
   `choose`/`run_terminal` loop onto `GameSession`, so the only copy of the
   loop is the real one.

**Verify:** `python -c "import terminalgames.engine.loader,
terminalgames.engine.session"` with Textual *uninstalled* in a scratch venv;
full suite green.

## Phase 2 -- quick browser preview via `textual serve` (skipped)

Skipped in favour of going straight to Phase 3.


**Goal:** play the existing TUI in a browser on the LAN, with no new frontend.

1. Add an optional extra `web = ["textual-serve"]` and a tiny
   `terminalgames/serve.py` (`python -m terminalgames.serve --port 8000`)
   that serves `terminalgames <story> --slot <slot>` through `textual-serve`.
2. It must pass a story and slot on the command line: the plain-console
   pre-flight picker in `main.py` runs *before* the Textual app starts and
   won't render through textual-serve.
3. README: a "Play in a browser (local)" section.

**Limits (acceptable for a preview):** every browser tab is a separate
process sharing the same `saves/` directory, so two tabs on one slot will
clobber each other. It needs a Python process running, and it is not
suitable for public hosting (every visitor runs a process on the host).

## Phase 3 -- static browser version on GitHub Pages (done)

Delivered, with these deviations from the plan below:

- **No Rich:** no shipped story uses markup, and the engine doesn't need
  Rich, so `web_bridge.markup_to_html` converts the basic tags (style words
  become CSS classes). Scene paragraphs are reflowed for the pane, and
  `backtick` spans render as code.
- **A zip instead of a wheel:** `web/build.py` zips only `engine/`,
  `stories/` and `web_bridge.py`, and Pyodide unpacks it. There's no
  package-data configuration and no micropip dependency resolution. The
  zip's hash is stamped into `app.js` for cache-busting.
- **Pyodide 314.0.7** (Python 3.14; Pyodide's new version scheme), loaded
  from jsdelivr.
- **Save download/import is deferred** to a follow-up. The menu notes that
  saves live in this browser only.
- **Testing:** the bridge is tested under CPython (`tests/test_web_bridge.py`,
  `tests/test_web_build.py`), and was smoke-tested in real Pyodide under
  Node plus an end-to-end run in headless Firefox (Playwright). The browser
  run isn't in CI yet.

The original plan follows.

**Goal:** `https://derd1ngs.github.io/TerminalGames/`: nothing to install,
no server, saves kept in the browser. The **unchanged Python engine** runs
client-side in Pyodide (CPython compiled to WebAssembly). The engine needs
only `pyyaml` (bundled with Pyodide) and `rich` (pure Python, for markup);
Textual is not used.

### 3.1 Python bridge -- `terminalgames/web/bridge.py`

A small, JSON-in/JSON-out facade over `GameSession`, unit-tested under
normal CPython:

| function | returns |
|---|---|
| `list_stories()` | `[{id, title}]` |
| `list_slots(story_id)` | `[{slot, summary}]` |
| `start(story_id, slot, new)` | view |
| `choose(index)` | view |
| `command(raw)` | view (plus `output`) |
| `complete(line)` | `[candidates]` |
| `save()` | `{ok}` |
| `compose_mail(to, subject, body)` | view (writes a draft, runs `mail sync`) |

A *view* is `{scene_type, story_html, choices, prompt, notices, ended}`.
Scene text is Rich markup (`[bold]...[/bold]`), so the bridge renders it to
HTML with `rich` (`Console(record=True).export_html(inline_styles=True)`),
and the browser never parses markup.

### 3.2 Frontend -- `web/index.html`, `web/app.js`, `web/style.css`

- The layout mirrors the TUI: the story pane and the choices sit left, the
  terminal sits right. Below about 700px wide the panes stack vertically.
- The terminal is a plain `<pre>` log plus an `<input>`, **not xterm.js**.
  The game is line-based, and xterm.js would mean reimplementing line
  editing while hurting mobile keyboards and copy/paste. Up/Down history and
  Tab completion call `bridge.complete()`, the same logic as the TUI.
- A loading screen covers the first Pyodide download (about 10 MB, cached
  afterwards).
- The story and slot pickers are simple HTML lists (they replace the
  console pre-flight picker).
- A mail compose dialog replaces "edit a draft file outside the game": the
  TUI's `mail sync` flow depends on a real text editor, which the browser
  doesn't have. It still goes through `outbox_match`, so the story content
  is unchanged.
- There are `Ctrl+S` and `Save` buttons. Autosave runs on chapter crossings
  exactly as in the TUI (it comes from `GameSession`).

### 3.3 Persistence

Mount Pyodide's IDBFS at `/saves`. Pass `/saves` as the saves root (Phase 1
makes it a parameter), so slot JSON *and* each slot's sandbox directory
persist in IndexedDB. Call `FS.syncfs(false)` after every command and
choice. It's cheap: the files are tiny.

### 3.4 Packaging and deployment

- At build time: `python -m build --wheel`. In the browser:
  `pyodide.loadPackage("pyyaml")`, then
  `micropip.install("rich")`, then
  `micropip.install(<our wheel>, deps=False)` (so it doesn't try to pull
  Textual). Story YAML ships inside the wheel as package data -- check the
  `pyproject.toml` package-data config covers `stories/**/*.yaml`.
- Load Pyodide from `cdn.jsdelivr.net`, pinned to a specific version.
- New workflow `.github/workflows/pages.yml`: on a push to `main`, build the
  wheel, copy `web/` + the wheel into `_site/`, then run
  `actions/upload-pages-artifact` and `actions/deploy-pages`. **One-time
  manual step:** repo Settings -> Pages -> Source: "GitHub Actions".

### 3.5 Tests

- `tests/test_web_bridge.py` (CPython): a full Zero Day playthrough through
  the bridge's JSON API, completion, compose-mail matching and bouncing,
  and markup-to-HTML rendering.
- Optional later step: a Playwright smoke test in CI that loads the built
  page and plays the first terminal scene.

**Risks:** Pyodide's first load time (mitigated by the loading screen and
caching); IndexedDB can be cleared by the browser, so offer a "download
save" / "import save" pair of buttons (slot JSON + sandbox as one zip).

## Phase 4 -- gameplay and engine

All items are done; item 7 is the one-chapter story *Dead Drop*
(`story_02_dead_drop`). `ssh` asks for the password on the next line, the way
real ssh does, rather than taking it as an argument; frontends mask that line.
A host with `logins` refuses `connect`.

Independent of each other; any order.

1. **`requires` combinators:** `all: [...]`, `any: [...]`, `not: {...}` in
   `check_requires`. They're recursive and backwards compatible.
   `check_story` gets them for free because it calls the real
   `check_requires`. This lets branching endings use fewer throwaway flags.
2. **`status` command:** current chapter/scene, connected host, the most
   recent `lead` journal entry. Helps a player who returns to a save after
   a week.
3. **`journal <category>` filter** (`lead | trace | suspect | note`), with
   completion.
4. **Use the unused `check_command_order` validator:** a terminal-block
   option `ordered_commands: [...]` for "recovery procedure" puzzles.
5. **Credentials puzzle:** `ssh <user>@<host>` with a password found in a
   file or mail. That means a `credentials:` host key in `network.yaml`, and
   `check_story` must know it can set `on_connect_flag`.
6. **Simple pipes:** `cmd | grep <pattern>` (only `grep` as a pipe target).
   This is a small change in `TerminalRunner.execute`.
7. **Second story** (short, 1 chapter): proves the engine generalizes and
   gives the browser build a story picker that's worth having.

## Phase 5 -- tooling (done)

Both items delivered in `tools/check_story.py`. The lint's problems fail
the check (and CI via `test_story_reachability`), while unread flags are
warnings only.
- **Mermaid only:** `--graph` outputs Mermaid, not Graphviz, because GitHub
  renders it natively; the output was verified with Mermaid 12.
- **Tools lint:** building the lint showed that `requires: {tool: ...}`
  can never pass, because no story content can grant a tool. The lint
  therefore reports any tool requirement as a problem. Adding a way to
  grant tools (e.g. a `tools:` effect) would be a small follow-up feature.
- **Unread flags:** neither shipped story has one.

The original plan follows.

1. `check_story`: report flags that are set but never read, flags that are
   read but never set (outside `win_flag`), and `journal_has` ids no scene
   or topic ever logs.
2. `check_story --graph`: emit a Graphviz/Mermaid scene graph for authors.

## Suggested order

Phase 1 -> Phase 2 (a quick win, same day) -> Phase 3 -> Phases 4/5 as
wanted. Phases 4 and 5 don't depend on 2 or 3.

## Decisions needed from you

1. **Phase 2 at all?** Skip it if you only care about the public static
   build.
2. **Browser save portability:** is the "download/import save" button in
   scope for Phase 3's first version, or a follow-up?
3. **Pages URL:** the default `derd1ngs.github.io/TerminalGames`, or a
   custom domain?
