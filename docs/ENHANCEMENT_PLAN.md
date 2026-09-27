# Enhancement plan

Status as of 2026-09-27. Phases 0, 1, 3, 4 and 5 are done; Phase 2 was
skipped. Phases 6-9 are done (Side Channel 1.0.x is on PyPI), Phase 10 is
done, Phase 11 is done, Phases 12-13 are proposals, and Phase 14 (the
Seven Hats series) is planned. Each phase is
sized to be one PR and leaves the test suite and CI green.

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
- **Save download/import** came as a follow-up: each slot exports to one
  JSON file (state plus sandbox; `engine/savefile.py`), which can be
  imported in any browser.
- **Testing:** the bridge is tested under CPython (`tests/test_web_bridge.py`,
  `tests/test_web_build.py`), and was smoke-tested in real Pyodide under
  Node plus an end-to-end run in headless Firefox (Playwright). That run was
  later committed as `web/e2e/e2e.mjs` and runs in CI as the `web-e2e` job.

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

## Phase 4 -- gameplay and engine (done)

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
  could never pass, because no story content could grant a tool. A
  follow-up added `sets: {tool.<id>: true}` (the same convention as
  `trust.<npc>`), and the lint now reports only tools that nothing grants.
- **Unread flags:** neither shipped story has one.

The original plan follows.

1. `check_story`: report flags that are set but never read, flags that are
   read but never set (outside `win_flag`), and `journal_has` ids no scene
   or topic ever logs.
2. `check_story --graph`: emit a Graphviz/Mermaid scene graph for authors.

## Phase 6 -- robustness (done)

From a second analysis of the finished project; each item was reproduced
first. Delivered as planned, with one correction: clamping trust on *both*
sides turned out to be unsound (trust 1 and 2 aren't interchangeable if it
can still rise to 3). Trust is therefore clamped at the top only when
nothing lowers it, and at the bottom only when nothing raises it. A new
`package` CI job installs the built wheel into a clean venv and runs it
outside the repo.

1. **Packaging**: a built wheel contains *no stories* (0 YAML files), so an
   installed `terminalgames` finds nothing, and saves would land next to the
   package directory, i.e. inside site-packages. Declare the stories as
   package data and move saves to the per-user data directory
   (`platformdirs`, already installed via Textual); keep a repo checkout
   using `saves/` so existing local saves stay put.
2. **Strict story loading**: unknown keys (`requries:`) are silently
   ignored, and invalid values (journal category `tracee`, scene type) load
   without complaint. Reject them at load time, suggesting the closest valid
   key.
3. **Finite checker search**: a loop that keeps raising trust makes every
   pass a new state, so the search stops at its cap with no useful message.
   Treat trust above the highest threshold any `trust_at_least` checks as
   equivalent.
4. **Stale saves**: continuing a save whose scene a later story edit
   removed raises a raw `StoryLoadError` (a traceback in the TUI, "internal
   error" in the browser). Say so plainly and offer to restart the slot.

## Phase 7 -- player experience (done)

1. **Mobile input** (done): phone keyboards have no Tab or arrow keys, so ⇥ ↑ ↓
   buttons next to the input act like those keys on touch or narrow screens,
   without taking focus (which would close the keyboard). Adding them
   exposed that the phone grid column couldn't shrink below its content;
   it's now `minmax(0, 1fr)`.
2. **`hint`** (done): a terminal block's `hints` list, revealed one at a time
   and saved per scene. Both stories have hints for every terminal scene, and
   a test solves each scene using only its hints' commands.
3. **Endings gallery** (done): endings found per story across all slots,
   recorded by `GameSession` on entering an ending and kept in
   `found-endings.txt` next to the slots. It's shown at each ending, in the
   TUI's slot listing, and in the browser's slot menu (undiscovered endings
   stay "???").
4. **Mail compose in the TUI** (done): `mail compose` opens a form (To,
   Subject, message) in the TUI, and the browser's Mail dialog on the same
   command. Both frontends send through `GameSession.compose_mail`, which
   writes a real draft and runs `mail sync`. The form uses compact widgets
   so it fits an 80x24 terminal.
5. **Offline browser play** (done): `web/sw.js` caches the game per build
   and Pyodide once (its URL is versioned). The page reports which Pyodide
   files it loaded, so even the first visit ends offline-ready. The build id
   now hashes every shipped file, not just the Python zip; otherwise a
   web-only change would never replace an installed worker. The e2e test
   proves the worker, not the HTTP cache, serves the reload (checked against
   a control build that caches nothing).

## Phase 8 -- content and engine (done)

1. **Night Shift** (`story_03_night_shift`), the third story, uses tool grants
   (the deploy key gates a host; Dex's sniffer gates an ending) and the trace
   meter (limit 6, one retry). Its tests found a real bug: an `ssh` password
   line was counted by the trace meter. The **trace meter** itself: a terminal block's `trace: {limit,
   on_trace}` counts commands that touch a host, while local ones are free.
2. More shell commands (done): `head`/`tail`, `find`, `ls -a` (plain `ls` now
   hides dotfiles), `history`, `clear`, `man <cmd>`, with a man page for
   every command.

## Phase 9 -- distribution (done)

**The game is now "Side Channel".** `terminalgames` clashes with PyPI's
existing `terminal-games`, and the owner chose a distinct name over a
suffix. The PyPI distribution, the command and the GitHub repo are
`sidechannel`, and the site moves to `derd1ngs.github.io/sidechannel/`. The
import package kept its original name, `terminalgames`, for 1.0.0; right
after the release it was renamed to `sidechannel` too. Version 1.0.0, with
no TestPyPI run.

Done:
- pyproject metadata: README, license file, classifiers, URLs;
- the version is single-sourced from `terminalgames/__init__.py`;
- `CHANGELOG.md`;
- `.github/workflows/release.yml`: a `vX.Y.Z` tag builds and checks, installs
  the wheel into a clean venv and runs it, publishes via trusted publishing
  (environment `pypi`), then creates a GitHub release;
- `setup.sh` drops the old distributions, so a stale command never lingers;
- the renamed player-facing names, the command and all URLs.

License: `GPL-3.0-or-later` (the owner's decision), as the SPDX `license`
expression in pyproject and a README section.

**Released 2026-09-27:** the repo was renamed to `sidechannel` (the site is
now https://derd1ngs.github.io/sidechannel/), the tag `v1.0.0` ran
`release.yml`, and `sidechannel` 1.0.0 is on PyPI
(https://pypi.org/project/sidechannel/) via trusted publishing, with a
matching GitHub release. It was verified by installing from PyPI into a
clean venv and running it.

## Phase 10 -- story selection (done; item 4 left for later)

From a third analysis after 1.0.1. The story lists showed titles only (the
TUI even showed folder names), with no dates and no pagination.

1. **Story metadata:** optional `published` (an ISO date) and `description`
   (one line) in `manifest.yaml`, validated strictly. Dates from git history:
   Zero Day 2026-09-08, Dead Drop and Night Shift 2026-09-26.
2. **Story cards:** the title, "Published 8 Sep 2026", the description,
   chapter and ending counts, and endings found. Sorted newest first.
3. **Pagination:** in the browser, ~6 per page with Prev/Next and "Stories
   1-6 of N", hidden while everything fits; in the TUI picker, `n`/`p` pages,
   titles instead of folder names.
4. Later, with many stories: a search/filter box.

## Phase 11 -- robustness II (done)

1. **A save format version** (done): saves carry `save_version` (1).
   `state.migrate` upgrades older ones, and a save without a version is 0,
   from <= 1.0.1. A save from a *newer* game is refused with an "update the
   game" message:
   - the slot list shows it instead of crashing;
   - the TUI exits without offering a restart, which would overwrite it;
   - the browser and save import show the error.
2. **Save when an ending is reached** (done), so a slot reflects a finished
   run.

## Phase 12 -- more stories

1. **User stories:** a stories folder in the user data directory (TUI),
   checked with `check_story` on load; loading a story zip in the browser.
   This is what makes Phase 10's pagination matter.
2. **Story delivery independent of the game version**, needed because new
   stories don't bump the game version (owner decision): published stories
   are downloadable into the user stories folder (e.g. `sidechannel stories
   update`), each with its own `version` in its manifest. The browser needs
   none of this, since it deploys from `main`.
3. `sidechannel new-story <id>`: a skeleton story that passes the checker.
4. **An author guide:** split the authoring half of the 513-line README
   into `docs/authoring.md`.

## Phase 13 -- polish

1. Browser settings: text size and a light theme, remembered per browser.
2. Load progress: a percentage for the first ~10 MB download.
3. Optional: a German interface and stories.

## Phase 14 -- "The Seven Hats" (a story series; storyline agreed)

The storyline is in **`docs/seven-hats/STORYLINE.md`**, the series' "story
bible". Owner decisions (2026-09-27):
- One long scenario in which the hats join at different times.
- The black hat is in it, and **starts long before the others**: hired at
  day -120, an audition job, then months of research.
- **Release order C**, the attacker first: Part 1 Black, then Blue, White,
  Gray, Green, Red, and Purple last.
- The company is **Brackwater Terminals**; the draft's "Brackwater Terminals" is
  a real company. All other names stay.
- **Each story is released separately.**
- Vesper's three endings (caught, escaped, cliffhanger), with the
  cliffhanger kept as a hook for a sequel.
- **New stories don't change the game's version number.**

The notes below are the original proposal; where they differ, the
storyline document wins.

Inspired by the seven hacker "hats"
(https://www.softwaresecured.com/post/the-7-hats-of-hacking):

- **White:** authorized, stays in scope.
- **Black:** malicious, no authorization.
- **Gray:** probes without permission but discloses.
- **Green:** a learning beginner.
- **Red:** a vigilante who hacks back.
- **Blue:** an invited pre-release tester.
- **Purple:** a self-taught home-lab tinkerer.

**Concept: one incident, seven points of view** (a "Rashomon" structure).
Each story stands alone, but each also shows traces of the others, so
together they reveal the whole picture. The fictional setting is Meridian
Freight, a port-logistics company launching a customer portal, "TrackNet".

| Hat | Story idea | What it teaches |
|---|---|---|
| Blue | Invited to test TrackNet before launch, on a deadline. Finds a flaw; the report gets deprioritized. | Time-boxed, authorized testing (an engagement clock) |
| White | A signed pentest of Brackwater's internal network. The tempting TrackNet production box is *out of scope*. | Permission and scope: staying in bounds is the win condition |
| Gray | An independent researcher stumbles on the same flaw, now live. Report it, publish it, or sit on it? | Responsible disclosure and legal risk, with branching endings |
| Black | The attacker who exploits it. Endings show the consequences; the logs from the other stories are what catch them. | No clean wins; consequences |
| Red | A vigilante tracks the attacker and hacks back, and hits a compromised innocent machine. | Why hack-back is a problem |
| Green | A new hire in Brackwater's training lab: a gentle tutorial that turns up the attacker's traces. | Onboarding, like Zero Day |
| Purple | Rebuilds the TrackNet flaw in a home lab to understand it: a short, open-ended epilogue. | Learning by building |

**Cross-story links:** each story's logs, mail and files contain artefacts
of the others, e.g. the gray hat's probe in the white hat's logs, the blue
hat's buried report leaking in the gray hat's story, the attacker's servers
turning up in the red hat's. Stories stay independent in code (no shared
save state); the connection is narrative.

**Engine work first (one PR):**
1. **Series metadata** in the manifest: `series: "The Seven Hats"`,
   `part: 3`, `hat: gray`. Story cards show "The Seven Hats · Part 3 of 7 ·
   Gray Hat", and a series sorts by part instead of by date.
2. **Scope:** a terminal block's `scope: [hosts]` with `on_scope_violation:
   <scene>`. `scan`/`connect`/`ssh` against a host outside the scope sends
   the story there (or logs a violation that later gates endings), like the
   trace meter. This is the article's core distinction, permission and
   scope, as a mechanic.
3. **An engagement clock:** the trace meter with a label, e.g. `trace: {limit:
   12, label: "hours left"}`, so a deadline doesn't read as "trace".

**Content guidelines:** fictional companies, hosts and people only, and no
real-world exploit techniques (the shell is simulated anyway).
Unauthorized hats' stories show legal and personal consequences; a black or
red hat can't "win" cleanly. Each story states its hat's rules in-world.

**Suggested order:** the engine PR; then Blue + White (the authorized pair,
using scope and the clock); then Gray + Black (the flaw goes public); then
Red, Green and Purple.

**Decisions for the owner:**
1. One shared incident (above), or seven independent stories?
2. Include a black-hat point of view (consequence-focused), or leave it out?
3. Which hats first? The suggestion is Blue + White.
4. The setting name ("Brackwater Terminals" / "TrackNet" are placeholders).

## Suggested order (Phases 6-9)

Phase 6 as one PR first (item 1 is a real bug; 2 and 3 catch authoring
mistakes early), then Phase 7 item 1 (the site is public), then the rest as
wanted.

## Decisions (resolved)

1. Phase 2 was skipped in favour of Phase 3.
2. Save download/import shipped as a follow-up to Phase 3.
3. The site used the default URL `derd1ngs.github.io/TerminalGames/`, and
   moved to `derd1ngs.github.io/sidechannel/` with the rename to Side Channel.
